import asyncio
import hashlib
import logging
import re
from dataclasses import asdict, dataclass
from time import monotonic

from .config import Device
from .errors import OperationError
from .media import Media, decode_clip
from .profiles import GENERIC, identify
from .serial_at import AT, Reply
from .sms import PHONE, decode_deliver, encode_submit
from .store import Store

logger = logging.getLogger(__name__)
ICCID = re.compile(r"89[0-9]{16,20}")
PIN = re.compile(r"[0-9]{4,8}")
RETRIES = re.compile(r"[^,]+,[^,]*,\s*\d+,\s*(\d+),\s*\d+,\s*\d+")
AUDIO_FORMAT = re.compile(r"0,\s*8000,\s*16,\s*20(?:,.*)?")
READ_AT = re.compile(
    r"AT(\+C(?:GMI|GMM|GMR|SQ|OPS\?|REG\?|GREG\?|EREG\?|GATT\?|GACT\?|PMS\?|LCC)|"
    r"\^(?:CVOICE|SYSINFO|SYSCFG)\?|I)?"
)
CALL_STATES = ("active", "held", "dialing", "ringing", "incoming", "waiting")
NETWORK_POLL = 10
SMS_POLL = 25


@dataclass
class Call:
    state: str
    direction: str
    peer: str


def parse_calls(reply: Reply) -> list[Call]:
    result = []
    for line in reply.lines:
        if not line.startswith("+CLCC:"):
            continue
        items = line.partition(":")[2].split(",")
        if len(items) < 5 or items[1].strip() not in {"0", "1"} or items[3].strip() != "0":
            continue
        state = items[2].strip()
        if not (state.isdigit() and int(state) < len(CALL_STATES)):
            continue
        peer = items[5].strip().strip('"') if len(items) > 5 else ""
        result.append(
            Call(
                CALL_STATES[int(state)], "incoming" if items[1].strip() == "1" else "outgoing", peer
            )
        )
    return result


class Modem:
    def __init__(self, device: Device, store: Store, publish, register):
        self.device = device
        self.store = store
        self.publish = publish
        self.register = register
        self.at: AT | None = None
        self.media: Media | None = None
        self.profile = GENERIC
        self.lock = asyncio.Lock()
        self.state = {"id": device.id, "connected": False, "calls": [], "capabilities": {}}
        self.sim_key: str | None = None
        self.sms_due = False
        self.reconnect = False
        self.failed_pins: set[str] = set()
        self.network_at = 0.0
        self.sms_at = 0.0
        self.call_at = 0.0
        self.dial_pending_until = 0.0
        self.ring = False
        self.sms_storages: set[str] = set()
        self.configured_ready = False

    @property
    def id(self) -> str:
        return self.state["id"]

    def put(self, **fields):
        if all(self.state.get(key) == value for key, value in fields.items()):
            return
        self.state = {**self.state, **fields}
        self.publish("modem", self.state)

    def audio_changed(self):
        self.put(
            audio=bool(self.media and self.media.active),
            playing=bool(self.media and self.media.active and self.media.clip),
            microphone=bool(self.media and self.media.active and self.media.mic_owner),
        )

    def urc(self, line):
        if line.startswith(("+CMTI:", "+CDSI:")):
            self.sms_due = True
        if line == "RING" or line.startswith(("+CRING:", "+CLIP:")):
            self.ring = True
        if line.startswith(("^CEND:", "^CONN:")) or line == "NO CARRIER":
            self.call_at = 0

    async def command(self, command, timeout=5, payload=None):
        if self.at is None or not self.at.port.is_open:
            raise OperationError("Modem is offline", "OFFLINE", 503)
        try:
            return await self.at.command(command, timeout, payload)
        except (OSError, TimeoutError) as error:
            self.reconnect = True
            raise OperationError("Modem did not complete the command", "MODEM_IO", 503) from error

    async def require(self, command, message, timeout=5, payload=None):
        reply = await self.command(command, timeout, payload)
        if not reply.ok:
            raise OperationError(message, "MODEM_REJECTED")
        return reply

    async def connect(self):
        self.at = AT(self.device.control, self.urc)
        self.reconnect = False
        self.configured_ready = False
        self.call_at = 0
        self.dial_pending_until = 0
        self.ring = False
        self.sms_at = 0
        self.network_at = 0
        await self.require("AT", "No modem response")
        await self.require("ATE0", "Could not disable AT echo")
        await self.command("AT+CMEE=2")
        fields = {}
        for key, cmd, prefix in (
            ("manufacturer", "AT+CGMI", "+CGMI:"),
            ("model", "AT+CGMM", "+CGMM:"),
            ("firmware", "AT+CGMR", "+CGMR:"),
            ("imei", "AT+CGSN", "+CGSN:"),
        ):
            reply = await self.command(cmd)
            fields[key] = (reply.field(prefix) or next(iter(reply.lines), "")) if reply.ok else ""
        self.profile = identify(fields["manufacturer"], fields["model"])
        self.register(self, fields["imei"])
        self.put(connected=True, profile=self.profile.name, **fields)
        await self.refresh_sim(auto=True)

    async def get_iccid(self) -> str:
        for command in self.profile.iccid_commands:
            reply = await self.command(command)
            for line in reply.lines:
                candidate = line.split(":")[-1].strip().strip('"').upper().removesuffix("F")
                if ICCID.fullmatch(candidate):
                    return candidate
        reply = await self.command("AT+CRSM=176,12258,0,0,10")
        match = re.fullmatch(r'144,\s*0,\s*"([0-9A-Fa-f]{20})"', reply.field("+CRSM:"))
        if not match:
            return ""
        value = match[1]
        decoded = "".join(value[i : i + 2][::-1] for i in range(0, 20, 2))
        decoded = decoded.upper().removesuffix("F")
        return decoded if ICCID.fullmatch(decoded) else ""

    async def retries(self) -> int | None:
        if self.profile.pin_counter:
            reply = await self.command(self.profile.pin_counter)
            match = RETRIES.fullmatch(reply.field("^CPIN:"))
            return int(match[1]) if match else None
        reply = await self.command('AT+CPINR="SIM PIN"')
        match = re.fullmatch(r'"?SIM PIN"?,\s*(\d+)(?:,\s*\d+)?', reply.field("+CPINR:"))
        return int(match[1]) if match else None

    async def refresh_sim(self, auto=False):
        state = (await self.command("AT+CPIN?")).field("+CPIN:") or "UNKNOWN"
        if state != self.state.get("sim") or auto:
            iccid = await self.get_iccid() if state in {"READY", "SIM PIN"} else ""
            self.sim_key = hashlib.sha256(iccid.encode()).hexdigest() if iccid else None
            self.put(
                sim=state,
                iccid=iccid or None,
                pinSaved=bool(self.sim_key and self.store.get_pin(self.sim_key)),
            )
        if state == "SIM PIN" and self.sim_key and self.sim_key not in self.failed_pins:
            pin = self.store.get_pin(self.sim_key)
            if auto and pin:
                try:
                    await self.unlock(pin, remember=True)
                except OperationError:
                    logger.warning("Saved SIM PIN could not be used", extra={"modem": self.id})
        if state != "READY":
            self.configured_ready = False
            return
        if not self.configured_ready:
            await self.probe()

    async def unlock(self, pin: str, remember: bool):
        if not PIN.fullmatch(pin):
            raise OperationError("PIN must contain 4 to 8 digits", "INVALID_PIN", 422)
        status = (await self.command("AT+CPIN?")).field("+CPIN:")
        if status == "SIM PIN":
            remaining = await self.retries()
            if remaining is None or remaining < 2:
                raise OperationError("At least two remaining PIN attempts must be verified")
            key = self.sim_key
            if key:
                self.failed_pins.add(key)
            reply = await self.command(f'AT+CPIN="{pin}"', 10)
            if not reply.ok:
                if key:
                    self.store.forget_pin(key)
                    self.put(pinSaved=False)
                raise OperationError("SIM rejected PIN. No retry was made.", "PIN_REJECTED")
            for _ in range(8):
                status = (await self.command("AT+CPIN?")).field("+CPIN:")
                if status == "READY":
                    break
                await asyncio.sleep(0.5)
        elif status == "READY":
            raise OperationError("SIM already unlocked. Enter and save its PIN when locked.")
        if status != "READY":
            raise OperationError("SIM did not become ready")
        if self.sim_key:
            self.failed_pins.discard(self.sim_key)
        self.put(sim="READY")
        iccid = await self.get_iccid()
        self.sim_key = hashlib.sha256(iccid.encode()).hexdigest() if iccid else None
        if remember and self.sim_key:
            self.store.save_pin(self.sim_key, pin)
        self.put(
            iccid=iccid or None, pinSaved=bool(self.sim_key and self.store.get_pin(self.sim_key))
        )
        self.configured_ready = False
        await self.probe()
        return self.state

    async def probe(self):
        sms = (await self.command("AT+CMGF=0")).ok
        stores = await self.command("AT+CPMS=?") if sms else Reply([], "ERROR")
        fields = stores.field("+CPMS:").split(")", 1)[0]
        self.sms_storages = set(re.findall(r'"(SM|ME|MT)"', fields)) or {"SM"}
        sms = sms and stores.ok
        notifications = (await self.command("AT+CNMI=2,1,0,0,0")).ok if sms else False
        call_status = (await self.command("AT+CLCC")).ok
        voice = "unknown"
        audio = False
        if self.profile.audio_probe:
            reply = await self.command(self.profile.audio_probe)
            audio = bool(
                reply.ok and AUDIO_FORMAT.fullmatch(reply.field("^CVOICE:")) and self.device.audio
            )
            voice = (
                "supported"
                if reply.ok and reply.field("^CVOICE:").startswith("0,")
                else "unsupported"
            )
        self.put(
            capabilities={
                "sms": sms,
                "smsNotifications": notifications,
                "callStatus": call_status,
                "voice": voice,
                "audio": audio,
            }
        )
        self.configured_ready = True
        self.sms_due = sms

    async def network(self):
        fields = {}
        for key, cmd, prefix in (
            ("signal", "AT+CSQ", "+CSQ:"),
            ("operator", "AT+COPS?", "+COPS:"),
            ("registration", "AT+CREG?", "+CREG:"),
            ("packetAttached", "AT+CGATT?", "+CGATT:"),
        ):
            fields[key] = (await self.command(cmd)).field(prefix) or None
        self.put(**fields)
        self.network_at = monotonic() + NETWORK_POLL

    async def sms_scan(self):
        if not self.state["capabilities"].get("sms"):
            return
        for storage in sorted(self.sms_storages):
            if not (await self.command(f'AT+CPMS="{storage}"')).ok:
                continue
            response = await self.command("AT+CMGL=4", 15)
            if not response.ok:
                continue
            for head, line in zip(response.lines, response.lines[1:]):
                if not head.startswith("+CMGL:") or not re.fullmatch(r"[0-9A-Fa-f]{10,512}", line):
                    continue
                raw = bytes.fromhex(line)
                try:
                    sms = decode_deliver(raw)
                except (ValueError, IndexError):
                    logger.debug("Skipped non-deliver or malformed SMS PDU")
                    continue
                scope = self.sim_key or self.id
                if sms.part is not None:
                    message = self.store.add_sms_part(
                        self.id, scope, sms.sender, sms.body, sms.part, raw
                    )
                else:
                    message = self.store.add_message(
                        self.id, "incoming", sms.sender, sms.body, "received", scope.encode() + raw
                    )
                if message:
                    self.publish("message", message)
        self.sms_at = monotonic() + SMS_POLL
        self.sms_due = False

    async def send(self, number: str, text: str):
        if not self.state["capabilities"].get("sms"):
            raise OperationError("SMS is unavailable")
        try:
            payload = encode_submit(number, text)
        except ValueError as error:
            raise OperationError(str(error), "INVALID_MESSAGE", 422) from error
        if self.state["calls"]:
            raise OperationError("SMS sending is paused during calls")
        message = self.store.add_message(self.id, "outgoing", number, text, "sending")
        self.publish("message", message)
        try:
            response = await self.command(
                f"AT+CMGS={len(payload) - 1}", 45, payload.hex().upper().encode()
            )
        except (OperationError, asyncio.CancelledError):
            self.publish("message", self.store.update_message(message["id"], "unknown"))
            raise
        result = self.store.update_message(message["id"], "submitted" if response.ok else "failed")
        self.publish("message", result)
        return result

    async def calls(self):
        if not self.state["capabilities"].get("callStatus"):
            return []
        reply = await self.command("AT+CLCC")
        if not reply.ok:
            return self.state["calls"]
        calls = [asdict(call) for call in parse_calls(reply)]
        if not calls and monotonic() < self.dial_pending_until:
            self.call_at = monotonic() + 0.3
            return self.state["calls"]
        self.dial_pending_until = 0
        self.put(calls=calls)
        if not calls and self.media:
            self.media.close()
            self.media = None
        elif any(call["state"] == "active" for call in calls):
            if self.media is None and self.ring:
                await self.prepare_audio()
            if self.media:
                self.media.active = self.media.port.is_open
        elif self.media:
            self.media.active = False
        self.audio_changed()
        self.ring = False
        self.call_at = monotonic() + (0.3 if calls else 1.4)
        return calls

    async def prepare_audio(self):
        if not self.state["capabilities"].get("audio") or not self.device.audio:
            return
        try:
            self.media = Media(self.device.audio, self.audio_changed)
            await self.require(self.profile.audio_setup, "Modem rejected USB voice routing")
        except (OperationError, OSError):
            if self.media:
                self.media.close()
                self.media = None
            self.put(mediaError="USB call audio could not be opened")

    async def dial(self, number: str):
        if not PHONE.fullmatch(number):
            raise OperationError("Invalid phone number", "INVALID_NUMBER", 422)
        if self.state["capabilities"].get("voice") == "unsupported" or not self.state[
            "capabilities"
        ].get("callStatus"):
            raise OperationError("Voice calling is unavailable")
        if await self.calls():
            raise OperationError("Line already in use")
        await self.require(f"ATD{number};", "Modem rejected the call", 12)
        self.dial_pending_until = monotonic() + 3
        self.put(calls=[asdict(Call("dialing", "outgoing", number))], mediaError=None)
        await self.prepare_audio()
        self.call_at = 0
        return self.state

    async def answer(self):
        if not any(call["state"] == "incoming" for call in await self.calls()):
            raise OperationError("No incoming call")
        await self.require("ATA", "Modem rejected answer", 12)
        await self.prepare_audio()
        await self.calls()
        return self.state

    async def hangup(self):
        if not self.state["calls"] and not await self.calls():
            return self.state
        await self.require("ATH", "Modem rejected hangup")
        self.dial_pending_until = 0
        await self.calls()
        return self.state

    def media_required(self):
        if not self.media or not self.media.active:
            raise OperationError("Active call audio is unavailable")
        return self.media

    def play(self, clip: str):
        media = self.media_required()
        try:
            media.clip = decode_clip(clip)
        except ValueError as error:
            raise OperationError(str(error), "INVALID_AUDIO", 422) from error
        media.clear_mic()
        self.audio_changed()
        return self.state

    def stop_playback(self):
        self.media_required().clip = b""
        self.audio_changed()
        return self.state

    async def raw(self, command: str, raw_enabled: bool):
        if (
            not 2 <= len(command) <= 128
            or not command.startswith("AT")
            or not command.isascii()
            or not command.isprintable()
            or any(char in command for char in (";", "\x7f"))
        ):
            raise OperationError("Expected a single AT command", "INVALID_AT", 422)
        if re.search(r"\b(?:CPIN|CLCK|CPWD|CRSM|CSIM)\b", command, re.IGNORECASE):
            raise OperationError("Use the dedicated SIM controls", "AT_FORBIDDEN", 403)
        if not READ_AT.fullmatch(command) and not raw_enabled:
            raise OperationError("Command outside the read-only allowlist", "AT_FORBIDDEN", 403)
        if self.state["calls"] and not READ_AT.fullmatch(command):
            raise OperationError("Raw writes are disabled during calls")
        reply = await self.command(command)
        return {"lines": reply.lines, "terminal": reply.terminal}

    async def run(self):
        try:
            while True:
                try:
                    async with self.lock:
                        if self.reconnect or self.at is None:
                            self.disconnect()
                            await self.connect()
                        await self.at.idle()
                        now = monotonic()
                        if now >= self.call_at or self.ring:
                            await self.calls()
                        if now >= self.network_at:
                            await self.refresh_sim()
                            if self.state["sim"] == "READY":
                                await self.network()
                            else:
                                self.network_at = now + NETWORK_POLL
                        if (
                            not self.state["calls"]
                            and self.configured_ready
                            and (self.sms_due or now >= self.sms_at)
                        ):
                            await self.sms_scan()
                except (OperationError, OSError, ValueError) as error:
                    self.disconnect()
                    logger.debug(
                        "Modem polling failed",
                        extra={"modem": self.id, "kind": type(error).__name__},
                    )
                    await asyncio.sleep(2)
                await asyncio.sleep(0.08)
        finally:
            self.disconnect()

    def disconnect(self):
        if self.media:
            self.media.close()
            self.media = None
        if self.at:
            self.at.close()
            self.at = None
        self.put(connected=False, calls=[], audio=False, playing=False, microphone=False)

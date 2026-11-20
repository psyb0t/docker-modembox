import asyncio
import fcntl
import re
import termios
from dataclasses import dataclass

import serial

BAUD = 115200
POLL_SECONDS = 0.015
DEFAULT_TIMEOUT = 5
MAX_BUFFER = 256 * 1024
TERMINAL = re.compile(
    r"^(OK|ERROR|NO CARRIER|BUSY|NO ANSWER|NO DIALTONE|"
    r"COMMAND NOT SUPPORT(?:ED)?|\+CM[ES] ERROR:.*)$"
)
UNSOLICITED = ("RING", "+CRING:", "+CMTI:", "+CDSI:", "+CLIP:", "^BOOT:", "^CONN:", "^CEND:")


@dataclass
class Reply:
    lines: list[str]
    terminal: str

    @property
    def ok(self) -> bool:
        return self.terminal == "OK"

    def field(self, prefix: str) -> str:
        return next(
            (line.removeprefix(prefix).strip() for line in self.lines if line.startswith(prefix)),
            "",
        )


class USBModemPort(serial.Serial):
    # Some USB interfaces, including the E173 control port, reject manual
    # control-line ioctls. Keep termios RTS/CTS available for audio.
    def _update_dtr_state(self):
        return None

    def _update_rts_state(self):
        return None


def open_port(path: str, *, audio: bool = False) -> serial.Serial:
    port = USBModemPort(path, BAUD, timeout=0, write_timeout=0.25, exclusive=True, rtscts=audio)
    try:
        fcntl.ioctl(port.fileno(), termios.TIOCEXCL)
    except OSError:
        port.close()
        raise
    return port


def close_port(port: serial.Serial):
    try:
        if port.is_open:
            fcntl.ioctl(port.fileno(), termios.TIOCNXCL)
    except OSError:
        # Disconnected USB devices lose their kernel port state.
        port.close()
        return
    port.close()


class AT:
    def __init__(self, path, notify):
        self.port = open_port(path)
        self.notify = notify
        self.lock = asyncio.Lock()
        self.buffer = bytearray()

    def close(self):
        close_port(self.port)

    def read(self):
        self.buffer.extend(self.port.read(4096))
        if len(self.buffer) > MAX_BUFFER:
            self.close()
            raise serial.SerialException("AT receive limit exceeded")

    def line(self):
        separator = self.buffer.find(b"\n")
        if separator < 0:
            return None
        value = bytes(self.buffer[:separator]).decode("ascii", "replace").strip()
        del self.buffer[: separator + 1]
        return value

    async def idle(self):
        if self.lock.locked():
            return
        self.read()
        while (line := self.line()) is not None:
            if line:
                self.notify(line)

    async def command(self, command: str, timeout=DEFAULT_TIMEOUT, payload: bytes | None = None):
        async with self.lock:
            if not self.port.is_open:
                raise serial.SerialException("AT port closed")
            try:
                await self.idle_before_command()
                async with asyncio.timeout(timeout):
                    self.port.write(command.encode("ascii") + b"\r")
                    return await self.receive(command, payload)
            except (TimeoutError, serial.SerialException, asyncio.CancelledError):
                self.close()
                raise

    async def idle_before_command(self):
        self.read()
        while (line := self.line()) is not None:
            if line:
                self.notify(line)

    async def receive(self, command, payload):
        lines = []
        while True:
            self.read()
            while self.buffer:
                if payload is not None and self.buffer.lstrip(b"\r\n ").startswith(b">"):
                    index = self.buffer.index(b">")
                    del self.buffer[: index + 1]
                    self.port.write(payload + b"\x1a")
                    payload = None
                    continue
                line = self.line()
                if line is None:
                    break
                if not line or line == command or line == command.split("=", 1)[0]:
                    continue
                if line == "NO CARRIER" and not command.startswith(
                    ("ATD", "ATA", "ATH", "AT+CHUP")
                ):
                    self.notify(line)
                    continue
                if TERMINAL.fullmatch(line):
                    return Reply(lines, line)
                if line.startswith(UNSOLICITED):
                    self.notify(line)
                    continue
                lines.append(line)
            if sum(map(len, lines)) > MAX_BUFFER:
                raise serial.SerialException("AT reply limit exceeded")
            await asyncio.sleep(POLL_SECONDS)

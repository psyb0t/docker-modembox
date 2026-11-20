import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .config import Device

HUAWEI_IDS = {"12d1:1001"}
HUAWEI_PCM_MODELS = {"E173", "E169", "E169G"}
IMEI = re.compile(r"(?P<equipment>[0-9]{14})[0-9]{1,2}")
IMEI_NAMESPACE = "urn:modembox:imei:"


def equipment_id(imei: str) -> str | None:
    match = IMEI.fullmatch(imei.strip().strip('"'))
    if not match or len(set(match["equipment"])) == 1:
        return None
    return str(uuid5(NAMESPACE_URL, IMEI_NAMESPACE + match["equipment"]))


@dataclass(frozen=True)
class Profile:
    name: str
    audio_setup: str | None = None
    audio_probe: str | None = None
    pin_counter: str | None = None
    iccid_commands: tuple[str, ...] = ("AT+CCID",)


HUAWEI = Profile("huawei", None, None, "AT^CPIN?", ("AT^ICCID?", "AT+CCID"))
HUAWEI_PCM = Profile(
    "huawei-serial-pcm", "AT^DDSETEX=2", "AT^CVOICE?", "AT^CPIN?", HUAWEI.iccid_commands
)
GENERIC = Profile("3gpp-at")


def identify(manufacturer: str, model: str) -> Profile:
    if "huawei" in manufacturer.casefold():
        return HUAWEI_PCM if model.upper() in HUAWEI_PCM_MODELS else HUAWEI
    return GENERIC


def discover(
    tty_root: Path = Path("/sys/class/tty"), device_root: Path = Path("/dev")
) -> list[Device]:
    """Select control and audio interfaces only for a known USB layout."""
    groups: dict[str, dict[str, str]] = {}
    stable = {
        link.resolve(): str(link)
        for link in sorted((device_root / "serial/by-path").glob("*"))
        if link.exists()
    }
    for tty in sorted(tty_root.glob("ttyUSB*")):
        node = device_root / tty.name
        if not node.exists():
            continue
        parents = (tty / "device").resolve().parents
        interface = next((item for item in parents if (item / "bInterfaceNumber").is_file()), None)
        usb = next((item for item in parents if (item / "idVendor").is_file()), None)
        if interface is None or usb is None:
            continue
        try:
            vendor = (usb / "idVendor").read_text(encoding="ascii").strip()
            product = (usb / "idProduct").read_text(encoding="ascii").strip()
            number = (interface / "bInterfaceNumber").read_text(encoding="ascii").strip()
        except OSError:
            # A modem may disappear between enumeration and reading its descriptors.
            continue
        if f"{vendor}:{product}" in HUAWEI_IDS:
            groups.setdefault(str(usb), {})[number] = stable.get(node.resolve(), str(node))
    return [
        Device(
            "usb-" + hashlib.sha256(base.encode()).hexdigest()[:12], ports["02"], ports.get("01")
        )
        for base, ports in groups.items()
        if "02" in ports
    ]

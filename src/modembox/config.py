import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEVICE_PATH = re.compile(r"/dev/[a-zA-Z0-9_.:/-]+")
DEVICE_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")


@dataclass(frozen=True)
class Device:
    id: str
    control: str
    audio: str | None = None


@dataclass(frozen=True)
class Config:
    token: str
    data: Path
    web: Path
    devices: tuple[Device, ...] = ()
    raw_at: bool = False
    disable_auth: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("MODEMBOX_TOKEN", "")
        disable_auth = os.environ.get("MODEMBOX_DISABLE_AUTH", "false")
        if disable_auth not in {"true", "false"}:
            raise ValueError("MODEMBOX_DISABLE_AUTH must be true or false")
        if disable_auth == "true":
            token = ""
        elif not token:
            raise ValueError("MODEMBOX_TOKEN is required unless MODEMBOX_DISABLE_AUTH=true")
        elif not token.isascii() or not 16 <= len(token) <= 256 or token.strip() != token:
            raise ValueError("MODEMBOX_TOKEN must contain 16 to 256 ASCII characters")
        devices = json.loads(os.environ.get("MODEMBOX_DEVICES", "[]"))
        if not isinstance(devices, list) or len(devices) > 32:
            raise ValueError("MODEMBOX_DEVICES must be an array of up to 32 devices")
        configured = tuple(Device(**device) for device in devices)
        paths = [path for d in configured for path in (d.control, d.audio) if path]
        if (
            any(not DEVICE_ID.fullmatch(d.id) for d in configured)
            or len({d.id for d in configured}) != len(configured)
            or len({os.path.realpath(path) for path in paths}) != len(paths)
            or any(not DEVICE_PATH.fullmatch(p) or ".." in Path(p).parts for p in paths)
        ):
            raise ValueError("Device IDs and /dev paths must be valid and unique")
        raw_at = os.environ.get("MODEMBOX_ALLOW_RAW_AT", "false")
        if raw_at not in {"true", "false"}:
            raise ValueError("MODEMBOX_ALLOW_RAW_AT must be true or false")
        return cls(
            token,
            Path(os.environ.get("MODEMBOX_DATA_DIR", ".data")),
            Path(os.environ.get("MODEMBOX_WEB_DIR", "web/build")),
            configured,
            raw_at == "true",
            disable_auth == "true",
        )

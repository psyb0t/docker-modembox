import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

GSM7 = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ "
    "!\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "ÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSIONS = {
    10: "\f",
    20: "^",
    40: "{",
    41: "}",
    47: "\\",
    60: "[",
    61: "~",
    62: "]",
    64: "|",
    101: "€",
}
PHONE = re.compile(r"\+?[0-9]{3,20}")
CONCAT_REFERENCE_BYTES = {0x00: 1, 0x08: 2}


class SmsPart(NamedTuple):
    reference: str
    total: int
    sequence: int
    coding: int
    timestamp: int


class Deliver(NamedTuple):
    sender: str
    body: str
    stamp: bytes
    part: SmsPart | None


def address(raw: bytes, digits: int, toa: int) -> str:
    if toa & 0x70 == 0x50:
        return septets(raw, digits * 4 // 7)
    number = "".join(f"{value & 15:x}{value >> 4:x}" for value in raw)[:digits]
    return ("+" if toa & 0x70 == 0x10 else "") + number


def septets(raw: bytes, count: int, skip: int = 0) -> str:
    packed = int.from_bytes(raw, "little")
    values = [(packed >> (index * 7)) & 127 for index in range(skip, count)]
    result = []
    escaped = False
    for value in values:
        if escaped:
            result.append(GSM7_EXTENSIONS.get(value, "�"))
            escaped = False
        elif value == 27:
            escaped = True
        else:
            result.append(GSM7[value])
    return "".join(result)


def service_center_time(stamp: bytes) -> int:
    digits = "".join(f"{value & 15:x}{value >> 4:x}" for value in stamp[:-1])
    zone = stamp[-1]
    offset = timedelta(minutes=15 * int(f"{zone & 7:x}{zone >> 4:x}"))
    date = datetime.strptime(digits, "%y%m%d%H%M%S")
    return int(date.replace(tzinfo=timezone(-offset if zone & 8 else offset)).timestamp())


def concatenation(header: bytes, coding: int, stamp: bytes) -> SmsPart | None:
    part = None
    while header:
        kind, length = header[:2]
        payload = header[2 : 2 + length]
        if len(payload) != length:
            raise ValueError("Truncated SMS user data element")
        header = header[2 + length :]
        width = CONCAT_REFERENCE_BYTES.get(kind)
        if width is None:
            continue
        if part or length != width + 2:
            raise ValueError("Invalid SMS concatenation header")
        total, sequence = payload[-2:]
        if not 1 <= sequence <= total:
            raise ValueError("Invalid SMS segment number")
        part = SmsPart(payload[:width].hex(), total, sequence, coding, service_center_time(stamp))
    return part if part and part.total > 1 else None


def decode_deliver(pdu: bytes) -> Deliver:
    start = pdu[0] + 1
    flags, digits, toa = pdu[start : start + 3]
    if flags & 3:
        raise ValueError("PDU is not SMS-DELIVER")
    end_address = start + 3 + (digits + 1) // 2
    sender = address(pdu[start + 3 : end_address], digits, toa)
    coding = pdu[end_address + 1]
    stamp = pdu[end_address + 2 : end_address + 9]
    length = pdu[end_address + 9]
    data = pdu[end_address + 10 :]
    data_bytes = length if (coding & 0x0C) in (4, 8) else (length * 7 + 7) // 8
    header_bytes = data[0] + 1 if flags & 0x40 else 0
    if len(data) < data_bytes or header_bytes > data_bytes:
        raise ValueError("Truncated SMS user data")
    part = concatenation(data[1:header_bytes], coding, stamp) if header_bytes else None
    if coding & 0x0C == 8:
        body = data[header_bytes:length].decode("utf-16-be", errors="replace")
    elif coding & 0x0C == 4:
        body = "[binary SMS] " + data[header_bytes:length].hex()
    else:
        body = septets(data, length, (header_bytes * 8 + 6) // 7)
    return Deliver(sender, body, stamp, part)


def encode_submit(number: str, text: str) -> bytes:
    if not PHONE.fullmatch(number):
        raise ValueError("Invalid phone number")
    digits = number.lstrip("+")
    padded = digits + ("f" if len(digits) % 2 else "")
    encoded = bytes.fromhex("".join(padded[i : i + 2][::-1] for i in range(0, len(padded), 2)))
    payload = text.encode("utf-16-be")
    if not 1 <= len(payload) <= 140:
        raise ValueError("Message must fit one SMS (70 UTF-16 units)")
    return (
        bytes((0, 1, 0, len(digits), 145 if number.startswith("+") else 129))
        + encoded
        + bytes((0, 8, len(payload)))
        + payload
    )

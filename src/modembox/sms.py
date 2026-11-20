import re

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


def decode_deliver(pdu: bytes):
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
    header_bytes = data[0] + 1 if flags & 0x40 else 0
    if coding & 0x0C == 8:
        body = data[header_bytes:length].decode("utf-16-be", errors="replace")
    elif coding & 0x0C == 4:
        body = "[binary SMS] " + data[header_bytes:length].hex()
    else:
        body = septets(data, length, (header_bytes * 8 + 6) // 7)
    return sender, body, stamp


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

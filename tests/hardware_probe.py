import asyncio
import json

from modembox.serial_at import AT


async def main():
    modem = AT("/dev/test-modem", lambda _line: None)
    try:
        checked = {}
        for name, command in (
            ("model", "AT+CGMM"),
            ("firmware", "AT+CGMR"),
            ("sim", "AT+CPIN?"),
            ("registration", "AT+CREG?"),
            ("voice", "AT^CVOICE?"),
        ):
            reply = await modem.command(command, timeout=8)
            checked[name] = {"lines": reply.lines, "result": reply.terminal}
        assert checked["model"]["result"] == "OK" and checked["model"]["lines"], checked
        print(json.dumps(checked))
    finally:
        modem.close()


if __name__ == "__main__":
    asyncio.run(main())

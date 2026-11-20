import asyncio
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tempfile import TemporaryDirectory

from modembox.__main__ import JsonFormatter
from modembox.config import Config, Device
from modembox.errors import OperationError
from modembox.modem import PIN
from modembox.service import Service
from modembox.sms import encode_submit

DEVICE = Device("sms-test", "/dev/test-modem")
STARTUP_TIMEOUT = 60
RECEIVE_TIMEOUT = 300
logger = logging.getLogger(__name__)


async def wait_for_inbox(gateway: Service, pin: str):
    attempted_unlock = False
    async with asyncio.timeout(STARTUP_TIMEOUT):
        while True:
            modem = gateway.modems.get(DEVICE.id)
            if modem and modem.state["connected"]:
                sim = modem.state.get("sim")
                if sim == "SIM PIN":
                    if not pin or attempted_unlock:
                        raise ValueError("Test SIM is locked. Check MODEMBOX_TEST_PIN.")
                    attempted_unlock = True
                    await gateway.act(DEVICE.id, "test", "unlock", pin=pin, remember=False)
                elif sim == "READY" and modem.sms_at:
                    return
            await asyncio.sleep(0.2)


async def receive(number: str, body: str, prompt: str, pin: str, data: Path):
    gateway = Service(Config("EXAMPLE-DO-NOT-USE-sms-receive", data, Path("web/build"), (DEVICE,)))
    async with gateway.lifecycle():
        try:
            await wait_for_inbox(gateway, pin)
        except TimeoutError as error:
            raise ValueError("Modem inbox did not become ready within 60 seconds.") from error

        events = asyncio.Queue()
        gateway.clients.add(events)
        try:
            sent = await gateway.act(DEVICE.id, "test", "send_sms", number=number, text=prompt)
            if sent["status"] != "submitted":
                raise ValueError("The modem rejected the SMS invitation.")
            print("SMS invitation submitted. Waiting up to five minutes for the reply.", flush=True)
            async with asyncio.timeout(RECEIVE_TIMEOUT):
                while True:
                    event = await events.get()
                    if event["type"] != "message":
                        continue
                    row = event["data"]
                    if (
                        row["direction"] == "incoming"
                        and row["peer"] == number
                        and row["body"] == body
                        and row["status"] == "received"
                        and gateway.store.message(row["id"]) == row
                    ):
                        print("PASS. The expected SMS reached the inbox.")
                        return
        except TimeoutError as error:
            raise ValueError("No matching SMS arrived within five minutes.") from error
        finally:
            gateway.clients.remove(events)


def main():
    number = os.environ.get("MODEMBOX_TEST_NUMBER", "")
    pin = os.environ.get("MODEMBOX_TEST_PIN", "")
    body = os.environ.get("MODEMBOX_TEST_SMS_TEXT", "TEST1")
    with TemporaryDirectory() as directory:
        data = Path(directory)
        handlers = [
            logging.StreamHandler(),
            RotatingFileHandler(data / "receive.log", maxBytes=1_048_576, backupCount=1),
        ]
        for handler in handlers:
            handler.setFormatter(JsonFormatter())
        logging.basicConfig(level=logging.INFO, handlers=handlers)
        try:
            if not re.fullmatch(r"\+[1-9][0-9]{7,14}", number):
                raise ValueError(
                    "Set MODEMBOX_TEST_NUMBER to an international number in .env.test."
                )
            if pin and not PIN.fullmatch(pin):
                raise ValueError("MODEMBOX_TEST_PIN must contain 4 to 8 digits.")
            if not body.strip():
                raise ValueError("MODEMBOX_TEST_SMS_TEXT must contain a reply.")
            prompt = f"ModemBox SMS test is running. Reply with: {body}"
            try:
                encode_submit(number, prompt)
            except ValueError as error:
                raise ValueError(
                    "The SMS invitation and reply text must fit in one SMS."
                ) from error
            asyncio.run(receive(number, body, prompt, pin, data))
        except (ValueError, OperationError) as error:
            logger.error("%s", error)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

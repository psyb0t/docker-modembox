import tempfile
from pathlib import Path

import uvicorn

from modembox.config import Config, Device
from modembox.server import create_app
from modembox.service import Service
from test_gateway import TOKEN, Emulator


def main():
    device = Emulator()
    device.unlocked = True
    try:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                TOKEN, Path(directory), Path("/app/web/build"), (Device("demo", device.path),)
            )
            service = Service(config)
            service.store.add_message(
                "demo", "incoming", "+12025550104", "Example received text", "received"
            )
            service.store.add_message(
                "demo", "outgoing", "+12025550104", "Example sent text", "submitted"
            )
            uvicorn.run(
                create_app(config, service=service), host="127.0.0.1", port=8000, access_log=False
            )
    finally:
        device.close()


if __name__ == "__main__":
    main()

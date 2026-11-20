import asyncio
from collections import deque
from contextlib import asynccontextmanager
from time import time

from .config import Config
from .errors import OperationError
from .modem import Modem
from .profiles import discover
from .store import Store


class Service:
    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config.data)
        self.modems: dict[str, Modem] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.clients: set[asyncio.Queue] = set()
        self.activity = deque(maxlen=60)
        self.sequence = 0

    def snapshot(self):
        return {
            "type": "snapshot",
            "seq": self.sequence,
            "data": {
                "modems": [modem.state for modem in self.modems.values()],
                "messages": self.store.messages(),
                "activity": list(reversed(self.activity)),
            },
        }

    def publish(self, kind, payload):
        self.sequence += 1
        event = {"type": kind, "seq": self.sequence, "data": payload}
        if kind == "activity":
            self.activity.append({"seq": self.sequence, **payload})
        for client in self.clients:
            if client.full():
                while not client.empty():
                    client.get_nowait()
                client.put_nowait(self.snapshot())
            else:
                client.put_nowait(event)

    def modem(self, device_id: str):
        modem = self.modems.get(device_id)
        if modem is None:
            raise OperationError("Modem not found", "NOT_FOUND", 404)
        return modem

    async def act(self, device_id: str, source: str, operation: str, **params):
        device = self.modem(device_id)
        async with device.lock:
            try:
                if not device.state["connected"]:
                    raise OperationError("Modem is offline", "OFFLINE", 503)
                match operation:
                    case "unlock":
                        result = await device.unlock(params["pin"], params["remember"])
                    case "send_sms":
                        result = await device.send(params["number"], params["text"])
                    case "dial":
                        result = await device.dial(params["number"])
                    case "answer":
                        result = await device.answer()
                    case "hangup":
                        result = await device.hangup()
                    case "play_audio":
                        result = device.play(params["wav"])
                    case "stop_audio":
                        result = device.stop_playback()
                    case "at":
                        result = await device.raw(params["command"], self.config.raw_at)
                    case "forget_pin":
                        if device.sim_key:
                            self.store.forget_pin(device.sim_key)
                            device.put(pinSaved=False)
                        result = device.state
                    case _:
                        raise OperationError("Unknown operation", "NOT_FOUND", 404)
            except OperationError:
                self.record(source, device_id, operation, "failed")
                raise
            self.record(source, device_id, operation, "ok")
            return result

    def record(self, source, device_id, operation, outcome):
        self.publish(
            "activity",
            {
                "source": source,
                "modem": device_id,
                "action": operation,
                "outcome": outcome,
                "time": time(),
            },
        )

    async def scan(self):
        while True:
            present = {device.id: device for device in self.config.devices or discover()}
            for device_id, worker in list(self.tasks.items()):
                if present.get(device_id) == self.modems[device_id].device:
                    continue
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                del self.tasks[device_id]
            for device_id, device in present.items():
                if device_id in self.tasks:
                    continue
                modem = Modem(device, self.store, self.publish)
                previous = self.modems.get(device_id)
                if previous:
                    modem.failed_pins.update(previous.failed_pins)
                self.modems[device_id] = modem
                self.publish("modem", modem.state)
                self.tasks[device_id] = asyncio.create_task(modem.run())
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifecycle(self):
        task = asyncio.create_task(self.scan())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            for worker in self.tasks.values():
                worker.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.store.db.close()

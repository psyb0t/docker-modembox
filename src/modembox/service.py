import asyncio
from collections import deque
from contextlib import asynccontextmanager
from time import time

from .config import Config
from .errors import OperationError
from .hooks import Hooks
from .modem import Modem
from .profiles import discover, equipment_id
from .store import Store


class Service:
    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config.data)
        self.modems: dict[str, Modem] = {}
        self.workers: dict[str, tuple[Modem, asyncio.Task]] = {}
        self.clients: set[asyncio.Queue] = set()
        self.activity = deque(maxlen=60)
        self.sequence = 0
        self.hooks = Hooks(self.store, self.publish, self.act)
        self.store.on_sms = self.hooks.sms_received

    def snapshot(self):
        return {
            "type": "snapshot",
            "seq": self.sequence,
            "data": {
                "modems": [modem.state for modem in self.modems.values()],
                "messages": self.store.messages(),
                "activity": list(reversed(self.activity)),
                "hooks": self.hooks.snapshot(),
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
        if kind == "message" and payload["direction"] == "incoming":
            self.hooks.publish_pending(f"sms:{payload['id']}")

    def modem(self, device_id: str):
        modem = self.modems.get(device_id)
        if modem is None:
            raise OperationError("Modem not found", "NOT_FOUND", 404)
        return modem

    async def act(self, device_id: str, source: str, operation: str, **params):
        if source == "hook" and operation == "send_sms" and device_id not in self.modems:
            raise OperationError("Modem is unavailable", "HOOK_DEFER", 503)
        device = self.modem(device_id)
        async with device.lock:
            try:
                if self.modems.get(device_id) is not device or not device.state["connected"]:
                    if source == "hook" and operation == "send_sms":
                        raise OperationError("Modem is offline", "HOOK_DEFER", 503)
                    raise OperationError("Modem is offline", "OFFLINE", 503)
                match operation:
                    case "unlock":
                        result = await device.unlock(params["pin"], params["remember"])
                    case "send_sms":
                        result = await device.send(
                            params["number"], params["text"], automated=source == "hook"
                        )
                    case "dial":
                        result = await device.dial(params["number"])
                    case "answer":
                        result = await device.answer()
                    case "hangup":
                        result = await device.hangup()
                    case "decline":
                        result = await device.decline(params["call_id"])
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
                if source != "hook" or operation != "send_sms":
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

    def register_modem(self, modem: Modem, imei: str):
        hardware_id = equipment_id(imei) if not self.config.devices else None
        modem_id = hardware_id or modem.device.id
        previous = self.modems.get(modem_id)
        if previous is modem:
            return
        if previous:
            if any(worker is previous for worker, _ in self.workers.values()):
                raise OperationError("Another modem already uses this equipment ID")
            modem.failed_pins.update(previous.failed_pins)
        old_id = modem.id
        remove_old = old_id != modem_id and self.modems.get(old_id) is modem
        if remove_old:
            del self.modems[old_id]
        modem.state = {"id": modem_id, "connected": False, "calls": [], "capabilities": {}}
        self.modems[modem_id] = modem
        if remove_old:
            self.publish("snapshot", self.snapshot()["data"])

    async def scan(self):
        while True:
            present = {device.id: device for device in self.config.devices or discover()}
            for device_id, (modem, worker) in list(self.workers.items()):
                if present.get(device_id) == modem.device:
                    continue
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                del self.workers[device_id]
            for device_id, device in present.items():
                if device_id in self.workers:
                    continue
                modem = Modem(
                    device, self.store, self.publish, self.register_modem, self.hooks.call_change
                )
                if self.config.devices:
                    self.register_modem(modem, "")
                    self.publish("modem", modem.state)
                self.workers[device_id] = (modem, asyncio.create_task(modem.run()))
            await asyncio.sleep(2)

    @asynccontextmanager
    async def lifecycle(self):
        self.hooks.recover_calls()
        tasks = [asyncio.create_task(self.scan())]
        tasks.extend(
            asyncio.create_task(self.hooks.worker(kind)) for kind in ("decline", "sms", "http")
        )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            workers = [worker for _, worker in self.workers.values()]
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            self.store.db.close()

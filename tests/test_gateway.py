import hashlib
import os
import pty
import select
import threading
import time
from pathlib import Path

import pytest
import serial
from fastapi.testclient import TestClient

from modembox.config import Config, Device
from modembox.media import decode_clip, encode_clip
from modembox.profiles import GENERIC, HUAWEI_PCM, discover, equipment_id, identify
from modembox.server import create_app
from modembox.serial_at import close_port, open_port
from modembox.service import Service
from modembox.sms import decode_deliver, encode_submit
from modembox.store import Store

TOKEN = "EXAMPLE-DO-NOT-USE-token"
HEADERS = {"Authorization": "Bearer " + TOKEN}
MCP_HEADERS = {**HEADERS, "Accept": "application/json, text/event-stream"}
DELIVER = "0000049121430008421010000000000400680069"


def multipart_pdu(text, part, total=4, reference="42", stamp="42101000000000"):
    header = bytes.fromhex("0003" + reference if len(reference) == 2 else "0804" + reference)
    header += bytes((total, part))
    payload = bytes((len(header),)) + header + text.encode("utf-16-be")
    return bytes.fromhex("0040049121430008" + stamp) + bytes((len(payload),)) + payload


class Emulator:
    def __init__(self):
        self.master, self.slave = pty.openpty()
        self.path = os.ttyname(self.slave)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.state = ""
        self.unlocked = False
        self.retries = 3
        self.iccid_fallback = False
        self.imei = "123456789012345"
        self.inbox = [bytes.fromhex(DELIVER)]
        self.commands = []
        self.thread.start()

    def response(self, command):
        if self.iccid_fallback and command == "AT^ICCID?":
            return b"\r\nERROR\r\n"
        if self.iccid_fallback and command == "AT+CCID":
            return b"\r\nCOMMAND NOT SUPPORT\r\n"
        outputs = {
            "AT+CGMI": "huawei",
            "AT+CGMM": "E173",
            "AT+CGMR": "EXAMPLE",
            "AT+CGSN": self.imei,
            "AT+CPIN?": "+CPIN: READY" if self.unlocked else "+CPIN: SIM PIN",
            "AT^CPIN?": f"^CPIN: SIM PIN,10,10,{self.retries},10,3",
            "AT^ICCID?": "^ICCID: 89123456789012345678",
            "AT+CRSM=176,12258,0,0,10": '+CRSM: 144,0,"982143658709214365F7"',
            "AT+CPMS=?": '+CPMS: ("SM"),("SM"),("SM")',
            "AT+CMGL=4": "\r\n".join(
                f"+CMGL: {slot},0,,{len(pdu) - 1}\r\n{pdu.hex()}"
                for slot, pdu in enumerate(self.inbox, 1)
            ),
            "AT+CSQ": "+CSQ: 23,99",
            "AT+COPS?": '+COPS: 0,2,"00101",0',
            "AT+CREG?": "+CREG: 0,1",
            "AT+CGATT?": "+CGATT: 0",
            "AT^CVOICE?": "^CVOICE: 0,8000,16,20",
            "AT+CLCC": self.state,
        }
        if command == 'AT+CPIN="1234"':
            self.unlocked = True
        elif command == "ATD+1234567890;":
            self.state = '+CLCC: 1,0,0,0,0,"+1234567890",145'
        elif command == "ATH":
            self.state = ""
        return ("\r\n" + outputs.get(command, "") + "\r\nOK\r\n").encode()

    def run(self):
        pending = b""
        awaiting_sms = False
        while not self.stop.is_set():
            if not select.select([self.master], [], [], 0.1)[0]:
                continue
            pending += os.read(self.master, 4096)
            while (separator := b"\x1a" if awaiting_sms else b"\r") in pending:
                command, pending = pending.split(separator, 1)
                text = command.decode().strip()
                if awaiting_sms:
                    self.sms_payload = bytes.fromhex(text)
                    os.write(self.master, b"\r\n+CMGS: 11\r\nOK\r\n")
                    awaiting_sms = False
                elif text.startswith("AT+CMGS="):
                    os.write(self.master, b"\r\n> ")
                    awaiting_sms = True
                elif text:
                    self.commands.append(text)
                    os.write(self.master, self.response(text))

    def close(self):
        self.stop.set()
        self.thread.join(2)
        os.close(self.master)
        os.close(self.slave)


@pytest.fixture
def emulator():
    value = Emulator()
    try:
        yield value
    finally:
        value.close()


def wait_for(client, condition):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        state = client.get("/v1/state", headers=HEADERS).json()
        if condition(state):
            return state
        time.sleep(0.04)
    pytest.fail("Expected modem state did not arrive")


def receive_until(ws, condition):
    for _ in range(40):
        event = ws.receive_json()
        if condition(event):
            return event
    pytest.fail("Expected event did not arrive")


def close_events(ws):
    ws.send_text("close")
    for _ in range(64):
        if ws.receive()["type"] == "websocket.close":
            return
    pytest.fail("Event stream did not close")


def test_multipart_inbox_repairs_legacy_rows_in_sequence(tmp_path, emulator):
    config = Config(TOKEN, tmp_path, Path("web/build"), (Device("usb", emulator.path),))
    segments = ["a" * 67, "b" * 67, "c" * 67, "end"]
    pdus = [multipart_pdu(body, part) for part, body in enumerate(segments, 1)]
    emulator.inbox = [pdus[3], pdus[1], pdus[0], pdus[2], pdus[1]]
    emulator.unlocked = True
    service = Service(config)
    sim_key = hashlib.sha256(b"89123456789012345678").hexdigest().encode()
    legacy_ids = [
        service.store.add_message("usb", "incoming", "+1234", body, "received", sim_key + pdu)["id"]
        for body, pdu in zip(segments, pdus, strict=True)
    ]
    with TestClient(create_app(config, service=service)) as client:
        state = wait_for(client, lambda s: len(s["messages"]) == 1)
        assert state["messages"][0]["body"] == "".join(segments)
        assert client.get("/v1/messages", headers=HEADERS).json()["items"] == state["messages"]
        for message_id in legacy_ids:
            assert client.get(f"/v1/messages/{message_id}", headers=HEADERS).status_code == 200
    with TestClient(create_app(config)) as client:
        state = wait_for(client, lambda s: s["modems"] and s["modems"][0].get("connected"))
        assert [message["body"] for message in state["messages"]] == ["".join(segments)]


def test_multipart_inbox_keeps_incomplete_parts_on_restart(tmp_path, emulator):
    config = Config(TOKEN, tmp_path, Path("web/build"), (Device("usb", emulator.path),))
    emulator.inbox = [multipart_pdu("world", 2, total=2, reference="1234"), bytes.fromhex(DELIVER)]
    emulator.unlocked = True
    with TestClient(create_app(config)) as client:
        state = wait_for(client, lambda s: any(row["body"] == "hi" for row in s["messages"]))
        assert [row["body"] for row in state["messages"]] == ["hi"]

    emulator.inbox = []
    with TestClient(create_app(config)) as client, client.websocket_connect("/v1/events") as ws:
        ws.send_text(TOKEN)
        assert ws.receive_json()["type"] == "snapshot"
        emulator.inbox = [multipart_pdu("hello ", 1, total=2, reference="1234")]
        os.write(emulator.master, b'\r\n+CMTI: "SM",1\r\n')
        event = receive_until(ws, lambda event: event["type"] == "message")
        assert event["data"]["body"] == "hello world"
        assert [
            row["body"] for row in client.get("/v1/messages", headers=HEADERS).json()["items"]
        ] == ["hello world", "hi"]
        close_events(ws)


@pytest.mark.parametrize("different", ["sim", "sender", "timestamp"])
def test_multipart_segments_from_different_messages_do_not_mix(tmp_path, different):
    first = multipart_pdu("hello ", 1, total=2)
    second = multipart_pdu(
        "world",
        2,
        total=2,
        stamp="42101000110000" if different == "timestamp" else "42101000000000",
    )
    if different == "sender":
        second = second[:4] + b"\x65\x87" + second[6:]
    store = Store(tmp_path)
    try:
        for raw, scope in ((first, "sim1"), (second, "sim2" if different == "sim" else "sim1")):
            sms = decode_deliver(raw)
            assert store.add_sms_part("usb", scope, sms.sender, sms.body, sms.part, raw) is None
        assert store.messages() == []
    finally:
        store.db.close()


def test_modem_identity_and_sms_follow_hardware_across_usb_ports(tmp_path, monkeypatch, emulator):
    from modembox import service

    first = tmp_path / "ttyUSB2"
    second = tmp_path / "ttyUSB8"
    first.symlink_to(emulator.path)
    second.symlink_to(emulator.path)
    devices = [Device("usb-first-port", str(first))]
    monkeypatch.setattr(service, "discover", lambda: devices.copy())
    emulator.unlocked = True
    config = Config(TOKEN, tmp_path / "db", Path("web/build"))

    with TestClient(create_app(config)) as client:
        state = wait_for(client, lambda s: bool(s["messages"]))
        modem_id = state["modems"][0]["id"]
        message = state["messages"][0]
        devices[:] = []
        wait_for(client, lambda s: not any(d["connected"] for d in s["modems"]))
        devices[:] = [Device("usb-second-port", str(second))]
        with client.websocket_connect("/v1/events") as ws:
            ws.send_text(TOKEN)
            assert ws.receive_json()["type"] == "snapshot"
            event = receive_until(
                ws, lambda event: event["type"] == "modem" and event["data"]["connected"]
            )
            close_events(ws)
        assert event["data"]["id"] == modem_id
        assert [d["id"] for d in client.get("/v1/modems", headers=HEADERS).json()] == [modem_id]

    with TestClient(create_app(config)) as client:
        state = wait_for(client, lambda s: any(d["connected"] for d in s["modems"]))
        assert state["modems"][0]["id"] == modem_id
        response = client.get("/v1/messages", params={"modem": modem_id}, headers=HEADERS)
        assert response.json()["items"] == [message]


def test_duplicate_equipment_identity_cannot_replace_a_connected_modem(
    tmp_path, monkeypatch, emulator
):
    from modembox import service

    duplicate = Emulator()
    distinct = Emulator()
    distinct.imei = "123456789012355"
    devices = [Device("usb-first-port", emulator.path)]
    monkeypatch.setattr(service, "discover", lambda: devices.copy())
    config = Config(TOKEN, tmp_path, Path("web/build"))
    try:
        with TestClient(create_app(config)) as client:
            state = wait_for(client, lambda s: any(d["connected"] for d in s["modems"]))
            modem_id = state["modems"][0]["id"]
            devices.extend(
                [
                    Device("usb-duplicate-port", duplicate.path),
                    Device("usb-other-port", distinct.path),
                ]
            )
            state = wait_for(
                client,
                lambda s: (
                    any(d.get("imei") == distinct.imei for d in s["modems"])
                    and "AT+CGSN" in duplicate.commands
                ),
            )
            assert len(state["modems"]) == 2
            assert {d["imei"] for d in state["modems"]} == {emulator.imei, distinct.imei}
            response = client.post(
                f"/v1/modems/{modem_id}/at", headers=HEADERS, json={"command": "AT+CSQ"}
            )
            assert response.status_code == 200
            assert "AT+CSQ" in emulator.commands
            assert "AT+CSQ" not in duplicate.commands
    finally:
        duplicate.close()
        distinct.close()


def test_discovered_modem_without_imei_uses_its_port_id(tmp_path, monkeypatch, emulator):
    from modembox import service

    emulator.imei = ""
    monkeypatch.setattr(service, "discover", lambda: [Device("usb-current-port", emulator.path)])
    config = Config(TOKEN, tmp_path, Path("web/build"))
    with TestClient(create_app(config)) as client:
        state = wait_for(client, lambda s: any(d["connected"] for d in s["modems"]))
        assert [d["id"] for d in state["modems"]] == ["usb-current-port"]


def test_serial_unlock_inbox_and_shared_call_state(tmp_path, emulator):
    emulator.iccid_fallback = True
    config = Config(TOKEN, tmp_path / "db", Path("web/build"), (Device("usb", emulator.path),))
    application = create_app(config)
    with TestClient(application) as client:
        assert client.get("/v1/state").status_code == 401
        assert client.get("/healthz").json() == {"ok": True}
        wait_for(client, lambda s: s["modems"] and s["modems"][0].get("sim") == "SIM PIN")
        with (
            client.websocket_connect("/v1/events") as first,
            client.websocket_connect("/v1/events") as second,
        ):
            for ws in (first, second):
                ws.send_text(TOKEN)
                assert ws.receive_json()["type"] == "snapshot"
            result = client.post(
                "/v1/modems/usb/pin", headers=HEADERS, json={"pin": "1234", "remember": True}
            )
            assert result.status_code == 200, result.text
            assert result.json()["pinSaved"] is True
            assert emulator.commands.count('AT+CPIN="1234"') == 1
            assert "1234" not in str(wait_for(client, lambda s: bool(s["messages"]))["activity"])
            messages = client.get("/v1/messages", headers=HEADERS).json()["items"]
            assert [(sms["peer"], sms["body"]) for sms in messages] == [("+1234", "hi")]
            submitted = client.post(
                "/v1/modems/usb/sms", headers=HEADERS, json={"number": "+1234", "text": "Reply"}
            )
            assert submitted.json()["status"] == "submitted"
            assert emulator.sms_payload == encode_submit("+1234", "Reply")
            result = client.post(
                "/mcp",
                headers=MCP_HEADERS,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "dial",
                        "arguments": {
                            "modem_id": "usb",
                            "number": "+1234567890",
                        },
                    },
                },
            )
            assert result.status_code == 200, result.text
            assert '"isError":false' in result.text
            for ws in (first, second):
                event = receive_until(
                    ws,
                    lambda event: (
                        event["type"] == "modem"
                        and any(call["state"] == "active" for call in event["data"]["calls"])
                    ),
                )
                assert event["data"]["calls"][0]["peer"] == "+1234567890"
            with client.websocket_connect("/v1/events") as late:
                late.send_text(TOKEN)
                assert late.receive_json()["data"]["modems"][0]["calls"][0]["state"] == "active"
                close_events(late)
            assert (
                client.delete("/v1/modems/usb/calls/current", headers=HEADERS).json()["calls"] == []
            )
            result = client.post(
                "/v1/modems/usb/at", headers=HEADERS, json={"command": "AT+CFUN=0"}
            )
            assert result.status_code == 403
            for ws in (first, second):
                close_events(ws)


def test_mcp_http_and_websocket_authorization(tmp_path):
    application = create_app(Config(TOKEN, tmp_path, Path("web/build")))
    with TestClient(application) as client:
        assert client.get("/auth-mode").json() == {"tokenRequired": True}
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post("/mcp", json=payload).status_code == 401
        result = client.post("/mcp", headers=MCP_HEADERS, json=payload)
        assert result.status_code == 200, result.text
        assert "modem_at" in result.text and "capture_audio" in result.text
        with client.websocket_connect("/v1/events") as socket:
            socket.send_text("incorrect")
            assert socket.receive()["code"] == 4401


@pytest.mark.unit
def test_auth_opt_out_requires_explicit_true(monkeypatch):
    monkeypatch.delenv("MODEMBOX_TOKEN", raising=False)
    monkeypatch.delenv("MODEMBOX_DISABLE_AUTH", raising=False)
    with pytest.raises(ValueError, match="MODEMBOX_TOKEN is required"):
        Config.from_env()
    monkeypatch.setenv("MODEMBOX_DISABLE_AUTH", "yes")
    with pytest.raises(ValueError, match="must be true or false"):
        Config.from_env()
    monkeypatch.setenv("MODEMBOX_DISABLE_AUTH", "true")
    assert Config.from_env().disable_auth
    monkeypatch.setenv("MODEMBOX_DISABLE_AUTH", "false")
    monkeypatch.setenv("MODEMBOX_TOKEN", TOKEN)
    assert not Config.from_env().disable_auth


def test_auth_opt_out_allows_rest_mcp_and_same_origin_events(tmp_path):
    config = Config("", tmp_path, Path("web/build"), disable_auth=True)
    with TestClient(create_app(config)) as client:
        assert client.get("/auth-mode").json() == {"tokenRequired": False}
        assert client.get("/v1/state").status_code == 200
        foreign = {"Origin": "https://example.invalid"}
        assert client.get("/v1/state", headers=foreign).status_code == 403
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = client.post("/mcp", headers={"Accept": MCP_HEADERS["Accept"]}, json=payload)
        assert response.status_code == 200
        assert "list_modems" in response.text
        with client.websocket_connect("/v1/events", headers=foreign) as socket:
            assert socket.receive()["code"] == 4403
        with client.websocket_connect(
            "/v1/events", headers={"Origin": "http://testserver"}
        ) as socket:
            assert socket.receive_json()["type"] == "snapshot"
            close_events(socket)


def test_last_pin_attempt_is_preserved(tmp_path, emulator):
    emulator.retries = 1
    config = Config(TOKEN, tmp_path, Path("web/build"), (Device("usb", emulator.path),))
    with TestClient(create_app(config)) as client:
        wait_for(
            client, lambda state: state["modems"] and state["modems"][0].get("sim") == "SIM PIN"
        )
        response = client.post(
            "/v1/modems/usb/pin",
            headers=HEADERS,
            json={
                "pin": "1234",
                "remember": True,
            },
        )
        assert response.status_code == 409
        assert not any(cmd.startswith("AT+CPIN=") for cmd in emulator.commands)


def test_browser_media_stream(tmp_path, emulator):
    master, slave = pty.openpty()
    try:
        config = Config(
            TOKEN, tmp_path, Path("web/build"), (Device("usb", emulator.path, os.ttyname(slave)),)
        )
        emulator.unlocked = True
        with TestClient(create_app(config)) as client:
            wait_for(
                client,
                lambda state: state["modems"] and state["modems"][0]["capabilities"].get("audio"),
            )
            response = client.post(
                "/v1/modems/usb/calls", headers=HEADERS, json={"number": "+1234567890"}
            )
            assert response.status_code == 200
            wait_for(client, lambda state: state["modems"][0].get("audio"))
            with client.websocket_connect("/v1/modems/usb/audio/ws") as socket:
                socket.send_text(TOKEN)
                socket.send_text("mic:on")
                wait_for(client, lambda state: state["modems"][0].get("microphone"))
                received = bytes.fromhex("1234") * 160
                sent = bytes.fromhex("2345") * 160
                os.write(master, received)
                socket.send_bytes(sent)
                assert socket.receive_bytes() == received
                output = bytearray()
                deadline = time.monotonic() + 2
                while sent not in output and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.1)[0]:
                        output.extend(os.read(master, 4096))
                assert sent in output
            assert client.delete("/v1/modems/usb/calls/current", headers=HEADERS).status_code == 200
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.unit
def test_usb_modem_port_opens_without_manual_line_toggles(monkeypatch):
    def reject_toggle(_port):
        raise BrokenPipeError(32, "USB modem rejects manual control signals")

    monkeypatch.setattr(serial.Serial, "_update_dtr_state", reject_toggle)
    monkeypatch.setattr(serial.Serial, "_update_rts_state", reject_toggle)
    master, slave = pty.openpty()
    try:
        port = open_port(os.ttyname(slave))
        close_port(port)
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.unit
def test_codecs_and_profiles():
    assert decode_deliver(bytes.fromhex(DELIVER))[:2] == ("+1234", "hi")
    assert encode_submit("+1234", "hi").endswith(bytes.fromhex("080400680069"))
    with pytest.raises(ValueError):
        encode_submit("+1234;ATH", "hi")
    payload = b"\x00\x01" * 160
    assert decode_clip(encode_clip(payload)) == payload
    assert identify("Huawei", "E173") is HUAWEI_PCM
    assert identify("Unknown", "E173") is GENERIC


@pytest.mark.unit
@pytest.mark.parametrize("reference", ["42", "1234"])
def test_decode_multipart_header(reference):
    sms = decode_deliver(multipart_pdu("hello", 2, reference=reference))
    assert sms.body == "hello"
    assert (sms.part.reference, sms.part.total, sms.part.sequence) == (reference, 4, 2)
    with pytest.raises(ValueError):
        decode_deliver(multipart_pdu("hello", 5, reference=reference))
    with pytest.raises(ValueError):
        decode_deliver(multipart_pdu("hello", 2, reference=reference)[:-1])


@pytest.mark.unit
def test_equipment_id_uses_the_hardware_digits():
    identifier = equipment_id("123456789012345")
    assert identifier
    assert equipment_id(' "1234567890123401" ') == identifier
    assert equipment_id("123456789012355") != identifier


@pytest.mark.unit
@pytest.mark.parametrize(
    "imei", ["", "123", "000000000000000", "１２３４５６７８９０１２３４５", "ERROR"]
)
def test_equipment_id_rejects_unusable_values(imei):
    assert equipment_id(imei) is None


@pytest.mark.unit
def test_discovery_groups_ports_by_usb_device(tmp_path):
    tty_root, dev_root = tmp_path / "tty", tmp_path / "dev"
    dev_root.mkdir()
    for address, first in (("3-1.2", 0), ("3-1.3", 3)):
        usb = tmp_path / address
        usb.mkdir()
        (usb / "idVendor").write_text("12d1")
        (usb / "idProduct").write_text("1001")
        for offset, interface in enumerate(("00", "01", "02")):
            name = f"ttyUSB{first + offset}"
            parent = usb / (address + ":1." + str(offset))
            child = parent / name
            child.mkdir(parents=True)
            (parent / "bInterfaceNumber").write_text(interface)
            tty = tty_root / name
            tty.mkdir(parents=True)
            (tty / "device").symlink_to(child)
            (dev_root / name).touch()
    devices = discover(tty_root, dev_root)
    assert len({device.id for device in devices}) == 2
    assert [(Path(d.control).name, Path(d.audio).name) for d in devices] == [
        ("ttyUSB2", "ttyUSB1"),
        ("ttyUSB5", "ttyUSB4"),
    ]

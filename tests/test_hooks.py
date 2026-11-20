import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from modembox.config import Config, Device
from modembox import hook_http
from modembox.hook_http import allowed_address
from modembox.hook_models import Rule, matches
from modembox.hooks import Hooks
from modembox.server import create_app
from modembox.sms import encode_segments
from modembox.store import Store
from test_gateway import (
    HEADERS,
    MCP_HEADERS,
    TOKEN,
    Emulator,
    close_events,
    multipart_pdu,
    receive_until,
    wait_for,
)

CALLER = "+40123456789"
RULE_PATH = "/v1/hooks/rules"
DEST_PATH = "/v1/hooks/destinations"


@pytest.fixture
def gateway(tmp_path):
    modem = Emulator()
    modem.unlocked = True
    config = Config(TOKEN, tmp_path, Path("web/build"), (Device("usb", modem.path),))
    try:
        yield config, modem
    finally:
        modem.close()


def hook_rule(trigger, actions, conditions=()):
    return {
        "name": "Example hook",
        "trigger": trigger,
        "conditions": list(conditions),
        "actions": actions,
    }


def wait_for_hook(client, predicate):
    return wait_for(client, lambda state: predicate(state["hooks"]))["hooks"]


def sms_notice(modem):
    os.write(modem.master, b'\r\n+CMTI: "SM",1\r\n')


def sms_stamp():
    moment = datetime.now(timezone.utc) + timedelta(seconds=1)
    digits = moment.strftime("%y%m%d%H%M%S")
    return "".join(digits[index : index + 2][::-1] for index in range(0, len(digits), 2)) + "00"


@pytest.mark.unit
def test_conditions_normalize_number_and_preserve_text_case_option():
    number = {"field": "peer", "op": "startsWith", "values": ["+40 (123)"]}
    text = {"field": "text", "op": "equals", "values": ["ALERT"], "caseSensitive": True}
    rule = Rule.model_validate(hook_rule("sms.received", [{"type": "sms"}], [number, text]))
    event = {"type": "sms.received", "modem": "usb", "peer": CALLER, "text": "alert"}
    assert not matches(rule, event)
    assert matches(rule.model_copy(update={"match": "any"}), event)
    assert matches(rule, event | {"text": "ALERT"})
    assert not matches(rule, event | {"peer": ""})


@pytest.mark.unit
def test_multipart_submit_preserves_unicode_at_segment_boundaries():
    body = "a" * 65 + "🙂" + "b" * 66
    parts = encode_segments(CALLER, body)
    assert len(parts) == 3
    header_start = 8 + (len(CALLER[1:]) + 1) // 2
    headers = [part[header_start : header_start + 7] for part in parts]
    assert all(header[:3] == b"\x06\x08\x04" for header in headers)
    assert len({header[3:5] for header in headers}) == 1
    assert [header[5:] for header in headers] == [b"\x03\x01", b"\x03\x02", b"\x03\x03"]
    assert "".join(part[header_start + 7 :].decode("utf-16-be") for part in parts) == body


@pytest.mark.unit
@pytest.mark.parametrize(
    "address", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0"]
)
def test_http_destination_cannot_approve_special_address(address):
    assert not allowed_address(address, address)


def test_hooks_preview_and_multipart_forward_only_fires_once(gateway):
    config, modem = gateway
    with TestClient(create_app(config)) as client:
        state = wait_for(client, lambda state: bool(state["messages"]))
        initial = state["messages"][0]
        rule = hook_rule("sms.received", [{"type": "sms", "recipient": CALLER}])
        assert client.post(RULE_PATH, json=rule).status_code == 401
        assert client.post(RULE_PATH, headers=HEADERS, json=rule).status_code == 201
        preview = client.post(
            "/v1/hooks/preview",
            headers=HEADERS,
            json={"rule": rule, "messageId": initial["id"]},
        ).json()
        assert preview["matches"] and preview["actions"][0]["text"] == "hi"
        assert client.get("/v1/hooks/runs", headers=HEADERS).json()["items"] == []
        stamp = sms_stamp()
        body = "a" * 66 + "b" * 66 + "c" * 66
        pdus = [
            multipart_pdu(body[66 * index : 66 * (index + 1)], index + 1, total=3, stamp=stamp)
            for index in range(3)
        ]
        modem.inbox += pdus + [pdus[0]]
        sms_notice(modem)
        hooks = wait_for_hook(
            client, lambda hooks: any(r["status"] == "submitted" for r in hooks["runs"])
        )
        assert len(hooks["runs"]) == 1
        message_id = hooks["runs"][0]["result"]["messageId"]
        outgoing = client.get(f"/v1/messages/{message_id}", headers=HEADERS).json()
        assert outgoing["body"] == body
        assert [part["status"] for part in outgoing["parts"]] == ["submitted"] * 3

    with TestClient(create_app(config)) as client:
        wait_for(client, lambda state: state["modems"] and state["modems"][0].get("sim") == "READY")
        hooks = wait_for_hook(client, lambda hooks: len(hooks["runs"]) == 1)
        assert hooks["runs"][0]["status"] == "submitted"
        assert sum(command.startswith("AT+CMGS=") for command in modem.commands) == 3


def test_call_hook_declines_once_and_runs_http_after_decline(gateway, monkeypatch):
    config, modem = gateway
    delivered = []

    async def deliver(destination, payload, _content_type, delivery_id):
        delivered.append((destination["headers"], json.loads(payload), delivery_id))
        return 204

    monkeypatch.setattr("modembox.hook_http.deliver", deliver)
    with TestClient(create_app(config)) as client:
        wait_for(
            client,
            lambda state: state["modems"] and state["modems"][0]["capabilities"].get("callerId"),
        )
        destination = client.post(
            DEST_PATH,
            headers=HEADERS,
            json={
                "name": "Example destination",
                "url": "https://example.org/hook",
                "headers": {"Authorization": "Bearer EXAMPLE-DO-NOT-USE"},
            },
        ).json()
        assert destination["hasHeaders"] and "headers" not in destination
        assert (
            client.post(
                DEST_PATH, headers=HEADERS, json={"name": "test", "url": "http://127.0.0.1"}
            ).status_code
            == 422
        )
        rule = hook_rule(
            "call.incoming",
            [{"type": "http", "destination": destination["id"]}, {"type": "decline"}],
            [{"field": "peer", "op": "startsWith", "values": ["+40"]}],
        )
        assert client.post(RULE_PATH, headers=HEADERS, json=rule).status_code == 201
        assert (
            client.post(
                RULE_PATH, headers=HEADERS, json=rule | {"actions": [{"type": "decline"}]}
            ).status_code
            == 201
        )
        modem.state = f'+CLCC: 1,1,4,0,0,"{CALLER}",145'
        os.write(modem.master, b"\r\nRING\r\nRING\r\n")
        hooks = wait_for_hook(
            client,
            lambda hooks: (
                len(hooks["runs"]) == 3
                and all(row["status"] == "succeeded" for row in hooks["runs"])
            ),
        )
        assert len(hooks["calls"]) == 1 and hooks["calls"][0]["outcome"] == "declined"
        assert modem.commands.count("ATH") == 1
        assert len(delivered) == 1
        assert delivered[0][1]["outcome"] == "declined"
        assert delivered[0][1]["peer"] == CALLER
        assert delivered[0][0]["Authorization"] == "Bearer EXAMPLE-DO-NOT-USE"
        results = client.get("/v1/hooks/runs?limit=2", headers=HEADERS).json()
        assert results["hasMore"] and len(results["items"]) == 2
        second = client.get("/v1/hooks/runs?limit=2&offset=2", headers=HEADERS).json()
        assert not second["hasMore"] and len(second["items"]) == 1
        assert client.get("/v1/hooks/runs?limit=2&offset=3", headers=HEADERS).json()["items"] == []


def test_call_hook_leaves_other_active_call_alone(gateway):
    config, modem = gateway
    with TestClient(create_app(config)) as client:
        wait_for(
            client,
            lambda state: state["modems"] and state["modems"][0]["capabilities"].get("callerId"),
        )
        rule = hook_rule("call.incoming", [{"type": "decline"}])
        assert client.post(RULE_PATH, headers=HEADERS, json=rule).status_code == 201
        modem.state = f'+CLCC: 1,1,4,0,0,"{CALLER}",145\r\n+CLCC: 2,0,0,0,0,"+1234",145'
        os.write(modem.master, b"\r\nRING\r\n")
        hooks = wait_for_hook(
            client, lambda hooks: hooks["runs"] and hooks["runs"][0]["status"] == "failed"
        )
        assert hooks["runs"][0]["error"] == "DECLINE_UNAVAILABLE"
        assert "ATH" not in modem.commands


def test_mcp_and_http_share_hook_rules(gateway):
    config, _ = gateway
    rule = hook_rule("call.incoming", [{"type": "decline"}])
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/mcp",
            headers=MCP_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "save_hook_rule", "arguments": {"rule": rule}},
            },
        )
        assert response.status_code == 200 and '"isError":false' in response.text
        saved = client.get(RULE_PATH, headers=HEADERS).json()["items"]
        assert len(saved) == 1 and saved[0]["trigger"] == "call.incoming"


def test_http_delivery_sends_signed_bytes_and_does_not_follow_redirect(monkeypatch):
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (self.path, self.headers, self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(307)
            self.send_header("Location", "/second")
            self.end_headers()

        def log_message(self, *_):
            pass

    # Docker's networkless test runner has only loopback. Override its address policy
    # here so the real request can reach the fixture.
    monkeypatch.setattr(hook_http, "allowed_address", lambda address, _: address == "127.0.0.1")
    with ThreadingHTTPServer(("127.0.0.1", 0), Receiver) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            destination = {
                "url": f"http://127.0.0.1:{server.server_port}/first",
                "method": "POST",
                "headers": {"X-Example": "sample"},
                "signingKey": "EXAMPLE-DO-NOT-USE",
                "privateAddress": None,
            }
            status = asyncio.run(
                hook_http.deliver(destination, b'{"text":"hi"}', "application/json", "5")
            )
        finally:
            server.shutdown()
            thread.join()
    assert status == 307 and len(received) == 1
    path, headers, body = received[0]
    assert path == "/first" and body == b'{"text":"hi"}'
    assert headers["X-Example"] == "sample" and headers["X-ModemBox-Delivery"] == "5"
    stamp, digest = headers["X-ModemBox-Signature"].split(",")
    expected = hmac.new(b"EXAMPLE-DO-NOT-USE", stamp[2:].encode() + b"." + body, hashlib.sha256)
    assert digest == "v1=" + expected.hexdigest()


def test_manual_answer_wins_over_queued_decline(gateway, monkeypatch):
    config, modem = gateway
    release_action = Event()
    execute = Hooks.execute

    async def delay_decline(self, row, action, event):
        while action["type"] == "decline" and not release_action.is_set():
            await asyncio.sleep(0.02)
        return await execute(self, row, action, event)

    monkeypatch.setattr(Hooks, "execute", delay_decline)
    with TestClient(create_app(config)) as client:
        wait_for(
            client,
            lambda state: state["modems"] and state["modems"][0]["capabilities"].get("callerId"),
        )
        assert (
            client.post(
                RULE_PATH, headers=HEADERS, json=hook_rule("call.incoming", [{"type": "decline"}])
            ).status_code
            == 201
        )
        modem.state = f'+CLCC: 1,1,4,0,0,"{CALLER}",145'
        os.write(modem.master, b"\r\nRING\r\n")
        wait_for_hook(
            client, lambda hooks: hooks["runs"] and hooks["runs"][0]["status"] == "running"
        )
        response = client.post("/v1/modems/usb/calls/answer", headers=HEADERS, json={})
        assert response.status_code == 200 and response.json()["calls"][0]["state"] == "active"
        release_action.set()
        hooks = wait_for_hook(client, lambda hooks: hooks["runs"][0]["status"] == "skipped")
        assert hooks["runs"][0]["error"] == "CALL_GONE" and "ATH" not in modem.commands
        modem.state = ""
        os.write(modem.master, b"\r\nNO CARRIER\r\n")
        ended = wait_for_hook(client, lambda hooks: hooks["calls"][0]["outcome"] == "answered")
        assert len(ended["calls"]) == 1


def test_hooks_open_existing_inbox_and_pin_without_rewriting_their_tables(tmp_path):
    previous = Store(tmp_path)
    previous.save_pin("sim", "1234")
    old_sms = previous.add_message("usb", "incoming", CALLER, "Old message", "received")
    previous.db.close()
    with sqlite3.connect(tmp_path / "modembox.sqlite3") as db:
        db.execute("DROP TABLE sms_outgoing_parts")
        previous_schema = dict(db.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
    config = Config(TOKEN, tmp_path, Path("web/build"))
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/messages", headers=HEADERS).json()["items"] == [old_sms]
        assert client.get("/v1/hooks/rules", headers=HEADERS).json()["items"] == []
    current = Store(tmp_path)
    try:
        assert current.get_pin("sim") == "1234"
        schema = dict(current.db.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"))
        assert {key: schema[key] for key in previous_schema} == previous_schema
    finally:
        current.db.close()


def test_short_call_with_delayed_caller_id_updates_both_subscribers(gateway):
    config, modem = gateway
    with TestClient(create_app(config)) as client:
        wait_for(
            client,
            lambda state: state["modems"] and state["modems"][0]["capabilities"].get("callerId"),
        )
        with (
            client.websocket_connect("/v1/events") as first,
            client.websocket_connect("/v1/events") as second,
        ):
            for socket in (first, second):
                socket.send_text(TOKEN)
                assert socket.receive_json()["type"] == "snapshot"
            os.write(modem.master, b"\r\nRING\r\n")
            initial = receive_until(first, lambda frame: frame["type"] == "hook.calls")["data"]
            assert initial["callerId"] == "unavailable"
            os.write(modem.master, f'\r\n+CLIP: "{CALLER}",145,,,,0\r\nNO CARRIER\r\n'.encode())
            for socket in (first, second):
                ended = receive_until(
                    socket,
                    lambda frame: (
                        frame["type"] == "hook.calls" and frame["data"]["outcome"] == "missed"
                    ),
                )["data"]
                assert ended["id"] == initial["id"] and ended["peer"] == CALLER
                close_events(socket)
            assert len(client.get("/v1/hooks/calls", headers=HEADERS).json()["items"]) == 1

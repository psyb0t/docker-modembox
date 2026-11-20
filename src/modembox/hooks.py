import asyncio
import http.client
import json
from time import time
from urllib.parse import urlsplit

from . import hook_http
from .errors import OperationError
from .hook_models import Destination, Preview, Rule, matches, normalized_peer, render
from .sms import PHONE

QUEUE_POLL = 0.25
ACTION_TTL = 3600
DECLINE_TTL = 8
SMS_RATE_WINDOW = 60
SMS_RATE_LIMIT = 6
HTTP_ATTEMPTS = 3
HTTP_PAYLOAD_LIMIT = 65536
MAX_RULES = 200
MAX_DESTINATIONS = 100
RECENT_LIMIT = 50
RESOURCES = {
    "rules": "hook_rules",
    "destinations": "hook_destinations",
    "runs": "hook_runs",
    "calls": "hook_calls",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
    activated REAL NOT NULL, document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hook_destinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
    document TEXT NOT NULL, credentials BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS hook_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hook_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, rule_id INTEGER NOT NULL,
    action_index INTEGER NOT NULL, destination_id INTEGER, kind TEXT NOT NULL,
    status TEXT NOT NULL, available REAL NOT NULL, expires REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, started REAL, document TEXT NOT NULL,
    UNIQUE (event_id, rule_id, action_index)
);
CREATE INDEX IF NOT EXISTS hook_pending ON hook_runs(kind, available, id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS hook_event ON hook_runs(event_id, kind, status);
CREATE INDEX IF NOT EXISTS hook_sms_rate ON hook_runs(started) WHERE kind='sms';
"""


class Hooks:
    def __init__(self, store, publish, act):
        self.store = store
        self.db = store.db
        self.publish = publish
        self.act = act
        self.db.executescript(SCHEMA)
        with self.db:
            self.db.execute("UPDATE hook_runs SET status='unknown' WHERE status='running'")
            self.db.execute(
                "UPDATE hook_runs SET status='expired' WHERE status='pending' AND kind='decline'"
            )

    def row(self, resource, key):
        table = RESOURCES[resource]
        row = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (key,)).fetchone()
        if row is None:
            raise OperationError("Hook record not found", "NOT_FOUND", 404)
        return row

    def public(self, resource, row):
        doc = json.loads(row["document"])
        if resource == "runs":
            return {
                "id": row["id"],
                "eventId": row["event_id"],
                "ruleId": row["rule_id"],
                "name": doc["name"],
                "action": row["kind"],
                "status": row["status"],
                "attempts": row["attempts"],
                "error": doc.get("error"),
                "created": doc["event"]["time"],
                "updated": doc.get("updated"),
                "result": doc.get("result"),
            }
        if resource == "calls":
            return doc
        return {"id": row["id"], **doc}

    def page(self, resource, limit=RECENT_LIMIT, offset=0):
        table = RESOURCES[resource]
        rows = self.db.execute(
            f"SELECT * FROM {table} ORDER BY id DESC LIMIT ? OFFSET ?", (limit + 1, offset)
        ).fetchall()
        return {
            "items": [self.public(resource, row) for row in rows[:limit]],
            "limit": limit,
            "offset": offset,
            "hasMore": len(rows) > limit,
        }

    def snapshot(self):
        return {
            "rules": self.page("rules", MAX_RULES)["items"],
            "destinations": self.page("destinations", MAX_DESTINATIONS)["items"],
            "runs": self.page("runs")["items"],
            "calls": self.page("calls")["items"],
        }

    def save_rule(self, rule: Rule, key=None):
        for action in rule.actions:
            if action.type == "http":
                self.row("destinations", action.destination)
        doc = rule.model_dump_json()
        with self.db:
            if key is None:
                self.check_limit("rules", MAX_RULES)
                key = self.db.execute(
                    "INSERT INTO hook_rules(enabled,revision,activated,document) VALUES (?,1,?,?)",
                    (rule.enabled, time(), doc),
                ).lastrowid
            else:
                previous = self.row("rules", key)
                self.db.execute(
                    """UPDATE hook_rules SET enabled=?,revision=revision+1,activated=?,document=?
                       WHERE id=?""",
                    (
                        rule.enabled,
                        previous["activated"] if previous["enabled"] else time(),
                        doc,
                        key,
                    ),
                )
            cancelled = [] if rule.enabled else self.cancel("rule_id", key)
        return self.changed("rules", key, cancelled)

    def check_limit(self, resource, limit):
        table = RESOURCES[resource]
        if self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] >= limit:
            raise OperationError("Hook configuration limit reached", "HOOK_LIMIT", 409)

    def save_destination(self, destination: Destination, key=None):
        doc = destination.model_dump(exclude={"headers", "signingKey"})
        secrets = self.destination_credentials(key) if key is not None else {}
        previous = self.row("destinations", key) if key is not None else None
        if previous:
            old_target = urlsplit(json.loads(previous["document"])["url"])
            new_target = urlsplit(destination.url)
            if (old_target.scheme, old_target.netloc) != (new_target.scheme, new_target.netloc):
                secrets = {}
        headers = secrets.get("headers", {}) if destination.headers is None else destination.headers
        signing = (
            secrets.get("signingKey", "")
            if destination.signingKey is None
            else destination.signingKey
        )
        doc["hasHeaders"] = bool(headers)
        doc["hasSigningKey"] = bool(signing)
        encrypted = self.store.cipher.encrypt(
            json.dumps({"headers": headers, "signingKey": signing}).encode()
        )
        fields = (destination.enabled, json.dumps(doc), encrypted)
        with self.db:
            if key is None:
                self.check_limit("destinations", MAX_DESTINATIONS)
                key = self.db.execute(
                    """INSERT INTO hook_destinations(enabled,document,credentials,revision)
                       VALUES (?,?,?,1)""",
                    fields,
                ).lastrowid
                cancelled = []
            else:
                self.db.execute(
                    """UPDATE hook_destinations SET enabled=?,document=?,credentials=?,
                       revision=revision+1 WHERE id=?""",
                    (*fields, key),
                )
                cancelled = self.cancel("destination_id", key)
        return self.changed("destinations", key, cancelled)

    def destination_credentials(self, key):
        row = self.row("destinations", key)
        return json.loads(self.store.cipher.decrypt(row["credentials"]))

    def changed(self, resource, key, cancelled):
        result = self.public(resource, self.row(resource, key))
        self.publish("hook." + resource, result)
        self.publish_runs(cancelled)
        return result

    def remove(self, resource, key):
        self.row(resource, key)
        column = {"rules": "rule_id", "destinations": "destination_id"}[resource]
        table = RESOURCES[resource]
        with self.db:
            cancelled = self.cancel(column, key)
            self.db.execute(f"DELETE FROM {table} WHERE id=?", (key,))
        self.publish("hook." + resource, {"id": key, "deleted": True})
        self.publish_runs(cancelled)
        return {"id": key, "deleted": True}

    def cancel(self, column, key):
        return self.db.execute(
            f"UPDATE hook_runs SET status='cancelled' WHERE {column}=? AND status='pending' RETURNING *",
            (key,),
        ).fetchall()

    def publish_runs(self, rows):
        for row in rows:
            self.publish("hook.runs", self.public("runs", row))

    def publish_pending(self, event_id):
        self.publish_runs(
            self.db.execute(
                "SELECT * FROM hook_runs WHERE event_id=? AND status='pending' ORDER BY id",
                (event_id,),
            ).fetchall()
        )

    def enqueue(self, event, timestamp):
        now = time()
        for row in self.db.execute(
            "SELECT * FROM hook_rules WHERE enabled=1 AND activated<=?", (timestamp,)
        ).fetchall():
            rule = Rule.model_validate_json(row["document"])
            if not matches(rule, event):
                continue
            for index, action in enumerate(rule.actions):
                destination = action.destination if action.type == "http" else None
                revision = None
                if destination is not None:
                    target = self.db.execute(
                        "SELECT revision,enabled FROM hook_destinations WHERE id=?", (destination,)
                    ).fetchone()
                    if target is None or not target["enabled"]:
                        continue
                    revision = target["revision"]
                document = {
                    "name": rule.name,
                    "action": action.model_dump(),
                    "event": event,
                    "destinationRevision": revision,
                }
                ttl = DECLINE_TTL if action.type == "decline" else ACTION_TTL
                self.db.execute(
                    """INSERT OR IGNORE INTO hook_runs(event_id,rule_id,action_index,destination_id,
                       kind,status,available,expires,document) VALUES (?,?,?,?,?,'pending',?,?,?)""",
                    (
                        event["id"],
                        row["id"],
                        index,
                        destination,
                        action.type,
                        now,
                        now + ttl,
                        json.dumps(document),
                    ),
                )

    def sms_received(self, message, timestamp):
        if timestamp is not None and 0 < timestamp <= time() + 300:
            self.enqueue(sms_event(message, timestamp), timestamp)

    def call_change(self, call, events):
        with self.db:
            self.db.execute(
                """INSERT INTO hook_calls(call_id,document) VALUES (?,?)
                   ON CONFLICT(call_id) DO UPDATE SET document=excluded.document""",
                (call["id"], json.dumps(call)),
            )
            for trigger in events:
                event = call_event(call, trigger)
                self.enqueue(event, event["time"])
        self.publish("hook.calls", call)
        for trigger in events:
            self.publish_pending(call_event(call, trigger)["id"])

    def recover_calls(self):
        rows = self.db.execute(
            "SELECT document FROM hook_calls WHERE json_extract(document,'$.outcome') IS NULL"
        )
        for row in rows.fetchall():
            call = json.loads(row["document"])
            call.update(outcome="interrupted", ended=time())
            self.call_change(call, ["call.ended"])

    def preview(self, preview: Preview):
        if preview.messageId is not None:
            message = self.store.message(preview.messageId)
            if not message or message["direction"] != "incoming":
                raise OperationError("Incoming SMS not found", "NOT_FOUND", 404)
            event = sms_event(message, time())
        else:
            row = self.db.execute(
                "SELECT document FROM hook_calls WHERE call_id=?", (preview.callId,)
            ).fetchone()
            if not row:
                raise OperationError("Incoming call not found", "NOT_FOUND", 404)
            trigger = "call.ended" if preview.rule.trigger == "call.ended" else "call.incoming"
            event = call_event(json.loads(row["document"]), trigger)
        matched = matches(preview.rule, event)
        actions = (
            [self.render_action(action.model_dump(), event) for action in preview.rule.actions]
            if matched
            else []
        )
        return {"matches": matched, "actions": actions}

    def render_action(self, action, event):
        match action["type"]:
            case "sms":
                return {
                    "type": "sms",
                    "number": action["recipient"] or normalized_peer(event.get("peer") or ""),
                    "modem": action["modem"] or event["modem"],
                    "text": render(action["template"], event),
                }
            case "http":
                dest = self.public("destinations", self.row("destinations", action["destination"]))
                body, content_type = http_body(action, event, dest["method"])
                return {
                    "type": "http",
                    "destination": dest["id"],
                    "name": dest["name"],
                    "method": dest["method"],
                    "contentType": content_type,
                    "body": body.decode() if body is not None else None,
                }
            case _:
                return {"type": "decline", "modem": event["modem"]}

    async def worker(self, kind):
        while True:
            row = self.db.execute(
                """SELECT r.* FROM hook_runs r WHERE kind=? AND status='pending' AND available<=?
                   AND (kind='decline' OR NOT EXISTS (
                       SELECT 1 FROM hook_runs d WHERE d.event_id=r.event_id AND d.kind='decline'
                       AND d.status IN ('pending','running')))
                   ORDER BY available,id LIMIT 1""",
                (kind, time()),
            ).fetchone()
            if row is None:
                await asyncio.sleep(QUEUE_POLL)
                continue
            await self.run(row)

    def update_run(self, row, status, doc, delay=0, attempt=False):
        now = time()
        if status == "pending" and not self.can_retry(row, doc):
            status = "cancelled"
        doc["updated"] = now
        with self.db:
            updated = self.db.execute(
                """UPDATE hook_runs SET status=?,document=?,available=?,attempts=attempts+?,
                   started=CASE WHEN ? THEN ? ELSE started END WHERE id=? RETURNING *""",
                (status, json.dumps(doc), now + delay, int(attempt), int(attempt), now, row["id"]),
            ).fetchone()
        self.publish_runs([updated])

    def can_retry(self, row, doc):
        rule = self.db.execute(
            "SELECT enabled FROM hook_rules WHERE id=?", (row["rule_id"],)
        ).fetchone()
        if rule is None or not rule["enabled"]:
            return False
        if row["destination_id"] is None:
            return True
        target = self.db.execute(
            "SELECT enabled,revision FROM hook_destinations WHERE id=?", (row["destination_id"],)
        ).fetchone()
        return (
            target is not None
            and bool(target["enabled"])
            and target["revision"] == doc.get("destinationRevision")
        )

    async def run(self, row):
        doc = json.loads(row["document"])
        action, event = doc["action"], doc["event"]
        if not self.can_retry(row, doc):
            self.update_run(row, "cancelled", doc)
            return
        if row["expires"] <= time():
            self.update_run(row, "expired", doc)
            return
        if row["kind"] == "decline":
            prior = self.db.execute(
                """SELECT status,document FROM hook_runs WHERE event_id=? AND kind='decline'
                   AND id!=? AND attempts>0 ORDER BY id LIMIT 1""",
                (row["event_id"], row["id"]),
            ).fetchone()
            if prior:
                prior_doc = json.loads(prior["document"])
                doc.update(error=prior_doc.get("error"), result=prior_doc.get("result"))
                self.update_run(row, prior["status"], doc)
                return
        if row["kind"] == "sms" and self.sms_rate_limited():
            self.update_run(row, "pending", doc, delay=SMS_RATE_WINDOW)
            return
        self.update_run(row, "running", doc, attempt=True)
        try:
            status, result = await self.execute(row, action, event)
        except OperationError as error:
            doc["error"] = error.code
            if error.code == "HOOK_DEFER":
                self.defer(row, doc)
                return
            status, result = ("skipped" if error.code == "CALL_GONE" else "failed"), None
        except (OSError, TimeoutError, http.client.HTTPException):
            status, result = "retry", None
            doc["error"] = "HTTP_TRANSPORT"
        if status == "retry":
            if row["attempts"] + 1 < HTTP_ATTEMPTS:
                self.update_run(row, "pending", doc, delay=2 ** (row["attempts"] + 1))
                return
            status = "failed"
        else:
            if status in {"submitted", "succeeded"}:
                doc.pop("error", None)
        doc["result"] = result
        self.update_run(row, status, doc)

    def defer(self, row, doc):
        with self.db:
            self.db.execute(
                "UPDATE hook_runs SET attempts=attempts-1,started=NULL WHERE id=?", (row["id"],)
            )
        self.update_run(row, "pending", doc, delay=2)

    def sms_rate_limited(self):
        return (
            self.db.execute(
                "SELECT count(*) FROM hook_runs WHERE kind='sms' AND started>=?",
                (time() - SMS_RATE_WINDOW,),
            ).fetchone()[0]
            >= SMS_RATE_LIMIT
        )

    async def execute(self, row, action, event):
        if action["type"] == "sms":
            rendered = self.render_action(action, event)
            if not PHONE.fullmatch(rendered["number"]) or not rendered["text"]:
                raise OperationError(
                    "SMS cannot be rendered for this event", "INVALID_MESSAGE", 422
                )
            result = await self.act(
                rendered["modem"],
                "hook",
                "send_sms",
                number=rendered["number"],
                text=rendered["text"],
            )
            return result["status"], {"messageId": result["id"]}
        if action["type"] == "decline":
            await self.act(event["modem"], "hook", "decline", call_id=event["callId"])
            return "succeeded", {"outcome": "declined"}
        destination = self.row("destinations", action["destination"])
        if not destination["enabled"]:
            return "cancelled", None
        config = json.loads(destination["document"]) | self.destination_credentials(
            destination["id"]
        )
        if event["type"] == "call.incoming":
            decline = self.db.execute(
                """SELECT status FROM hook_runs WHERE event_id=? AND kind='decline'
                   ORDER BY attempts DESC,id LIMIT 1""",
                (event["id"],),
            ).fetchone()
            if decline:
                event = event | {
                    "outcome": "declined" if decline["status"] == "succeeded" else decline["status"]
                }
        payload, content_type = http_body(action, event, config["method"])
        if payload is not None and len(payload) > HTTP_PAYLOAD_LIMIT:
            raise OperationError("HTTP payload is too large", "HOOK_BODY_LIMIT")
        response = await hook_http.deliver(config, payload, content_type, str(row["id"]))
        if response is None:
            raise OperationError("HTTP destination address is prohibited", "HOOK_ADDRESS")
        if 200 <= response < 300:
            return "succeeded", {"httpStatus": response}
        if response in {408, 425, 429} or response >= 500:
            return "retry", {"httpStatus": response}
        return "failed", {"httpStatus": response}


def sms_event(message, timestamp):
    return {
        "version": 1,
        "id": f"sms:{message['id']}",
        "type": "sms.received",
        "modem": message["modem"],
        "time": timestamp,
        "peer": message["peer"],
        "text": message["body"],
        "messageId": message["id"],
    }


def call_event(call, trigger):
    return {
        "version": 1,
        "id": f"{trigger}:{call['id']}",
        "type": trigger,
        "modem": call["modem"],
        "time": (call["ended"] or call["time"]) if trigger == "call.ended" else call["time"],
        "peer": call["peer"],
        "callerId": call["callerId"],
        "outcome": call["outcome"],
        "callId": call["id"],
    }


def http_body(action, event, method):
    if method in {"GET", "HEAD"}:
        return None, ""
    if action["textBody"] is not None:
        return render(action["textBody"], event).encode(), "text/plain; charset=utf-8"
    if action["jsonBody"] is not None:
        body = {key: render(value, event) for key, value in action["jsonBody"].items()}
    elif action["fields"] is not None:
        body = {key: event.get(key) for key in action["fields"]}
    else:
        body = event
    return json.dumps(body, ensure_ascii=True).encode(), "application/json"

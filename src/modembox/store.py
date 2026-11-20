import hashlib
import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .sms import SmsPart

SMS_PART_WINDOW_SECONDS = 600


class Store:
    def __init__(self, directory: Path):
        self.on_sms = None
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / "pin.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if path.stat().st_mode & 0o077:
                raise ValueError("pin.key permissions must be 0600")
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
        self.cipher = Fernet(path.read_bytes())
        # The ASGI application can be constructed before its event loop starts.
        self.db = sqlite3.connect(directory / "modembox.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS pins (
                sim TEXT PRIMARY KEY, value BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, modem TEXT NOT NULL,
                direction TEXT NOT NULL, peer TEXT NOT NULL,
                body TEXT NOT NULL, status TEXT NOT NULL,
                created TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                fingerprint TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS sms_parts (
                fingerprint TEXT PRIMARY KEY, group_key TEXT NOT NULL,
                scope TEXT NOT NULL, peer TEXT NOT NULL,
                reference TEXT NOT NULL, total INTEGER NOT NULL, coding INTEGER NOT NULL,
                stamp INTEGER NOT NULL, sequence INTEGER NOT NULL, body TEXT NOT NULL,
                message_id INTEGER,
                UNIQUE (group_key, sequence)
            );
            CREATE INDEX IF NOT EXISTS pending_sms_parts
                ON sms_parts (scope, peer, reference, total, coding)
                WHERE message_id IS NULL;
            CREATE TABLE IF NOT EXISTS sms_outgoing_parts (
                message_id INTEGER NOT NULL, part INTEGER NOT NULL, status TEXT NOT NULL,
                PRIMARY KEY (message_id, part)
            );
        """)
        with self.db:
            self.db.execute("UPDATE sms_outgoing_parts SET status='unknown' WHERE status='sending'")
            self.db.execute("UPDATE messages SET status='unknown' WHERE status='sending'")

    def get_pin(self, sim: str) -> str | None:
        row = self.db.execute("SELECT value FROM pins WHERE sim=?", (sim,)).fetchone()
        if row is None:
            return None
        try:
            return self.cipher.decrypt(row["value"]).decode("ascii")
        except InvalidToken as error:
            raise ValueError("saved SIM PIN cannot be decrypted") from error

    def save_pin(self, sim: str, pin: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO pins (sim, value) VALUES (?, ?)",
                (sim, self.cipher.encrypt(pin.encode("ascii"))),
            )

    def forget_pin(self, sim: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM pins WHERE sim=?", (sim,))

    def add_message(self, modem, direction, peer, body, status, raw=None, *, timestamp=None):
        fingerprint = hashlib.sha256(raw).hexdigest() if raw else None
        with self.db:
            result = self.db.execute(
                """
                INSERT INTO messages (modem,direction,peer,body,status,fingerprint)
                VALUES (?,?,?,?,?,?) ON CONFLICT(fingerprint) DO NOTHING
                """,
                (modem, direction, peer, body, status, fingerprint),
            )
            if result.rowcount and direction == "incoming" and self.on_sms:
                self.on_sms(self.message(result.lastrowid), timestamp)
        return self.message(result.lastrowid) if result.rowcount else None

    def update_message(self, message_id: int, status: str):
        with self.db:
            self.db.execute("UPDATE messages SET status=? WHERE id=?", (status, message_id))
        return self.message(message_id)

    def add_sms_part(self, modem: str, scope: str, peer: str, body: str, part: SmsPart, raw: bytes):
        fingerprint = hashlib.sha256(scope.encode() + raw).hexdigest()
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM sms_parts WHERE fingerprint = ?", (fingerprint,)
            ).fetchone():
                return None
            candidates = self.db.execute(
                """
                SELECT group_key FROM sms_parts
                WHERE scope = ? AND peer = ? AND reference = ? AND total = ? AND coding = ?
                    AND message_id IS NULL
                GROUP BY group_key
                HAVING MIN(stamp) >= ? AND MAX(stamp) <= ? AND SUM(sequence = ?) = 0
                LIMIT 2
                """,
                (
                    scope,
                    peer,
                    part.reference,
                    part.total,
                    part.coding,
                    part.timestamp - SMS_PART_WINDOW_SECONDS,
                    part.timestamp + SMS_PART_WINDOW_SECONDS,
                    part.sequence,
                ),
            ).fetchall()
            group_key = candidates[0]["group_key"] if len(candidates) == 1 else fingerprint
            self.db.execute(
                """
                INSERT INTO sms_parts
                    (fingerprint, group_key, scope, peer, reference, total, coding,
                     stamp, sequence, body)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint,
                    group_key,
                    scope,
                    peer,
                    part.reference,
                    part.total,
                    part.coding,
                    part.timestamp,
                    part.sequence,
                    body,
                ),
            )
            parts = self.db.execute(
                """
                SELECT part.body, part.stamp, legacy.id AS legacy_id
                FROM sms_parts AS part
                LEFT JOIN messages AS legacy ON legacy.fingerprint = part.fingerprint
                WHERE part.group_key = ?
                ORDER BY part.sequence
                """,
                (group_key,),
            ).fetchall()
            if len(parts) != part.total:
                return None
            result = self.db.execute(
                """
                INSERT INTO messages (modem, direction, peer, body, status)
                VALUES (?, 'incoming', ?, ?, 'received')
                """,
                (modem, peer, "".join(row["body"] for row in parts)),
            )
            message_id = result.lastrowid
            self.db.execute(
                "UPDATE sms_parts SET message_id = ? WHERE group_key = ?",
                (message_id, group_key),
            )
            message = self.message(message_id)
            if self.on_sms and not any(row["legacy_id"] is not None for row in parts):
                self.on_sms(message, min(row["stamp"] for row in parts))
        replaces = [row["legacy_id"] for row in parts if row["legacy_id"] is not None]
        if replaces:
            message["replaces"] = replaces
        return message

    def message(self, message_id: int):
        row = self.db.execute(
            "SELECT id,modem,direction,peer,body,status,created FROM messages WHERE id=?",
            (message_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result["direction"] == "outgoing" and (parts := self.outgoing_parts(message_id)):
            result["parts"] = parts
        return result

    def add_outgoing(self, modem, number, text, count):
        with self.db:
            result = self.db.execute(
                """INSERT INTO messages (modem,direction,peer,body,status)
                   VALUES (?,'outgoing',?,?,'sending')""",
                (modem, number, text),
            )
            if count > 1:
                self.db.executemany(
                    """INSERT INTO sms_outgoing_parts(message_id,part,status)
                       VALUES (?,?,'pending')""",
                    [(result.lastrowid, index) for index in range(1, count + 1)],
                )
        return self.message(result.lastrowid)

    def outgoing_part(self, message_id, part, status, message_status=None):
        with self.db:
            self.db.execute(
                "UPDATE sms_outgoing_parts SET status=? WHERE message_id=? AND part=?",
                (status, message_id, part),
            )
            if message_status:
                self.db.execute(
                    "UPDATE messages SET status=? WHERE id=?", (message_status, message_id)
                )
        return self.message(message_id)

    def outgoing_parts(self, message_id):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT part,status FROM sms_outgoing_parts WHERE message_id=? ORDER BY part",
                (message_id,),
            )
        ]

    def messages(self, modem: str | None = None, before: int | None = None, limit: int = 100):
        rows = self.db.execute(
            """
            SELECT id,modem,direction,peer,body,status,created FROM messages
            WHERE (? IS NULL OR modem = ?) AND (? IS NULL OR id < ?)
                AND NOT EXISTS (
                    SELECT 1 FROM sms_parts
                    WHERE sms_parts.fingerprint = messages.fingerprint AND message_id IS NOT NULL
                )
            ORDER BY id DESC LIMIT ?
            """,
            (modem, modem, before, before, limit),
        )
        return [dict(row) for row in rows]

import hashlib
import os
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Store:
    def __init__(self, directory: Path):
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
        """)
        with self.db:
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

    def add_message(self, modem, direction, peer, body, status, raw=None):
        fingerprint = hashlib.sha256(raw).hexdigest() if raw else None
        with self.db:
            result = self.db.execute(
                """
                INSERT INTO messages (modem,direction,peer,body,status,fingerprint)
                VALUES (?,?,?,?,?,?) ON CONFLICT(fingerprint) DO NOTHING
                """,
                (modem, direction, peer, body, status, fingerprint),
            )
        return self.message(result.lastrowid) if result.rowcount else None

    def update_message(self, message_id: int, status: str):
        with self.db:
            self.db.execute("UPDATE messages SET status=? WHERE id=?", (status, message_id))
        return self.message(message_id)

    def message(self, message_id: int):
        row = self.db.execute(
            "SELECT id,modem,direction,peer,body,status,created FROM messages WHERE id=?",
            (message_id,),
        ).fetchone()
        return dict(row) if row else None

    def messages(self, modem: str | None = None, before: int | None = None, limit: int = 100):
        rows = self.db.execute(
            """
            SELECT id,modem,direction,peer,body,status,created FROM messages
            WHERE (? IS NULL OR modem = ?) AND (? IS NULL OR id < ?)
            ORDER BY id DESC LIMIT ?
            """,
            (modem, modem, before, before, limit),
        )
        return [dict(row) for row in rows]

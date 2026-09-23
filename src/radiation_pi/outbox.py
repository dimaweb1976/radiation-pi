"""Durable FIFO queue for exact HTTP request bodies."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutboxItem:
    id: int
    kind: str
    message_id: str
    body: bytes
    state: str
    attempts: int
    next_attempt_at: float
    last_status: int | None


class Outbox:
    def __init__(self, path: Path, *, station_id: int, server_url: str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)
        """)
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK (kind IN ('measurement', 'heartbeat')),
                message_id TEXT NOT NULL UNIQUE,
                body BLOB NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'blocked')),
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                last_status INTEGER,
                created_at REAL NOT NULL
            )
        """)
        self.connection.commit()
        try:
            self._bind("station_id", str(station_id))
            self._bind("server_url", server_url)
        except Exception:
            self.close()
            raise

    def _bind(self, key: str, value: str) -> None:
        with self.connection:
            existing = self.connection.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    "INSERT INTO metadata (key, value) VALUES (?, ?)", (key, value)
                )
            elif existing["value"] != value:
                raise ValueError(f"outbox belongs to a different {key}; use a separate outbox_path")

    def enqueue(self, kind: str, message_id: str, body: bytes) -> OutboxItem:
        if kind not in {"measurement", "heartbeat"}:
            raise ValueError("unknown message kind")
        if not message_id or not isinstance(body, bytes):
            raise ValueError("message_id and serialized body are required")
        with self.connection:
            existing = self.connection.execute(
                "SELECT * FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if existing is not None:
                item = _item(existing)
                if item.kind != kind or item.body != body:
                    raise ValueError("message_id already exists with different data")
                return item
            cursor = self.connection.execute(
                "INSERT INTO messages (kind, message_id, body, created_at) VALUES (?, ?, ?, ?)",
                (kind, message_id, body, time.time()),
            )
            return self.get_by_id(cursor.lastrowid)

    def get_by_id(self, item_id: int) -> OutboxItem | None:
        row = self.connection.execute("SELECT * FROM messages WHERE id = ?", (item_id,)).fetchone()
        return _item(row) if row is not None else None

    def pending_head(self) -> OutboxItem | None:
        row = self.connection.execute(
            "SELECT * FROM messages WHERE state = 'pending' ORDER BY id LIMIT 1"
        ).fetchone()
        return _item(row) if row is not None else None

    def acknowledge(self, item_id: int) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM messages WHERE id = ?", (item_id,))

    def schedule_retry(self, item: OutboxItem, *, now: float, status: int | None) -> float:
        delay = min(300, 2 ** min(item.attempts, 9))
        retry_at = now + delay
        with self.connection:
            self.connection.execute(
                "UPDATE messages SET attempts = attempts + 1, next_attempt_at = ?, last_status = ? WHERE id = ?",
                (retry_at, status, item.id),
            )
        return retry_at

    def block(self, item_id: int, status: int | None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE messages SET state = 'blocked', attempts = attempts + 1, last_status = ? WHERE id = ?",
                (status, item_id),
            )

    def retry_blocked(self, message_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE messages SET state = 'pending', next_attempt_at = 0 WHERE message_id = ? AND state = 'blocked'",
                (message_id,),
            )
        return cursor.rowcount == 1

    def summary(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id, kind, message_id, state, attempts, next_attempt_at, last_status "
            "FROM messages ORDER BY id"
        )
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Outbox:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _item(row: sqlite3.Row) -> OutboxItem:
    return OutboxItem(
        id=row["id"], kind=row["kind"], message_id=row["message_id"],
        body=row["body"], state=row["state"], attempts=row["attempts"],
        next_attempt_at=row["next_attempt_at"], last_status=row["last_status"],
    )

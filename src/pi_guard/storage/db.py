"""SQLite persistence for state that must survive restarts.

Every query is small and indexed, so calls run synchronously on the event loop;
WAL mode lets the CLI read while the service writes.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import secure_file

_SCHEMA = [
    # version 1
    """
    CREATE TABLE kv (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE users (
        provider TEXT NOT NULL,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        role TEXT NOT NULL,
        language TEXT,
        layout TEXT NOT NULL DEFAULT 'simple',
        notifications INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        PRIMARY KEY (provider, user_id)
    );
    CREATE TABLE invites (
        code TEXT PRIMARY KEY,
        role TEXT NOT NULL,
        created_by TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        type TEXT NOT NULL,
        camera TEXT,
        data TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX events_ts ON events (ts);
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY,
        ts TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX audit_ts ON audit (ts);
    """,
]


@dataclass(frozen=True)
class StoredEvent:
    id: int
    ts: datetime
    type: str
    camera: str | None
    data: dict[str, Any]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            path, timeout=10, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        secure_file(path)
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(_SCHEMA[version:], start=version + 1):
            self._conn.executescript(f"BEGIN; {script}; PRAGMA user_version={number}; COMMIT;")

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def close(self) -> None:
        self._conn.close()

    def check(self) -> str:
        return str(self._conn.execute("PRAGMA quick_check").fetchone()[0])

    # key/value state

    def get(self, key: str) -> Any:
        row = self.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def get_with_time(self, key: str) -> tuple[Any, str | None]:
        row = self.execute("SELECT value, updated_at FROM kv WHERE key = ?", (key,)).fetchone()
        return (json.loads(row["value"]), row["updated_at"]) if row else (None, None)

    def set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) "
            "DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value), utcnow().isoformat()),
        )

    # event history

    def add_event(self, ts: datetime, type_: str, camera: str | None, data: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO events (ts, type, camera, data) VALUES (?, ?, ?, ?)",
            (ts.isoformat(), type_, camera, json.dumps(data, default=str)),
        )

    def recent_events(self, limit: int = 10, types: list[str] | None = None) -> list[StoredEvent]:
        sql, params = "SELECT * FROM events", []
        if types:
            sql += f" WHERE type IN ({','.join('?' * len(types))})"
            params = list(types)
        rows = self.execute(sql + " ORDER BY ts DESC LIMIT ?", (*params, limit)).fetchall()
        return [
            StoredEvent(
                r["id"],
                datetime.fromisoformat(r["ts"]),
                r["type"],
                r["camera"],
                json.loads(r["data"]),
            )
            for r in rows
        ]

    def events_between(
        self, start: datetime, end: datetime, types: list[str], camera: str
    ) -> list[datetime]:
        # Only "?" placeholders are interpolated; values are always bound parameters.
        rows = self.execute(
            f"SELECT ts FROM events WHERE camera = ? AND ts BETWEEN ? AND ? "  # noqa: S608
            f"AND type IN ({','.join('?' * len(types))})",
            (camera, start.isoformat(), end.isoformat(), *types),
        ).fetchall()
        return [datetime.fromisoformat(r["ts"]) for r in rows]

    def prune(self, before: datetime) -> int:
        cutoff = before.isoformat()
        removed = self.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount
        removed += self.execute("DELETE FROM audit WHERE ts < ?", (cutoff,)).rowcount
        self.execute("DELETE FROM invites WHERE expires_at < ?", (utcnow().isoformat(),))
        self.execute("DELETE FROM kv WHERE key LIKE 'trust:%' AND updated_at < ?", (cutoff,))
        return removed

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        self.execute(
            "INSERT INTO audit (ts, actor, action, detail) VALUES (?, ?, ?, ?)",
            (utcnow().isoformat(), actor, action, detail),
        )

"""Computers connected with the pi-guard agent (see `agent/`).

The agent connects to the dashboard, like an frp client to its server, so the computer opens no
port. Each has its own token, stored as a hash. Only the commands of the agent exist; power
commands need two confirmations by the same person, as remote commands do.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from pi_guard.addons.remote import CONFIRM_SECONDS, MIN_SECONDS_BETWEEN_STEPS, ConfirmationError
from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

REQUEST_SECONDS = 20
POWER_COMMANDS = ("shutdown", "reboot", "suspend", "lock")
DOCKER_ACTIONS = ("start", "stop", "restart")
_TABLE = """
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, token_hash TEXT NOT NULL,
    created TEXT NOT NULL, last_seen TEXT, info TEXT NOT NULL DEFAULT '{}');
"""


class AgentError(Exception):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class Connection:
    agent_id: int
    send: Any  # async callable taking a str
    pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict)


@dataclass
class _Pending:
    agent_id: int
    command: str
    actor: str
    step: int
    created: float


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AgentHub:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._ids = itertools.count(1)
        self.connections: dict[int, Connection] = {}
        self._confirmations: dict[str, _Pending] = {}
        db.executescript(_TABLE)

    # --- agents -------------------------------------------------------------------------

    def create(self, name: str) -> tuple[int, str]:
        name = " ".join(name.split())
        if not 1 <= len(name) <= 40:
            raise AgentError("web.ag_bad_name")
        if self._db.execute("SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone():
            raise AgentError("web.ag_name_taken")
        token = f"pga_{secrets.token_urlsafe(32)}"
        cursor = self._db.execute(
            "INSERT INTO agents (name, token_hash, created) VALUES (?, ?, ?)",
            (name, _digest(token), utcnow().isoformat()),
        )
        return int(cursor.lastrowid or 0), token

    def delete(self, agent_id: int) -> bool:
        return self._db.execute("DELETE FROM agents WHERE id = ?", (agent_id,)).rowcount > 0

    def authenticate(self, token: str) -> int | None:
        row = self._db.execute("SELECT id FROM agents WHERE token_hash = ?",
                               (_digest(token),)).fetchone()  # fmt: skip
        return int(row["id"]) if row else None

    def list(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, name, created, last_seen, info FROM agents ORDER BY name"
        ).fetchall()
        return [{**dict(r), "info": json.loads(r["info"]), "online": r["id"] in self.connections}
                for r in rows]  # fmt: skip

    def name(self, agent_id: int) -> str | None:
        row = self._db.execute("SELECT name FROM agents WHERE id = ?", (agent_id,)).fetchone()
        return str(row["name"]) if row else None

    def find(self, text: str) -> int | None:
        """The agent mentioned in a sentence."""
        return next((a["id"] for a in self.list() if a["name"].casefold() in text), None)

    # --- connection ---------------------------------------------------------------------

    def attach(self, connection: Connection) -> None:
        self.connections[connection.agent_id] = connection

    def detach(self, connection: Connection) -> None:
        if self.connections.get(connection.agent_id) is connection:
            del self.connections[connection.agent_id]
        for future in connection.pending.values():
            if not future.done():
                future.set_exception(AgentError("web.ag_offline"))

    def received(self, connection: Connection, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return
        if message.get("type") == "status" and isinstance(message.get("data"), dict):
            self._db.execute("UPDATE agents SET info = ?, last_seen = ? WHERE id = ?",
                             (json.dumps(message["data"])[:8000], utcnow().isoformat(),
                              connection.agent_id))  # fmt: skip
        elif message.get("type") == "reply":
            future = connection.pending.pop(message.get("id"), None)
            if future is not None and not future.done():
                if message.get("ok"):
                    future.set_result(message.get("data"))
                else:
                    future.set_exception(RuntimeError(str(message.get("data"))[:200]))

    async def request(self, agent_id: int, **body: Any) -> Any:
        connection = self.connections.get(agent_id)
        if connection is None:
            raise AgentError("web.ag_offline")
        request_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        connection.pending[request_id] = future
        await connection.send(json.dumps({"id": request_id, **body}))
        try:
            return await asyncio.wait_for(future, REQUEST_SECONDS)
        except TimeoutError:
            raise AgentError("web.ag_timeout") from None
        finally:
            connection.pending.pop(request_id, None)

    async def docker(self, agent_id: int, action: str, container: str = "") -> Any:
        return await self.request(agent_id, cmd="docker", action=action, container=container)

    # --- two confirmations --------------------------------------------------------------

    def _expire(self) -> None:
        limit = time.monotonic() - CONFIRM_SECONDS
        self._confirmations = {k: p for k, p in self._confirmations.items() if p.created > limit}

    def begin(self, agent_id: int, command: str, actor: str) -> str:
        self._expire()
        if command not in POWER_COMMANDS or agent_id not in self.connections:
            raise ConfirmationError("web.ag_offline")
        return self._issue(agent_id, command, actor, 1)

    def _issue(self, agent_id: int, command: str, actor: str, step: int) -> str:
        token = secrets.token_hex(6)
        self._confirmations[token] = _Pending(agent_id, command, actor, step, time.monotonic())
        return token

    def confirm(self, token: str, actor: str) -> tuple[int, str, str | None]:
        """(agent, command, token for the second confirmation or None when fully confirmed)."""
        self._expire()
        pending = self._confirmations.pop(token, None)
        if pending is None or pending.actor != actor:
            raise ConfirmationError("remote.expired")
        if time.monotonic() - pending.created < MIN_SECONDS_BETWEEN_STEPS:
            raise ConfirmationError("remote.too_fast")
        if pending.step == 1:
            following = self._issue(pending.agent_id, pending.command, actor, 2)
            return pending.agent_id, pending.command, following
        return pending.agent_id, pending.command, None

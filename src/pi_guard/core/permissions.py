"""Who may talk to pi-guard, and with which role.

Users listed in the environment are authoritative and cannot be removed through
chat. Further users join with a single-use invite code created by an admin.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Literal

from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

Layout = Literal["simple", "advanced"]


class Role(StrEnum):
    ADMIN = "admin"
    VIEWER = "viewer"

    def allows(self, required: Role) -> bool:
        return self is Role.ADMIN or required is Role.VIEWER


@dataclass(frozen=True)
class User:
    provider: str
    user_id: str
    role: Role
    name: str = ""
    language: str | None = None
    layout: Layout = "simple"
    notifications: bool = True
    configured: bool = False

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.user_id}"


class UserStore:
    def __init__(self, db: Database, configured: dict[str, dict[str, Role]]) -> None:
        """`configured` maps provider -> {user_id: role} from the environment."""
        self._db = db
        self._configured = configured

    def get(self, provider: str, user_id: str) -> User | None:
        row = self._db.execute(
            "SELECT * FROM users WHERE provider = ? AND user_id = ?", (provider, user_id)
        ).fetchone()
        configured_role = self._configured.get(provider, {}).get(user_id)
        if row is None and configured_role is None:
            return None
        user = User(provider, user_id, configured_role or Role.VIEWER, configured=True)
        if row is not None:
            user = replace(
                user,
                role=configured_role or Role(row["role"]),
                name=row["name"],
                language=row["language"],
                layout=row["layout"],
                notifications=bool(row["notifications"]),
                configured=configured_role is not None,
            )
        return user

    def save(self, user: User) -> None:
        self._db.execute(
            "INSERT INTO users (provider, user_id, name, role, language, layout, notifications, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(provider, user_id) DO UPDATE "
            "SET name = excluded.name, role = excluded.role, language = excluded.language, "
            "layout = excluded.layout, notifications = excluded.notifications",
            (
                user.provider,
                user.user_id,
                user.name,
                user.role.value,
                user.language,
                user.layout,
                int(user.notifications),
                utcnow().isoformat(),
            ),
        )

    def all(self, provider: str | None = None) -> list[User]:
        keys = {(p, uid) for p, ids in self._configured.items() for uid in ids}
        rows = self._db.execute("SELECT provider, user_id FROM users").fetchall()
        keys |= {(r["provider"], r["user_id"]) for r in rows}
        users = [self.get(p, uid) for p, uid in sorted(keys) if provider in (None, p)]
        return [u for u in users if u is not None]

    def remove(self, provider: str, user_id: str) -> bool:
        if user_id in self._configured.get(provider, {}):
            return False
        return (
            self._db.execute(
                "DELETE FROM users WHERE provider = ? AND user_id = ?", (provider, user_id)
            ).rowcount
            > 0
        )

    def create_invite(self, role: Role, created_by: str, ttl: timedelta) -> tuple[str, datetime]:
        code = secrets.token_urlsafe(9)
        expires = utcnow() + ttl
        self._db.execute(
            "INSERT INTO invites (code, role, created_by, expires_at) VALUES (?, ?, ?, ?)",
            (code, role.value, created_by, expires.isoformat()),
        )
        self._db.audit(created_by, "invite.create", role.value)
        return code, expires

    def redeem(self, code: str, provider: str, user_id: str, name: str) -> User | None:
        now = utcnow().isoformat()
        rows = self._db.execute(
            "SELECT code, role FROM invites WHERE expires_at > ?", (now,)
        ).fetchall()
        match = next((r for r in rows if hmac.compare_digest(r["code"], code)), None)
        if match is None:
            return None
        self._db.execute("DELETE FROM invites WHERE code = ?", (match["code"],))
        existing = self.get(provider, user_id)
        user = existing or User(provider, user_id, Role(match["role"]), name=name)
        self.save(replace(user, name=name or user.name))
        self._db.audit(f"{provider}:{user_id}", "invite.redeem", match["role"])
        return self.get(provider, user_id)

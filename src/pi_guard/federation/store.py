"""Linked households, open invitations and this Pi's own federation key."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pi_guard.core.timeutil import utcnow
from pi_guard.federation.protocol import EXTRAS, ROLES, b64, clean_name, public_bytes, unb64
from pi_guard.storage.db import Database

KEY = "federation.key"
INVITE_HOURS = 24
MAX_LINKS = 20
MAX_INVITES = 10
SIDES = ("helper", "helped")  # this Pi helps them / this Pi is helped by them


@dataclass
class Link:
    id: int
    name: str
    side: str
    peer_key: bytes
    url: str = ""
    pin: str = ""
    role: str = "viewer"
    extras: list[str] = field(default_factory=list)
    created: str = ""
    last_seen: str = ""

    def as_dict(self) -> dict[str, Any]:
        from pi_guard.federation.protocol import fingerprint

        return {"id": self.id, "name": self.name, "side": self.side, "role": self.role,
                "extras": list(self.extras), "created": self.created,
                "last_seen": self.last_seen or None, "fingerprint": fingerprint(self.peer_key),
                "host": self.url.split("/")[2] if self.url else None}  # fmt: skip


class FederationError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class FederationStore:
    def __init__(self, db: Database, secret_key: bytes) -> None:
        self._db = db
        self._seal_key = hashlib.sha256(b"pi-guard federation key\0" + secret_key).digest()
        self._identity: Ed25519PrivateKey | None = None

    # --- own key: made once, kept sealed like other secrets ---------------------------------

    def identity(self) -> Ed25519PrivateKey:
        if self._identity is not None:
            return self._identity
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aead = AESGCM(self._seal_key)
        sealed = self._db.get(KEY)
        if isinstance(sealed, str):
            raw = base64.b64decode(sealed)
            self._identity = Ed25519PrivateKey.from_private_bytes(
                aead.decrypt(raw[:12], raw[12:], b"pi-guard-federation")
            )
        else:
            key = Ed25519PrivateKey.generate()
            from cryptography.hazmat.primitives import serialization

            private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                        serialization.NoEncryption())  # fmt: skip
            nonce = secrets.token_bytes(12)
            sealed_key = nonce + aead.encrypt(nonce, private, b"pi-guard-federation")
            self._db.set(KEY, base64.b64encode(sealed_key).decode())
            self._identity = key
        return self._identity

    def public(self) -> bytes:
        return public_bytes(self.identity())

    # --- invitations (helper side) ----------------------------------------------------------

    def invite(self) -> str:
        """A one-time token; only its hash is kept."""
        now = utcnow()
        self._db.execute("DELETE FROM federation_invites WHERE expires < ?", (now.isoformat(),))
        count = self._db.execute("SELECT COUNT(*) FROM federation_invites").fetchone()[0]
        if count >= MAX_INVITES:
            raise FederationError("web.fed_too_many_invites")
        token = secrets.token_urlsafe(24)
        expires = now + timedelta(hours=INVITE_HOURS)
        self._db.execute("INSERT INTO federation_invites (token_hash, expires) VALUES (?, ?)",
                         (_digest(token), expires.isoformat()))  # fmt: skip
        return token

    def redeem(self, token: str) -> bool:
        """True once for a valid token; it is gone afterwards."""
        cursor = self._db.execute(
            "DELETE FROM federation_invites WHERE token_hash = ? AND expires >= ?",
            (_digest(token), utcnow().isoformat()),
        )
        return bool(cursor.rowcount)

    def clear_invites(self) -> None:
        self._db.execute("DELETE FROM federation_invites")

    # --- links ------------------------------------------------------------------------------

    def links(self, side: str | None = None) -> list[Link]:
        rows = self._db.execute("SELECT * FROM federation_links ORDER BY name COLLATE NOCASE")
        found = [self._row(r) for r in rows.fetchall()]
        return [link for link in found if side is None or link.side == side]

    def get(self, link_id: int) -> Link | None:
        row = self._db.execute("SELECT * FROM federation_links WHERE id = ?", (link_id,)).fetchone()
        return self._row(row) if row else None

    def by_key(self, side: str, key: bytes) -> Link | None:
        row = self._db.execute("SELECT * FROM federation_links WHERE side = ? AND peer_key = ?",
                               (side, b64(key))).fetchone()  # fmt: skip
        return self._row(row) if row else None

    def add(self, side: str, name: str, key: bytes, *, url: str = "", pin: str = "",
            role: str = "viewer", extras: list[str] | None = None) -> int:  # fmt: skip
        if side not in SIDES or len(key) != 32:
            raise FederationError("web.error")
        if key == self.public():
            raise FederationError("web.fed_self")
        if self.by_key(side, key) is not None:
            raise FederationError("web.fed_known")
        if len(self.links()) >= MAX_LINKS:
            raise FederationError("web.fed_too_many")
        role, extras = self._grants(role, extras or [])
        cursor = self._db.execute(
            "INSERT INTO federation_links (name, side, peer_key, url, pin, role, extras, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (clean_name(name) or "pi-guard", side, b64(key), url, pin, role, json.dumps(extras),
             utcnow().isoformat()),
        )  # fmt: skip
        return int(cursor.lastrowid or 0)

    def set_grants(self, link_id: int, role: str, extras: list[str]) -> bool:
        role, extras = self._grants(role, extras)
        cursor = self._db.execute(
            "UPDATE federation_links SET role = ?, extras = ? WHERE id = ? AND side = 'helped'",
            (role, json.dumps(extras), link_id),
        )
        return bool(cursor.rowcount)

    def rename(self, link_id: int, name: str) -> bool:
        name = clean_name(name)
        if not name:
            raise FederationError("web.fed_bad_name")
        cursor = self._db.execute("UPDATE federation_links SET name = ? WHERE id = ?",
                                  (name, link_id))  # fmt: skip
        return bool(cursor.rowcount)

    def seen(self, link_id: int, when: datetime | None = None) -> None:
        self._db.execute("UPDATE federation_links SET last_seen = ? WHERE id = ?",
                         ((when or utcnow()).isoformat(), link_id))  # fmt: skip

    def remove(self, link_id: int) -> Link | None:
        link = self.get(link_id)
        if link is not None:
            self._db.execute("DELETE FROM federation_links WHERE id = ?", (link_id,))
        return link

    @staticmethod
    def _grants(role: str, extras: list[str]) -> tuple[str, list[str]]:
        if role not in ROLES:
            raise FederationError("web.fed_bad_role")
        return role, [e for e in EXTRAS if e in extras]

    @staticmethod
    def _row(row: Any) -> Link:
        try:
            extras = [e for e in json.loads(row["extras"] or "[]") if e in EXTRAS]
        except ValueError:
            extras = []
        role = row["role"] if row["role"] in ROLES else "viewer"
        return Link(int(row["id"]), row["name"], row["side"], unb64(row["peer_key"]),
                    row["url"] or "", row["pin"] or "", role, extras, row["created"] or "",
                    row["last_seen"] or "")  # fmt: skip

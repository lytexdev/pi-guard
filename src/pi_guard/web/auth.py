"""Dashboard authentication: passwords, key files, TOTP and sessions.

Everything here uses the standard library: scrypt for password hashes,
SHA-256 for key files and session IDs (both are 256-bit random values, so a
fast hash is sufficient), HMAC-SHA1 for RFC 6238 TOTP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import quote

from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SESSION_IDLE = timedelta(hours=12)
SESSION_MAX = timedelta(days=7)
PENDING_TOTP = timedelta(minutes=5)
KEYFILE_PREFIX = "pi-guard-key-v1:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_sessions (
    id_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL,
    created TEXT NOT NULL, last_seen TEXT NOT NULL, ip TEXT NOT NULL, agent TEXT NOT NULL,
    pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS web_keyfiles (
    name TEXT PRIMARY KEY, hash TEXT NOT NULL, created TEXT NOT NULL, last_used TEXT);
"""


# --- passwords -----------------------------------------------------------------------


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    b64 = base64.b64encode
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# --- TOTP (RFC 6238) ---------------------------------------------------------------------


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_uri(secret: str, account: str, issuer: str = "pi-guard") -> str:
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")  # fmt: skip


def totp_code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{value % 1_000_000:06d}"


def totp_match(secret: str, code: str, now: float | None = None, window: int = 1) -> int | None:
    """The matching time step, or None. Callers reject steps already used (replay)."""
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    counter = int((time.time() if now is None else now) // 30)
    for step in range(counter - window, counter + window + 1):
        if hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


# --- key files and sessions -----------------------------------------------------------


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    username: str
    csrf: str
    pending: bool


class AuthStore:
    def __init__(self, db: Database) -> None:
        self._db = db
        db.executescript(_SCHEMA)

    # key files: the file content is the credential; only its hash is stored

    def create_keyfile(self, name: str) -> str:
        secret = KEYFILE_PREFIX + secrets.token_urlsafe(32)
        self._db.execute(
            "INSERT OR REPLACE INTO web_keyfiles (name, hash, created) VALUES (?, ?, ?)",
            (name, _digest(secret), utcnow().isoformat()),
        )
        return secret

    def check_keyfile(self, content: str) -> str | None:
        content = content.strip()
        if not content.startswith(KEYFILE_PREFIX):
            return None
        row = self._db.execute(
            "SELECT name FROM web_keyfiles WHERE hash = ?", (_digest(content),)
        ).fetchone()
        if row is None:
            return None
        self._db.execute("UPDATE web_keyfiles SET last_used = ? WHERE name = ?",
                         (utcnow().isoformat(), row["name"]))  # fmt: skip
        return str(row["name"])

    def keyfiles(self) -> list[dict[str, str | None]]:
        rows = self._db.execute("SELECT name, created, last_used FROM web_keyfiles ORDER BY name")
        return [dict(r) for r in rows]

    def delete_keyfile(self, name: str) -> bool:
        return self._db.execute("DELETE FROM web_keyfiles WHERE name = ?", (name,)).rowcount > 0

    # sessions

    def create_session(self, username: str, ip: str, agent: str, pending: bool = False) -> str:
        token = secrets.token_urlsafe(32)
        now = utcnow().isoformat()
        self._db.execute(
            "INSERT INTO web_sessions (id_hash, username, csrf, created, last_seen, ip, agent, "
            "pending) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_digest(token), username, secrets.token_urlsafe(24), now, now, ip, agent[:200],
             int(pending)),
        )  # fmt: skip
        return token

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        row = self._db.execute(
            "SELECT * FROM web_sessions WHERE id_hash = ?", (_digest(token),)
        ).fetchone()
        if row is None:
            return None
        now = utcnow()
        created, seen = (
            datetime.fromisoformat(row["created"]),
            datetime.fromisoformat(row["last_seen"]),
        )
        limit = PENDING_TOTP if row["pending"] else SESSION_IDLE
        if now - seen > limit or now - created > SESSION_MAX:
            self.end(token)
            return None
        if now - seen > timedelta(minutes=1):
            self._db.execute("UPDATE web_sessions SET last_seen = ? WHERE id_hash = ?",
                             (now.isoformat(), _digest(token)))  # fmt: skip
        return Session(row["username"], row["csrf"], bool(row["pending"]))

    def promote(self, token: str) -> str:
        """Replace a half-authenticated (TOTP pending) session with a full one."""
        row = self._db.execute(
            "SELECT username, ip, agent FROM web_sessions WHERE id_hash = ?", (_digest(token),)
        ).fetchone()
        self.end(token)
        return self.create_session(row["username"], row["ip"], row["agent"])

    def end(self, token: str) -> None:
        self._db.execute("DELETE FROM web_sessions WHERE id_hash = ?", (_digest(token),))

    def end_all(self) -> int:
        return self._db.execute("DELETE FROM web_sessions").rowcount

    def sessions(self) -> list[dict[str, str]]:
        rows = self._db.execute(
            "SELECT username, created, last_seen, ip, agent FROM web_sessions WHERE pending = 0 "
            "ORDER BY last_seen DESC"
        )
        return [dict(r) for r in rows]

    def totp_step_unused(self, username: str, step: int) -> bool:
        """Remember the last accepted TOTP step per user so a code works only once."""
        key = f"web.totp_step.{username}"
        last = self._db.get(key)
        if last is not None and step <= int(last):
            return False
        self._db.set(key, step)
        return True

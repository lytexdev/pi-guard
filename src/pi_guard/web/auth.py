"""Dashboard authentication: the account, key files, TOTP and sessions.

There is one dashboard account, stored in the database. It starts as
admin / admin123 and must be changed at the first login before anything else
is possible. The dashboard secret key (PI_GUARD_DASHBOARD_SECRET_KEY) is mixed
into password hashes as a pepper and encrypts the TOTP secret, so a copy of
the database alone is not enough to guess the password offline or to generate
login codes. Key files and session IDs are 256-bit random values, so SHA-256
of them is enough.
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
from typing import Any
from urllib.parse import quote

from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SESSION_IDLE = timedelta(hours=12)
SESSION_MAX = timedelta(days=7)
PENDING_TOTP = timedelta(minutes=5)
KEYFILE_PREFIX = "pi-guard-key-v1:"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"  # noqa: S105 - documented first-login default, must be changed
MIN_PASSWORD = 10

_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_sessions (
    id_hash TEXT PRIMARY KEY, username TEXT NOT NULL, csrf TEXT NOT NULL,
    created TEXT NOT NULL, last_seen TEXT NOT NULL, ip TEXT NOT NULL, agent TEXT NOT NULL,
    pending INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS web_keyfiles (
    name TEXT PRIMARY KEY, hash TEXT NOT NULL, created TEXT NOT NULL, last_used TEXT);
CREATE TABLE IF NOT EXISTS web_account (
    id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL, password_hash TEXT NOT NULL,
    must_change INTEGER NOT NULL, totp TEXT, totp_pending TEXT, updated TEXT NOT NULL);
"""


class CredentialError(Exception):
    """`key` names the problem for the user (a translation key)."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# --- passwords -----------------------------------------------------------------------


def _peppered(password: str, pepper: bytes) -> bytes:
    if not pepper:
        return password.encode()
    return hmac.new(pepper, password.encode(), hashlib.sha256).digest()


def hash_password(password: str, pepper: bytes = b"") -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        _peppered(password, pepper), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    b64 = base64.b64encode
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str, pepper: bytes = b"") -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            _peppered(password, pepper),
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
    def __init__(self, db: Database, secret_key: bytes = b"") -> None:
        self._db = db
        db.executescript(_SCHEMA)
        self.configure_keys(secret_key)

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

    # --- the dashboard account ----------------------------------------------------------

    def configure_keys(self, secret_key: bytes) -> None:
        """Derive the password pepper and the TOTP encryption key from the secret key."""
        self._pepper = hashlib.sha256(b"pi-guard password pepper\0" + secret_key).digest()
        self._totp_key = hashlib.sha256(b"pi-guard totp key\0" + secret_key).digest()

    def ensure_account(self) -> None:
        if self._db.execute("SELECT 1 FROM web_account").fetchone() is None:
            self.reset_account()

    def reset_account(self) -> None:
        """Back to admin / admin123 with a forced change, TOTP off, all sessions ended."""
        self._db.execute(
            "INSERT OR REPLACE INTO web_account (id, username, password_hash, must_change, totp, "
            "totp_pending, updated) VALUES (1, ?, ?, 1, NULL, NULL, ?)",
            (DEFAULT_USERNAME, hash_password(DEFAULT_PASSWORD, self._pepper), utcnow().isoformat()),
        )
        self.end_all()

    def account(self) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM web_account WHERE id = 1").fetchone()
        return {"username": row["username"], "must_change": bool(row["must_change"]),
                "totp": row["totp"] is not None}  # fmt: skip

    def check_password(self, username: str, password: str) -> str | None:
        row = self._db.execute("SELECT * FROM web_account WHERE id = 1").fetchone()
        # Always hash, so a wrong user name takes as long as a wrong password.
        ok = verify_password(password[:1000], row["password_hash"], self._pepper)
        same_user = hmac.compare_digest(username.strip().casefold(), row["username"].casefold())
        return str(row["username"]) if ok and same_user else None

    def change_credentials(self, current: str, username: str, password: str) -> None:
        account = self.account()
        username = username.strip()
        if self.check_password(account["username"], current) is None:
            raise CredentialError("web.wrong_password")
        if (
            not (3 <= len(username) <= 32)
            or not username.replace("_", "").replace("-", "").replace(".", "").isalnum()
        ):
            raise CredentialError("web.bad_username")
        if len(password) < MIN_PASSWORD:
            raise CredentialError("web.password_too_short")
        if password in (DEFAULT_PASSWORD, current) or password.casefold() == username.casefold():
            raise CredentialError("web.password_too_simple")
        self._db.execute(
            "UPDATE web_account SET username = ?, password_hash = ?, must_change = 0, updated = ? "
            "WHERE id = 1",
            (username, hash_password(password, self._pepper), utcnow().isoformat()),
        )
        self._db.execute(
            "UPDATE web_sessions SET username = ? WHERE username NOT LIKE 'key:%'", (username,)
        )

    # TOTP secrets are encrypted with a key derived from the dashboard secret key.

    def _encrypt(self, value: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(12)
        sealed = AESGCM(self._totp_key).encrypt(nonce, value.encode(), b"pi-guard-totp")
        return base64.b64encode(nonce + sealed).decode()

    def _decrypt(self, value: str) -> str | None:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.b64decode(value)
        try:
            return AESGCM(self._totp_key).decrypt(raw[:12], raw[12:], b"pi-guard-totp").decode()
        except InvalidTag:
            return None  # the secret key changed; TOTP cannot be checked any more

    def totp_secret(self) -> str | None:
        row = self._db.execute("SELECT totp FROM web_account WHERE id = 1").fetchone()
        return self._decrypt(row["totp"]) if row and row["totp"] else None

    def begin_totp(self) -> str:
        secret = new_totp_secret()
        self._db.execute("UPDATE web_account SET totp_pending = ? WHERE id = 1",
                         (self._encrypt(secret),))  # fmt: skip
        return secret

    def confirm_totp(self, code: str) -> bool:
        row = self._db.execute(
            "SELECT totp_pending, username FROM web_account WHERE id = 1"
        ).fetchone()
        secret = self._decrypt(row["totp_pending"]) if row["totp_pending"] else None
        step = totp_match(secret, code) if secret else None
        if secret is None or step is None or not self.totp_step_unused("account", step):
            return False
        self._db.execute("UPDATE web_account SET totp = totp_pending, totp_pending = NULL, "
                         "updated = ? WHERE id = 1", (utcnow().isoformat(),))  # fmt: skip
        return True

    def disable_totp(self, password: str) -> bool:
        account = self.account()
        if self.check_password(account["username"], password) is None:
            return False
        self._db.execute("UPDATE web_account SET totp = NULL, totp_pending = NULL WHERE id = 1")
        return True

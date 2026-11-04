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
RECOVERY_CODES = 10
MIN_CODEWORD = 4
# web_sessions.pending: what is still missing before a session counts.
PENDING_NONE, PENDING_ALL, PENDING_CODEWORD = 0, 1, 2

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
CREATE TABLE IF NOT EXISTS web_users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL, role TEXT NOT NULL, totp TEXT, totp_pending TEXT,
    created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_recovery (hash TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS web_tokens (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, hash TEXT NOT NULL UNIQUE, scope TEXT NOT NULL,
    created TEXT NOT NULL, last_used TEXT);
CREATE TABLE IF NOT EXISTS web_factors (name TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_credentials (
    id TEXT PRIMARY KEY, public_key TEXT NOT NULL, sign_count INTEGER NOT NULL,
    name TEXT NOT NULL, created TEXT NOT NULL, last_used TEXT);
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
    level: int = PENDING_NONE


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

    def label_session(self, token: str, label: str) -> None:
        """Marks a session as belonging to something that can be removed, such as a phone."""
        self._db.execute("UPDATE web_sessions SET agent = ? WHERE id_hash = ?",
                         (label, _digest(token)))  # fmt: skip

    def end_sessions(self, label: str) -> None:
        self._db.execute("DELETE FROM web_sessions WHERE agent = ?", (label,))

    def create_session(self, username: str, ip: str, agent: str, pending: int = 0) -> str:
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
        return Session(row["username"], row["csrf"], bool(row["pending"]), int(row["pending"]))

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
        self._db.executescript(
            "DELETE FROM web_recovery; DELETE FROM web_factors; DELETE FROM web_credentials;"
        )
        self.end_all()

    # More people: admins can do everything, viewers only watch (see VIEWER_PATHS in server.py).

    def users(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, username, role, created, totp IS NOT NULL AS totp FROM web_users "
            "ORDER BY username"
        ).fetchall()
        return [{**dict(r), "totp": bool(r["totp"])} for r in rows]

    def _extra(self, username: str) -> Any:
        return self._db.execute(
            "SELECT * FROM web_users WHERE username = ?", (username,)
        ).fetchone()

    def _check_new_login(self, username: str, password: str) -> None:
        clean = username.replace("_", "").replace("-", "").replace(".", "")
        if not 3 <= len(username) <= 32 or not clean.isalnum():
            raise CredentialError("web.bad_username")
        if len(password) < MIN_PASSWORD:
            raise CredentialError("web.password_too_short")
        if password == DEFAULT_PASSWORD or password.casefold() == username.casefold():
            raise CredentialError("web.password_too_simple")

    def add_user(self, username: str, password: str, role: str) -> None:
        username = username.strip()
        self._check_new_login(username, password)
        if role not in ("admin", "viewer"):
            raise CredentialError("web.bad_username")
        taken = username.casefold() == self.account()["username"].casefold()
        if taken or self._extra(username) is not None:
            raise CredentialError("web.user_taken")
        self._db.execute(
            "INSERT INTO web_users (username, password_hash, role, created) VALUES (?, ?, ?, ?)",
            (username, hash_password(password, self._pepper), role, utcnow().isoformat()),
        )

    def set_role(self, user_id: int, role: str) -> bool:
        if role not in ("admin", "viewer"):
            return False
        return self._db.execute("UPDATE web_users SET role = ? WHERE id = ?",
                                (role, user_id)).rowcount > 0  # fmt: skip

    def delete_user(self, user_id: int) -> bool:
        row = self._db.execute("SELECT username FROM web_users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return False
        self._db.execute("DELETE FROM web_users WHERE id = ?", (user_id,))
        self._db.execute("DELETE FROM web_sessions WHERE username = ?", (row["username"],))
        return True

    def change_own_password(self, username: str, current: str, password: str) -> None:
        row = self._extra(username)
        if row is None or self.check_password(username, current) is None:
            raise CredentialError("web.wrong_password")
        self._check_new_login(username, password)
        self._db.execute("UPDATE web_users SET password_hash = ? WHERE id = ?",
                         (hash_password(password, self._pepper), row["id"]))  # fmt: skip

    def role_of(self, username: str) -> str:
        """Unknown names are viewers: a session can outlive a changed account."""
        if username.startswith(("key:", "api:")) or username == "local":
            return "admin"
        if username.casefold() == self.account()["username"].casefold():
            return "admin"
        row = self._extra(username)
        return str(row["role"]) if row else "viewer"

    def is_owner(self, username: str) -> bool:
        return self._extra(username) is None

    def account(self, username: str | None = None) -> dict[str, Any]:
        extra = self._extra(username) if username else None
        if extra is not None:
            return {"username": extra["username"], "must_change": False, "owner": False,
                    "totp": extra["totp"] is not None, "codeword": False,
                    "recovery_left": 0}  # fmt: skip
        row = self._db.execute("SELECT * FROM web_account WHERE id = 1").fetchone()
        return {"username": row["username"], "must_change": bool(row["must_change"]),
                "totp": row["totp"] is not None, "codeword": self.has_codeword,
                "recovery_left": self.recovery_left, "owner": True}  # fmt: skip

    def check_password(self, username: str, password: str) -> str | None:
        row = self._db.execute("SELECT * FROM web_account WHERE id = 1").fetchone()
        extra = self._extra(username.strip())
        stored = extra["password_hash"] if extra else row["password_hash"]
        # Always hash, so a wrong user name takes as long as a wrong password.
        ok = verify_password(password[:1000], stored, self._pepper)
        if extra is not None:
            return str(extra["username"]) if ok else None
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
        if self._extra(username) is not None:
            raise CredentialError("web.user_taken")
        self._db.execute(
            "UPDATE web_account SET username = ?, password_hash = ?, must_change = 0, updated = ? "
            "WHERE id = 1",
            (username, hash_password(password, self._pepper), utcnow().isoformat()),
        )
        self._db.execute("UPDATE web_sessions SET username = ? WHERE username = ?",
                         (username, account["username"]))  # fmt: skip

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

    def _totp_row(self, username: str | None) -> tuple[str, int]:
        """(table, id) of a person's TOTP columns; the table name is one of these two constants."""
        extra = self._extra(username) if username else None
        return ("web_users", extra["id"]) if extra is not None else ("web_account", 1)

    def _totp_read(self, column: str, username: str | None) -> str | None:
        table, key = self._totp_row(username)
        sql = f"SELECT {column} AS value FROM {table} WHERE id = ?"  # noqa: S608
        row = self._db.execute(sql, (key,)).fetchone()
        return self._decrypt(row["value"]) if row and row["value"] else None

    def _totp_write(self, assignment: str, values: tuple[Any, ...], username: str | None) -> None:
        table, key = self._totp_row(username)
        sql = f"UPDATE {table} SET {assignment} WHERE id = ?"  # noqa: S608
        self._db.execute(sql, (*values, key))

    def totp_secret(self, username: str | None = None) -> str | None:
        return self._totp_read("totp", username)

    def begin_totp(self, username: str | None = None) -> str:
        secret = new_totp_secret()
        self._totp_write("totp_pending = ?", (self._encrypt(secret),), username)
        return secret

    def confirm_totp(self, code: str, username: str | None = None) -> bool:
        secret = self._totp_read("totp_pending", username)
        step = totp_match(secret, code) if secret else None
        used = "account" if username is None or self.is_owner(username) else username
        if secret is None or step is None or not self.totp_step_unused(used, step):
            return False
        self._totp_write("totp = totp_pending, totp_pending = NULL", (), username)
        return True

    def disable_totp(self, password: str, username: str | None = None) -> bool:
        account = self.account(username)
        if self.check_password(account["username"], password) is None:
            return False
        self._totp_write("totp = NULL, totp_pending = NULL", (), username)
        if account["owner"]:
            self._db.execute("DELETE FROM web_recovery")
        return True

    # Recovery codes replace a lost phone once each. 80 random bits, so a keyed hash is enough.

    def _recovery_hash(self, code: str) -> str:
        cleaned = code.replace("-", "").replace(" ", "").upper()
        return hmac.new(self._pepper, cleaned.encode(), hashlib.sha256).hexdigest()

    def new_recovery_codes(self) -> list[str]:
        codes = []
        self._db.execute("DELETE FROM web_recovery")
        for _ in range(RECOVERY_CODES):
            raw = base64.b32encode(secrets.token_bytes(10)).decode()
            code = "-".join(raw[i : i + 4] for i in range(0, 16, 4))
            self._db.execute("INSERT INTO web_recovery (hash) VALUES (?)",
                             (self._recovery_hash(code),))  # fmt: skip
            codes.append(code)
        return codes

    @property
    def recovery_left(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM web_recovery").fetchone()[0])

    def use_recovery_code(self, code: str) -> bool:
        cursor = self._db.execute("DELETE FROM web_recovery WHERE hash = ?",
                                  (self._recovery_hash(code),))  # fmt: skip
        return cursor.rowcount > 0

    # The code word is asked in addition to everything else at every login.

    @property
    def has_codeword(self) -> bool:
        return (
            self._db.execute("SELECT 1 FROM web_factors WHERE name = 'codeword'").fetchone()
            is not None
        )

    def set_codeword(self, password: str, codeword: str) -> None:
        """Empty `codeword` removes it."""
        if self.check_password(self.account()["username"], password) is None:
            raise CredentialError("web.wrong_password")
        word = codeword.strip().casefold()
        if not word:
            self._db.execute("DELETE FROM web_factors WHERE name = 'codeword'")
            return
        if len(word) < MIN_CODEWORD:
            raise CredentialError("web.codeword_too_short")
        self._db.execute(
            "INSERT OR REPLACE INTO web_factors (name, value) VALUES ('codeword', ?)",
            (hash_password(word, self._pepper),),
        )

    def check_codeword(self, word: str) -> bool:
        row = self._db.execute("SELECT value FROM web_factors WHERE name = 'codeword'").fetchone()
        if row is None:
            return True
        return verify_password(word.strip().casefold()[:200], row["value"], self._pepper)

    # API tokens: 256 random bits, shown once, stored as a hash.

    def create_token(self, name: str, scope: str) -> str:
        token = f"pg_{secrets.token_urlsafe(32)}"
        self._db.execute(
            "INSERT INTO web_tokens (name, hash, scope, created) VALUES (?, ?, ?, ?)",
            (name[:40], _digest(token), scope, utcnow().isoformat()),
        )
        return token

    def tokens(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, name, scope, created, last_used FROM web_tokens ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_token(self, token_id: int) -> bool:
        return self._db.execute("DELETE FROM web_tokens WHERE id = ?", (token_id,)).rowcount > 0

    def check_token(self, token: str) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM web_tokens WHERE hash = ?",
                               (_digest(token),)).fetchone()  # fmt: skip
        if row is None:
            return None
        self._db.execute("UPDATE web_tokens SET last_used = ? WHERE id = ?",
                         (utcnow().isoformat(), row["id"]))  # fmt: skip
        return dict(row)

    # Passkeys and hardware keys (WebAuthn).

    def credentials(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM web_credentials ORDER BY created").fetchall()
        return [dict(r) for r in rows]

    def add_credential(self, credential_id: str, public_key: str, count: int, name: str) -> None:
        self._db.execute(
            "INSERT INTO web_credentials (id, public_key, sign_count, name, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (credential_id, public_key, count, name[:40], utcnow().isoformat()),
        )

    def credential_used(self, credential_id: str, count: int) -> None:
        self._db.execute("UPDATE web_credentials SET sign_count = ?, last_used = ? WHERE id = ?",
                         (count, utcnow().isoformat(), credential_id))  # fmt: skip

    def delete_credential(self, credential_id: str) -> bool:
        cursor = self._db.execute("DELETE FROM web_credentials WHERE id = ?", (credential_id,))
        return cursor.rowcount > 0

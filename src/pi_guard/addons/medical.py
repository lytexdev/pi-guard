"""Emergency medical information of the people at home, for when help comes.

Health data are a special category (Art. 9 GDPR), so:
- each record is encrypted (AES-256-GCM) in its own database, `medical.db`, with a key of its
  own, `medical.key`, both readable by the service only; deleted rows are overwritten
- only admins see or change them, and each time with their password
- every view, change and every opening of a rescue link goes into the audit log
- a rescue link shows one person's sheet, read only, for 15 to 60 minutes; by default it also
  needs a six-digit PIN that the admin tells the paramedic, and five wrong PINs end it. It can
  be ended at any time. Only a hash of the link and of the PIN is stored
- nothing of it goes into chats, push messages or MQTT; at most a link does

Encryption at rest protects a copied database file; someone with the service's rights on the
running Pi can read it, as the Pi itself has to.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import secure_file

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, data BLOB NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS links (
    hash TEXT PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    expires TEXT NOT NULL, pin_hash TEXT, tries INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL, created_by TEXT NOT NULL, opened INTEGER NOT NULL DEFAULT 0);
PRAGMA foreign_keys=ON;
"""
LINK_MINUTES = (15, 30, 60)
PIN_TRIES = 5
TEXT_FIELDS = {"name": 80, "birthday": 10, "blood_type": 10, "height_cm": 5, "weight_kg": 5,
               "allergies": 1000, "conditions": 1500, "medications": 2000,
               "medication_plan": 2000, "implants": 500, "organ_donor": 40,
               "doctor": 300, "insurance": 200, "contacts": 600, "notes": 2000}  # fmt: skip
BLOOD_TYPES = {"", "0+", "0-", "A+", "A-", "B+", "B-", "AB+", "AB-"}


class MedicalError(ValueError):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def clean(data: dict[str, Any]) -> dict[str, str]:
    values = {}
    for key, limit in TEXT_FIELDS.items():
        lines = [" ".join(line.split()) for line in str(data.get(key) or "").splitlines()]
        value = "\n".join(line for line in lines if line)[:limit]
        if value:
            values[key] = value
    if not values.get("name"):
        raise MedicalError("web.md_needs_name")
    if values.get("blood_type", "").upper() not in BLOOD_TYPES:
        raise MedicalError("web.md_bad_blood_type")
    if "blood_type" in values:
        values["blood_type"] = values["blood_type"].upper()
    return values


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class MedicalRecords:
    def __init__(self, data_dir: Path) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        path = data_dir / "medical.db"
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        secure_file(path)
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(_SCHEMA)
        key_path = data_dir / "medical.key"
        if not key_path.exists():
            key_path.write_bytes(AESGCM.generate_key(bit_length=256))
        secure_file(key_path)
        self._aead = AESGCM(key_path.read_bytes())

    def _seal(self, record_id: int, data: dict[str, str]) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, json.dumps(data).encode(), str(record_id).encode())

    def _open(self, record_id: int, blob: bytes) -> dict[str, str]:
        plain = self._aead.decrypt(blob[:12], blob[12:], str(record_id).encode())
        return dict(json.loads(plain))

    def people(self) -> list[dict[str, Any]]:
        """Names only, for choosing; the sheet itself needs `read`."""
        rows = self._conn.execute("SELECT id, name, updated FROM records ORDER BY name")
        return [{"id": r[0], "name": r[1], "updated": r[2]} for r in rows]

    def read(self, record_id: int) -> dict[str, str] | None:
        row = self._conn.execute("SELECT data FROM records WHERE id = ?", (record_id,)).fetchone()
        return self._open(record_id, bytes(row[0])) if row else None

    def save(self, record_id: int | None, data: dict[str, Any]) -> int:
        values = clean(data)
        now = utcnow().isoformat()
        if record_id is None:
            record_id = int(self._conn.execute(
                "INSERT INTO records (name, data, updated) VALUES (?, ?, ?)",
                (values["name"], b"", now)).lastrowid or 0)  # fmt: skip
        elif (
            self._conn.execute("SELECT 1 FROM records WHERE id = ?", (record_id,)).fetchone()
            is None
        ):
            raise MedicalError("web.md_not_found")
        sealed = self._seal(record_id, values)
        self._conn.execute("UPDATE records SET name = ?, data = ?, updated = ? WHERE id = ?",
                           (values["name"], sealed, now, record_id))  # fmt: skip
        return record_id

    def delete(self, record_id: int) -> bool:
        gone = self._conn.execute("DELETE FROM records WHERE id = ?", (record_id,)).rowcount > 0
        if gone:
            self._conn.execute("DELETE FROM links WHERE record_id = ?", (record_id,))
            self._conn.execute("VACUUM")
        return gone

    # --- rescue links -------------------------------------------------------------------

    def make_link(
        self, record_id: int, minutes: int, with_pin: bool, actor: str
    ) -> tuple[str, str | None, str]:
        """(token, pin, expires). Neither is stored, only their hashes."""
        if minutes not in LINK_MINUTES:
            raise MedicalError("web.md_bad_minutes")
        if self.read(record_id) is None:
            raise MedicalError("web.md_not_found")
        token = secrets.token_urlsafe(32)
        pin = f"{secrets.randbelow(10**6):06d}" if with_pin else None
        expires = (utcnow() + timedelta(minutes=minutes)).isoformat()
        self._conn.execute(
            "INSERT INTO links (hash, record_id, expires, pin_hash, created, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_digest(token), record_id, expires, _digest(f"{token}:{pin}") if pin else None,
             utcnow().isoformat(), actor),
        )  # fmt: skip
        return token, pin, expires

    def links(self) -> list[dict[str, Any]]:
        now = utcnow().isoformat()
        self._conn.execute("DELETE FROM links WHERE expires < ?", (now,))
        rows = self._conn.execute(
            "SELECT l.hash, r.name, l.expires, l.pin_hash IS NOT NULL, l.opened, l.created_by "
            "FROM links l JOIN records r ON r.id = l.record_id ORDER BY l.expires"
        )
        return [{"id": r[0][:16], "name": r[1], "expires": r[2], "pin": bool(r[3]),
                 "opened": r[4], "by": r[5]} for r in rows]  # fmt: skip

    def end_link(self, short_id: str) -> bool:
        if not short_id or len(short_id) < 16:
            return False
        return self._conn.execute("DELETE FROM links WHERE substr(hash, 1, 16) = ?",
                                  (short_id[:16],)).rowcount > 0  # fmt: skip

    def link_state(self, token: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT expires, pin_hash IS NOT NULL FROM links WHERE hash = ? AND expires >= ?",
            (_digest(token[:100]), utcnow().isoformat())).fetchone()  # fmt: skip
        return {"expires": row[0], "pin": bool(row[1])} if row else None

    def open_link(self, token: str, pin: str) -> tuple[str, dict[str, str]] | None:
        """The sheet behind a valid link and PIN, or None. Wrong PINs count; five end it."""
        digest = _digest(token[:100])
        row = self._conn.execute(
            "SELECT record_id, pin_hash, tries FROM links WHERE hash = ? AND expires >= ?",
            (digest, utcnow().isoformat())).fetchone()  # fmt: skip
        if row is None:
            return None
        record_id, pin_hash, tries = row
        if pin_hash and not secrets.compare_digest(pin_hash, _digest(f"{token}:{pin.strip()}")):
            if tries + 1 >= PIN_TRIES:
                self._conn.execute("DELETE FROM links WHERE hash = ?", (digest,))
            else:
                self._conn.execute("UPDATE links SET tries = tries + 1 WHERE hash = ?", (digest,))
            raise MedicalError("web.md_wrong_pin")
        self._conn.execute("UPDATE links SET opened = opened + 1 WHERE hash = ?", (digest,))
        sheet = self.read(record_id)
        return (str(sheet["name"]), sheet) if sheet else None

    def export(self) -> list[dict[str, str]]:
        return [self.read(r["id"]) or {} for r in self.people()]

    def close(self) -> None:
        self._conn.close()

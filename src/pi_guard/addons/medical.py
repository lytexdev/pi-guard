"""Emergency medical information of the people at home, for when help comes.

Health data are a special category (Art. 9 GDPR), so:
- each record is encrypted (AES-256-GCM) in its own database, `medical.db`, with a key of its
  own, `medical.key`, both readable by the service only; deleted rows are overwritten
- only logged-in admins see or change them; files, sending and deleting need the password
- every view, change, file and sending goes into the audit log
- nothing of it goes into chats, push messages or MQTT, except the PDF to an emergency contact
  set to get it (notifications/sharing.py); others are only told that data exist

Encryption at rest protects a copied database file; someone with the service's rights on the
running Pi can read it, as the Pi itself has to.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import secure_file

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, data BLOB NOT NULL, updated TEXT NOT NULL);
PRAGMA foreign_keys=ON;
"""
# One line each.
LINE_FIELDS = {"name": 80, "birthday": 10, "blood_type": 10, "height_cm": 5, "weight_kg": 5,
               "phone": 40, "email": 120, "account": 64, "organ_donor": 20,
               "organ_donor_note": 300, "insurance": 200}  # fmt: skip
# Several lines, Markdown allowed (shown as text where Markdown is not drawn).
TEXT_FIELDS = {"allergies": 1000, "conditions": 1500, "medications": 2000,
               "medication_plan": 6000, "implants": 500, "doctor": 300, "contacts": 600,
               "notes": 2000}  # fmt: skip
BLOOD_TYPES = {"", "0+", "0-", "A+", "A-", "B+", "B-", "AB+", "AB-"}
ORGAN_DONOR = {"", "yes", "no", "some", "except", "person", "undecided"}
# A medicine as a line of the German federal medication plan (BMP) has it.
MED_FIELDS = {"name": 80, "ingredient": 80, "strength": 40, "form": 40, "morning": 8,
              "noon": 8, "evening": 8, "night": 8, "schedule": 60, "unit": 30,
              "reason": 120, "note": 200}  # fmt: skip
MED_SOURCES = ("doctor", "self")  # the first is the default
MED_KINDS = ("regular", "as_needed")
SUPPLEMENT_FIELDS = {"name": 80, "dose": 60, "note": 200}
MAX_ITEMS = 40
_CONTROL = dict.fromkeys(c for c in range(32) if c not in (9, 10))


class MedicalError(ValueError):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _text(value: object, limit: int) -> str:
    """Keeps line breaks and indentation for Markdown, drops control characters and runs of
    empty lines."""
    text = str(value or "").replace("\r\n", "\n").replace("\t", "  ").translate(_CONTROL)
    lines, empty = [], 0
    for line in text.split("\n"):
        line = line.rstrip()
        empty = empty + 1 if not line else 0
        if empty < 2:
            lines.append(line)
    return "\n".join(lines).strip("\n")[:limit]


def _items(
    value: object, fields: dict[str, int], choices: dict[str, tuple[str, ...]] | None = None
) -> list[dict[str, str]]:
    """Rows without a name are dropped; a choice not offered becomes its first value."""
    items = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        item = {k: _line(raw.get(k), n) for k, n in fields.items() if _line(raw.get(k), n)}
        if not (item.get("name") or item.get("ingredient")):
            continue
        for key, allowed in (choices or {}).items():
            item[key] = raw[key] if raw.get(key) in allowed else allowed[0]
        items.append(item)
    return items[:MAX_ITEMS]


def clean(data: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, limit in LINE_FIELDS.items():
        if value := _line(data.get(key), limit):
            values[key] = value
    for key, limit in TEXT_FIELDS.items():
        if value := _text(data.get(key), limit):
            values[key] = value
    if not values.get("name"):
        raise MedicalError("web.md_needs_name")
    if values.get("blood_type", "").upper() not in BLOOD_TYPES:
        raise MedicalError("web.md_bad_blood_type")
    if "blood_type" in values:
        values["blood_type"] = values["blood_type"].upper()
    donor = str(data.get("organ_donor") or "")
    if donor not in ORGAN_DONOR:  # free text from before the choice: keep it as the note
        note = " ".join(filter(None, [_line(donor, 300), values.get("organ_donor_note", "")]))
        values["organ_donor_note"] = note[:300]
        values.pop("organ_donor", None)
    if meds := _items(data.get("meds"), MED_FIELDS, {"source": MED_SOURCES, "kind": MED_KINDS}):
        values["meds"] = meds
    if supplements := _items(data.get("supplements"), SUPPLEMENT_FIELDS):
        values["supplements"] = supplements
    if values.get("email") and "@" not in values["email"]:
        raise MedicalError("web.md_bad_email")
    return values


class MedicalRecords:
    def __init__(self, data_dir: Path) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        path = data_dir / "medical.db"
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        secure_file(path)
        self._conn.execute("PRAGMA secure_delete=ON")
        self._conn.executescript(_SCHEMA)
        # Rescue links are gone (1.10); their hashes too.
        self._conn.execute("DROP TABLE IF EXISTS links")
        key_path = data_dir / "medical.key"
        if not key_path.exists():
            key_path.write_bytes(AESGCM.generate_key(bit_length=256))
        secure_file(key_path)
        self._aead = AESGCM(key_path.read_bytes())

    def _seal(self, record_id: int, data: dict[str, Any]) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._aead.encrypt(nonce, json.dumps(data).encode(), str(record_id).encode())

    def _open(self, record_id: int, blob: bytes) -> dict[str, Any]:
        plain = self._aead.decrypt(blob[:12], blob[12:], str(record_id).encode())
        return dict(json.loads(plain))

    def people(self) -> list[dict[str, Any]]:
        """Names only, for choosing; the sheet itself needs `read`."""
        rows = self._conn.execute("SELECT id, name, updated FROM records ORDER BY name")
        return [{"id": r[0], "name": r[1], "updated": r[2]} for r in rows]

    def read(self, record_id: int) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT data FROM records WHERE id = ?", (record_id,)).fetchone()
        return self._open(record_id, bytes(row[0])) if row else None

    def for_account(self, username: str) -> tuple[int, dict[str, Any]] | None:
        """The sheet of the person a dashboard account belongs to, for an SOS from its phone."""
        if not username:
            return None
        for person in self.people():
            sheet = self.read(person["id"]) or {}
            if str(sheet.get("account", "")).casefold() == username.casefold():
                return person["id"], sheet
        return None

    def forget_account(self, username: str) -> None:
        found = self.for_account(username)
        if found is not None:
            record_id, sheet = found
            self.save(record_id, {**sheet, "account": ""})

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
            self._conn.execute("VACUUM")
        return gone

    def export(self) -> list[dict[str, Any]]:
        return [self.read(r["id"]) or {} for r in self.people()]

    def close(self) -> None:
        self._conn.close()

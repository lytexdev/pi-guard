"""Contact details of dashboard accounts: name, email, phone, address, a note. All optional.

They belong to the account: renaming it moves them, removing it deletes them, and the data
export lists them. The account's email address is also where a login code by email goes,
never to the recipients of alerts, who may be other people.

vCard (RFC 6350) in and out, so the details can come from and go to an address book.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pi_guard.storage.db import Database

FIELDS = {"name": 80, "email": 120, "phone": 40, "address": 200, "note": 500}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS web_profiles (
    username TEXT PRIMARY KEY COLLATE NOCASE, data TEXT NOT NULL);
"""
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE = re.compile(r"^\+?[0-9 ()/.-]{3,40}$")


class ProfileError(ValueError):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def clean(data: dict[str, Any]) -> dict[str, str]:
    values = {k: " ".join(str(data.get(k) or "").split())[:n] for k, n in FIELDS.items()
              if k not in ("address", "note")}  # fmt: skip
    for key in ("address", "note"):  # these may have lines
        lines = [" ".join(line.split()) for line in str(data.get(key) or "").splitlines()]
        values[key] = "\n".join(line for line in lines if line)[: FIELDS[key]]
    if values["email"] and not EMAIL.match(values["email"]):
        raise ProfileError("web.pp_bad_email")
    if values["phone"] and not PHONE.match(values["phone"]):
        raise ProfileError("web.pf_bad_phone")
    return {k: v for k, v in values.items() if v}


class Profiles:
    def __init__(self, db: Database) -> None:
        self._db = db
        db.executescript(_SCHEMA)

    def get(self, username: str) -> dict[str, str]:
        row = self._db.execute(
            "SELECT data FROM web_profiles WHERE username = ?", (username,)
        ).fetchone()
        return dict(json.loads(row[0])) if row else {}

    def set(self, username: str, data: dict[str, Any]) -> dict[str, str]:
        values = clean(data)
        if values:
            self._db.execute(
                "INSERT INTO web_profiles VALUES (?, ?) ON CONFLICT(username) "
                "DO UPDATE SET data = excluded.data",
                (username, json.dumps(values)),
            )
        else:
            self.delete(username)
        return values

    def delete(self, username: str) -> None:
        self._db.execute("DELETE FROM web_profiles WHERE username = ?", (username,))

    def rename(self, old: str, new: str) -> None:
        if old.casefold() != new.casefold():
            self._db.execute("DELETE FROM web_profiles WHERE username = ?", (new,))
        self._db.execute("UPDATE web_profiles SET username = ? WHERE username = ?", (new, old))

    def all(self) -> dict[str, dict[str, str]]:
        return {str(r[0]): json.loads(r[1]) for r in self._db.execute("SELECT * FROM web_profiles")}


# --- vCard ----------------------------------------------------------------------------------


def _escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,")
            .replace(";", "\\;"))  # fmt: skip


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def to_vcard(username: str, data: dict[str, str]) -> str:
    name = data.get("name") or username
    lines = ["BEGIN:VCARD", "VERSION:4.0", f"FN:{_escape(name)}", f"NICKNAME:{_escape(username)}"]
    if data.get("email"):
        lines.append(f"EMAIL:{_escape(data['email'])}")
    if data.get("phone"):
        lines.append(f"TEL;VALUE=uri:tel:{data['phone'].replace(' ', '')}")
    if data.get("address"):
        lines.append(f"ADR:;;{_escape(data['address'])};;;;")
    if data.get("note"):
        lines.append(f"NOTE:{_escape(data['note'])}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def from_vcard(text: str) -> dict[str, str]:
    """The fields of the first card in `text`; unknown lines are left out."""
    unfolded = re.sub(r"\r?\n[ \t]", "", text[:20000])
    found: dict[str, str] = {}
    for line in unfolded.splitlines():
        if ":" not in line:
            continue
        head, value = line.split(":", 1)
        key = head.split(";", 1)[0].strip().upper()
        if key == "END":
            break
        if key == "FN" and "name" not in found:
            found["name"] = _unescape(value)
        elif key == "EMAIL" and "email" not in found:
            found["email"] = _unescape(value)
        elif key == "TEL" and "phone" not in found:
            found["phone"] = _unescape(value).removeprefix("tel:")
        elif key == "ADR" and "address" not in found:
            parts = [p for p in _split(value) if p]
            found["address"] = "\n".join(parts)
        elif key == "NOTE" and "note" not in found:
            found["note"] = _unescape(value)
    return found


def _split(value: str) -> list[str]:
    """ADR's parts at unescaped semicolons."""
    parts, current, i = [], [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            current.append(value[i : i + 2])
            i += 2
            continue
        if value[i] == ";":
            parts.append(_unescape("".join(current)).strip())
            current = []
        else:
            current.append(value[i])
        i += 1
    parts.append(_unescape("".join(current)).strip())
    return parts

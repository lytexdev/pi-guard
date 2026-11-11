"""Emergency contacts: who they are, how to reach them, and what each gets on which occasion.

A contact has a name, optionally a relation, phone and email, a dashboard account it belongs
to, and one or more ways to reach it (Telegram, Signal, Discord, WhatsApp or email). For
each occasion (an SOS from a phone, alerts nobody acknowledged) the owner chooses what the
contact gets: the place, pictures from the phone or from the Pi's cameras, sound, the latest
events, and the emergency medical data of the person in need, as a rescue link with a PIN or
as the sheet itself (PDF).

The older settings (`emergency_telegram_user_ids` and so on) still work: each entry is a
contact that gets what it got before, and it is shown read only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

OCCASIONS = ("sos", "escalation")
SHARES = ("location", "phone_photo", "phone_audio", "pi_photo", "events", "home_audio",
          "medical")  # fmt: skip
MEDICAL_MODES = ("link", "sheet")  # sheet: the PDF goes to the chat service
PROVIDERS = ("telegram", "signal", "discord", "whatsapp", "email")
# What the old settings sent; new contacts start with this as well, without medical data.
DEFAULT_SHARE = {
    "sos": ["location", "phone_photo", "phone_audio", "pi_photo", "home_audio"],
    "escalation": ["events", "pi_photo"],
}
MAX_CONTACTS = 30
MAX_CHANNELS = 5
_TABLE = """
CREATE TABLE IF NOT EXISTS emergency_contacts (
    id INTEGER PRIMARY KEY, data TEXT NOT NULL, created TEXT NOT NULL);
"""
_ADDRESS = {
    "telegram": re.compile(r"^-?\d{1,20}$"),
    "signal": re.compile(r"^\+\d{6,16}$"),
    "discord": re.compile(r"^\d{5,25}$"),
    "whatsapp": re.compile(r"^\+?\d{6,16}$"),
    "email": re.compile(r"^[^@\s<>]{1,64}@[^@\s<>]{1,190}\.[A-Za-z]{2,}$"),
}


class ContactError(ValueError):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class Contact:
    id: int
    name: str
    channels: list[tuple[str, str]]
    share: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_SHARE.items()}
    )
    medical_mode: str = "link"
    relation: str = ""
    phone: str = ""
    email: str = ""
    account: str = ""
    note: str = ""
    legacy: bool = False  # from the settings, read only

    def gets(self, occasion: str, what: str) -> bool:
        return what in self.share.get(occasion, [])

    def wants(self, occasion: str) -> bool:
        return occasion in self.share

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "relation": self.relation, "phone": self.phone,
            "email": self.email, "account": self.account, "note": self.note,
            "channels": [{"provider": p, "address": a} for p, a in self.channels],
            "share": self.share, "medical_mode": self.medical_mode, "legacy": self.legacy,
        }  # fmt: skip


def _line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean(data: dict[str, Any]) -> dict[str, Any]:
    name = _line(data.get("name"), 60)
    if not name:
        raise ContactError("web.ec_needs_name")
    channels = []
    for raw in data.get("channels") or []:
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider", ""))
        address = _line(raw.get("address"), 200).replace(" ", "")
        if not address:
            continue
        if provider not in PROVIDERS or not _ADDRESS[provider].match(address):
            raise ContactError("web.ec_bad_address")
        if (provider, address) not in channels:
            channels.append((provider, address))
    if not channels:
        raise ContactError("web.ec_needs_channel")
    raw_share = data.get("share")
    share: dict[str, list[str]] = {}
    if not isinstance(raw_share, dict):  # a new contact without a choice: what the old ones got
        share = {k: list(v) for k, v in DEFAULT_SHARE.items()}
    else:
        for occasion in OCCASIONS:  # an occasion left out: the contact is not told at all
            chosen = raw_share.get(occasion)
            if isinstance(chosen, list):
                share[occasion] = [s for s in SHARES if s in chosen]
    email = _line(data.get("email"), 200)
    if email and not _ADDRESS["email"].match(email):
        raise ContactError("web.ec_bad_email")
    mode = str(data.get("medical_mode") or "link")
    return {
        "name": name, "relation": _line(data.get("relation"), 40),
        "phone": _line(data.get("phone"), 40), "email": email,
        "account": _line(data.get("account"), 64), "note": _line(data.get("note"), 300),
        "channels": [list(c) for c in channels[:MAX_CHANNELS]], "share": share,
        "medical_mode": mode if mode in MEDICAL_MODES else "link",
    }  # fmt: skip


def _contact(row_id: int, data: dict[str, Any]) -> Contact:
    return Contact(
        id=row_id, name=data["name"], channels=[(p, a) for p, a in data["channels"]],
        share=data["share"], medical_mode=data.get("medical_mode", "link"),
        relation=data.get("relation", ""), phone=data.get("phone", ""),
        email=data.get("email", ""), account=data.get("account", ""), note=data.get("note", ""),
    )  # fmt: skip


def from_settings(settings: Any) -> list[Contact]:
    pairs = (
        [("telegram", str(uid)) for uid in settings.emergency_telegram_user_ids]
        + [("signal", number) for number in settings.emergency_signal_numbers]
        + [("discord", uid) for uid in settings.emergency_discord_user_ids]
    )
    return [Contact(id=-(n + 1), name=f"{p}: {a}", channels=[(p, a)], legacy=True)
            for n, (p, a) in enumerate(pairs)]  # fmt: skip


class Contacts:
    def __init__(self, db: Database, settings: Any) -> None:
        self._db = db
        self._legacy = from_settings(settings)
        db.executescript(_TABLE)

    def all(self) -> list[Contact]:
        rows = self._db.execute("SELECT id, data FROM emergency_contacts ORDER BY id").fetchall()
        return self._legacy + [_contact(r["id"], json.loads(r["data"])) for r in rows]

    def get(self, contact_id: int) -> Contact | None:
        return next((c for c in self.all() if c.id == contact_id), None)

    def save(self, contact_id: int | None, data: dict[str, Any]) -> int:
        values = json.dumps(clean(data))
        if contact_id is None:
            count = self._db.execute("SELECT COUNT(*) FROM emergency_contacts").fetchone()[0]
            if count >= MAX_CONTACTS:
                raise ContactError("web.ec_too_many")
            cursor = self._db.execute(
                "INSERT INTO emergency_contacts (data, created) VALUES (?, ?)",
                (values, utcnow().isoformat()),
            )
            return int(cursor.lastrowid or 0)
        changed = self._db.execute(
            "UPDATE emergency_contacts SET data = ? WHERE id = ?", (values, contact_id)
        ).rowcount
        if not changed:
            raise ContactError("web.ec_not_found")
        return contact_id

    def delete(self, contact_id: int) -> bool:
        return (
            self._db.execute("DELETE FROM emergency_contacts WHERE id = ?", (contact_id,)).rowcount
            > 0
        )

    def recipients(self, occasion: str) -> list[tuple[str, str, Contact]]:
        """(provider, address, contact) for everyone who takes part in this occasion."""
        return [(p, a, c) for c in self.all() if c.wants(occasion) for p, a in c.channels]

    def channels(self) -> set[tuple[str, str]]:
        return {(p, a) for c in self.all() for p, a in c.channels}

    def forget_account(self, username: str) -> None:
        """A removed dashboard account is no longer linked."""
        for contact in self.all():
            if not contact.legacy and contact.account.casefold() == username.casefold():
                self.save(contact.id, {**contact.as_dict(), "account": ""})

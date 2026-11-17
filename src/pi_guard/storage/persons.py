"""Central people: face, account, messenger and emergency links on one record."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

KINDS = ("face", "web_account", "messenger", "emergency_contact", "medical", "vpn")
# household: lives here; trusted: cameras know them; emergency/alert/viewer: who gets what
ROLES = ("household", "trusted", "emergency", "alert", "viewer")
MAX_PERSONS = 80


class PersonError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass
class Person:
    id: int
    display_name: str
    note: str = ""
    roles: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "note": self.note,
            "roles": list(self.roles),
            "links": [{"kind": k, "ref": r} for k, r in self.links],
        }


def _line(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


class Persons:
    def __init__(self, db: Database) -> None:
        self._db = db

    def all(self) -> list[Person]:
        rows = self._db.execute(
            "SELECT id, display_name, note, roles FROM persons ORDER BY display_name COLLATE NOCASE"
        ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, person_id: int) -> Person | None:
        row = self._db.execute(
            "SELECT id, display_name, note, roles FROM persons WHERE id = ?", (person_id,)
        ).fetchone()
        return self._row(row) if row else None

    def find_by_link(self, kind: str, ref: str) -> Person | None:
        row = self._db.execute(
            "SELECT person_id FROM person_links WHERE kind = ? AND ref = ?", (kind, ref)
        ).fetchone()
        return self.get(int(row["person_id"])) if row else None

    def save(self, person_id: int | None, data: dict[str, Any]) -> int:
        name = _line(data.get("display_name") or data.get("name"), 60)
        if not name:
            raise PersonError("web.person_needs_name")
        note = _line(data.get("note"), 300)
        roles = [r for r in data.get("roles") or [] if r in ROLES]
        roles_json = json.dumps(roles)
        if person_id is None:
            count = self._db.execute("SELECT COUNT(*) FROM persons").fetchone()[0]
            if count >= MAX_PERSONS:
                raise PersonError("web.person_limit")
            cur = self._db.execute(
                "INSERT INTO persons (display_name, note, roles, created) VALUES (?, ?, ?, ?)",
                (name, note, roles_json, utcnow().isoformat()),
            )
            person_id = int(cur.lastrowid or 0)
        else:
            if not self.get(person_id):
                raise PersonError("web.person_missing")
            self._db.execute(
                "UPDATE persons SET display_name = ?, note = ?, roles = ? WHERE id = ?",
                (name, note, roles_json, person_id),
            )
        if "links" in data:
            self._db.execute("DELETE FROM person_links WHERE person_id = ?", (person_id,))
            for raw in data.get("links") or []:
                if not isinstance(raw, dict):
                    continue
                kind, ref = str(raw.get("kind", "")), _line(raw.get("ref"), 120)
                if kind not in KINDS or not ref:
                    continue
                self.link(person_id, kind, ref)
        return person_id

    def link(self, person_id: int, kind: str, ref: str) -> None:
        if kind not in KINDS or not ref:
            raise PersonError("web.person_bad_link")
        if not self.get(person_id):
            raise PersonError("web.person_missing")
        existing = self._db.execute(
            "SELECT person_id FROM person_links WHERE kind = ? AND ref = ?", (kind, ref)
        ).fetchone()
        if existing and int(existing["person_id"]) != person_id:
            self._db.execute("DELETE FROM person_links WHERE kind = ? AND ref = ?", (kind, ref))
        self._db.execute(
            "INSERT OR REPLACE INTO person_links (person_id, kind, ref) VALUES (?, ?, ?)",
            (person_id, kind, ref),
        )

    def unlink(self, kind: str, ref: str) -> None:
        self._db.execute("DELETE FROM person_links WHERE kind = ? AND ref = ?", (kind, ref))

    def delete(self, person_id: int) -> bool:
        return bool(self._db.execute("DELETE FROM persons WHERE id = ?", (person_id,)).rowcount)

    def ensure_named(self, name: str, *, kind: str | None = None, ref: str | None = None) -> int:
        """Find or create a person by display name; optionally attach one link."""
        name = _line(name, 60)
        if not name:
            raise PersonError("web.person_needs_name")
        if kind and ref:
            found = self.find_by_link(kind, ref)
            if found:
                return found.id
        row = self._db.execute(
            "SELECT id FROM persons WHERE display_name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        person_id = int(row["id"]) if row else self.save(None, {"display_name": name})
        if kind and ref:
            self.link(person_id, kind, ref)
        return person_id

    def migrate_existing(
        self,
        *,
        face_names: list[str] | None = None,
        profiles: list[dict[str, Any]] | None = None,
        contacts: list[dict[str, Any]] | None = None,
    ) -> int:
        """One-shot backfill by name match. Safe to call repeatedly."""
        created = 0
        for face in face_names or []:
            before = len(self.all())
            person_id = self.ensure_named(face, kind="face", ref=face)
            person = self.get(person_id)
            roles = set(person.roles if person else [])
            roles.update(("household", "trusted"))
            self.save(person_id, {"display_name": face, "roles": list(roles)})
            if len(self.all()) > before:
                created += 1
        for profile in profiles or []:
            username = _line(profile.get("username") or profile.get("name"), 60)
            face = _line(profile.get("person"), 60)
            if not username:
                continue
            person_id = self.ensure_named(face or username, kind="web_account", ref=username)
            if face:
                self.link(person_id, "face", face)
            for provider in ("telegram", "discord", "signal", "whatsapp"):
                address = _line(profile.get(provider), 120)
                if address:
                    self.link(person_id, "messenger", f"{provider}:{address}")
            person = self.get(person_id)
            roles = set(person.roles if person else [])
            roles.add("household")
            if face:
                roles.add("trusted")
            self.save(
                person_id,
                {
                    "display_name": (person.display_name if person else face or username),
                    "roles": list(roles),
                },
            )
        for contact in contacts or []:
            name = _line(contact.get("name"), 60)
            contact_id = contact.get("id")
            if not name or contact_id is None:
                continue
            person_id = self.ensure_named(name, kind="emergency_contact", ref=str(contact_id))
            account = _line(contact.get("account"), 60)
            if account:
                self.link(person_id, "web_account", account)
            person = self.get(person_id)
            roles = set(person.roles if person else [])
            roles.add("emergency")
            self.save(
                person_id,
                {
                    "display_name": name,
                    "roles": list(roles),
                    "note": _line(contact.get("note"), 300),
                },
            )
        return created

    def _row(self, row: Any) -> Person:
        links = self._db.execute(
            "SELECT kind, ref FROM person_links WHERE person_id = ? ORDER BY kind, ref",
            (row["id"],),
        ).fetchall()
        try:
            roles = json.loads(row["roles"] or "[]")
        except json.JSONDecodeError:
            roles = []
        return Person(
            int(row["id"]),
            row["display_name"],
            row["note"] or "",
            [r for r in roles if r in ROLES],
            [(r["kind"], r["ref"]) for r in links],
        )

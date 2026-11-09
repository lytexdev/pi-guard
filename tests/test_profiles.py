"""Contact details of dashboard accounts, and vCard in and out."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pi_guard.web.profiles import ProfileError, Profiles, clean, from_vcard, to_vcard

from .test_web import PASSWORD, _client, _login


def test_details_are_checked_and_trimmed() -> None:
    assert clean({"name": "  Lena   Krause ", "address": "Weg 1\n\n  12345 Ort ", "x": "y"}) == {
        "name": "Lena Krause", "address": "Weg 1\n12345 Ort"}  # fmt: skip
    with pytest.raises(ProfileError, match="email"):
        clean({"email": "nope"})
    with pytest.raises(ProfileError, match="phone"):
        clean({"phone": "call me"})


def test_details_follow_the_account(db: Any) -> None:
    store = Profiles(db)
    store.set("Lena", {"email": "lena@example.org"})
    assert store.get("lena") == {"email": "lena@example.org"}  # names ignore case
    store.rename("Lena", "LenaK")
    assert store.get("Lena") == {} and store.get("LenaK")["email"] == "lena@example.org"
    store.set("LenaK", {})
    assert store.all() == {}  # nothing left to keep


def test_a_vcard_goes_out_and_comes_back() -> None:
    data = {"name": "Lena; Krause", "email": "lena@example.org", "phone": "+49 170 1234567",
            "address": "Weg 1\n12345 Ort", "note": "Allergie, Nüsse"}  # fmt: skip
    card = to_vcard("lena", data)
    assert card.startswith("BEGIN:VCARD\r\nVERSION:4.0") and "Lena\\; Krause" in card
    back = from_vcard(card)
    assert back == {**data, "phone": "+491701234567"}
    folded = (
        "BEGIN:VCARD\r\nFN:Max Mus\r\n termann\r\nEMAIL;TYPE=work:max@example.org\r\nEND:VCARD\r\n"
    )
    assert from_vcard(folded) == {"name": "Max Mustermann", "email": "max@example.org"}


async def test_people_edit_their_own_and_admins_everyones(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        d = app.dashboard
        d.auth.add_user("lena", "another long password", "viewer")
        saved = await client.put(
            "/api/profiles/lena", json={"phone": "+49 30 123"}, headers=headers
        )
        assert saved.status == 200 and d.profiles.get("lena") == {"phone": "+49 30 123"}
        assert (await client.get("/api/profiles/local")).status == 404  # a login, not a person
        assert (await client.get("/api/profiles/nobody")).status == 404
        mine = await client.put("/api/me/profile", json={"email": "admin@example.org"},
                                headers=headers)  # fmt: skip
        assert mine.status == 200
        card = await client.get("/api/me/profile/vcard")
        assert card.headers["Content-Type"].startswith("text/vcard")
        assert "admin@example.org" in await card.text()
        read = await client.post("/api/me/profile/vcard", data=to_vcard("x", {"email": "a@b.cd"}),
                                 headers=headers)  # fmt: skip
        assert (await read.json()) == {"name": "x", "email": "a@b.cd"}
        assert d.profiles.get("admin") == {"email": "admin@example.org"}  # reading does not save
        body = {"format": "json", "password": PASSWORD}
        exported = await (await client.post("/api/export/data", json=body, headers=headers)).json()
        assert {"username": "lena", "phone": "+49 30 123"} in exported["accounts"]
        change = {"current": PASSWORD, "username": "chef", "password": "a new long password"}
        moved = await client.post("/api/account/credentials", headers=headers, json=change)
        assert moved.status == 200 and d.profiles.get("chef") == {"email": "admin@example.org"}
    finally:
        await client.close()
        app.db.close()


async def test_a_viewer_edits_only_their_own(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        app.dashboard.auth.add_user("lena", "another long password", "viewer")
        login = {"username": "lena", "password": "another long password"}
        assert (await client.post("/api/login", json=login)).status == 200
        csrf = (await (await client.get("/api/session")).json())["csrf"]
        headers = {"X-CSRF-Token": csrf}
        own = await client.put("/api/me/profile", json={"name": "Lena"}, headers=headers)
        assert own.status == 200
        other = await client.put("/api/profiles/admin", json={"name": "x"}, headers=headers)
        assert other.status == 403
    finally:
        await client.close()
        app.db.close()

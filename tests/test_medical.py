"""Emergency medical information: encrypted, password every time, short links for rescuers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pi_guard.addons.medical import MedicalError, MedicalRecords

from .test_ecosystem import enable
from .test_web import PASSWORD, _client, _login

LENA = {"name": "Lena Krause", "birthday": "1950-04-02", "blood_type": "a+",
        "allergies": "Penicillin", "medications": "Marcumar 3 mg morgens"}  # fmt: skip


def test_records_are_encrypted_on_disk(tmp_path: Path) -> None:
    records = MedicalRecords(tmp_path)
    record_id = records.save(None, LENA)
    assert records.read(record_id)["blood_type"] == "A+"
    raw = (tmp_path / "medical.db").read_bytes()
    assert b"Penicillin" not in raw and b"Marcumar" not in raw
    assert (tmp_path / "medical.key").stat().st_mode & 0o077 == 0
    with pytest.raises(MedicalError, match="name"):
        records.save(None, {"allergies": "x"})
    with pytest.raises(MedicalError, match="blood"):
        records.save(None, {"name": "x", "blood_type": "Z"})
    assert records.delete(record_id) and records.people() == []
    records.close()


def test_a_rescue_link_needs_its_pin_and_ends(tmp_path: Path, monkeypatch: Any) -> None:
    from datetime import timedelta

    from pi_guard.addons import medical
    from pi_guard.core.timeutil import utcnow

    records = MedicalRecords(tmp_path)
    record_id = records.save(None, LENA)
    token, pin, _ = records.make_link(record_id, 15, True, "web:admin")
    assert pin is not None and len(pin) == 6
    assert token not in str(records.links()) and records.link_state(token) == {
        "expires": records.links()[0]["expires"], "pin": True}  # fmt: skip
    with pytest.raises(MedicalError):
        records.open_link(token, "000000" if pin != "000000" else "111111")
    name, sheet = records.open_link(token, pin)
    assert name == "Lena Krause" and sheet["allergies"] == "Penicillin"
    for _ in range(4):  # with the one above, five wrong PINs
        with pytest.raises(MedicalError):
            records.open_link(token, "nope")
    assert records.link_state(token) is None  # five wrong PINs end it
    assert records.open_link(token, pin) is None  # also the right one, afterwards
    with pytest.raises(MedicalError, match="minutes"):
        records.make_link(record_id, 600, True, "x")
    open_token, no_pin, _ = records.make_link(record_id, 15, False, "x")
    assert no_pin is None and records.open_link(open_token, "")[0] == "Lena Krause"
    later = utcnow() + timedelta(minutes=16)
    monkeypatch.setattr(medical, "utcnow", lambda: later)
    assert records.link_state(open_token) is None  # ran out
    records.close()


async def test_the_dashboard_asks_for_the_password_every_time(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        app.dashboard.guard.login_allowed = lambda ip: True
        headers = {"X-CSRF-Token": await _login(client)}
        enable(app, medical={"enabled": True})
        created = await client.post("/api/medical", json={"record": LENA, "password": "no"},
                                    headers=headers)  # fmt: skip
        assert created.status == 403
        created = await client.post("/api/medical", json={"record": LENA, "password": PASSWORD},
                                    headers=headers)  # fmt: skip
        record_id = (await created.json())["id"]
        listed = await (await client.get("/api/medical")).json()
        assert listed[0]["name"] == "Lena Krause" and "allergies" not in listed[0]
        seen = await client.post("/api/medical/view", json={"id": record_id, "password": PASSWORD},
                                 headers=headers)  # fmt: skip
        assert (await seen.json())["medications"].startswith("Marcumar")
        body = {"minutes": 30, "pin": True, "password": PASSWORD}
        answer = await client.post(f"/api/medical/{record_id}/link", headers=headers, json=body)
        made = await answer.json()
        token = made["url"].rsplit("/", 1)[1]
        client.session.cookie_jar.clear()  # the rescuer has no login
        page = await client.get(f"/emergency/{token}")
        assert page.status == 200 and "emergency.js" in await page.text()
        state = await (await client.get(f"/api/emergency/{token}")).json()
        assert state["pin"] is True and "name" not in state
        wrong = await client.post(f"/api/emergency/{token}", json={"pin": "x"})
        assert wrong.status == 403
        sheet = await client.post(f"/api/emergency/{token}", json={"pin": made["pin"]})
        assert (await sheet.json())["name"] == "Lena Krause"
        assert (await client.get("/api/medical")).status == 401  # the rest still needs a login
        actions = [r[0] for r in app.db.execute("SELECT action FROM audit")]
        for action in (
            "medical.save",
            "medical.view",
            "medical.link",
            "medical.link_open",
            "medical.link_wrong_pin",
        ):
            assert action in actions
        detail = " ".join(r[0] for r in app.db.execute("SELECT detail FROM audit"))
        assert "Penicillin" not in detail and "Lena" not in detail
    finally:
        await client.close()
        app.db.close()


async def test_viewers_never_see_health_data(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        enable(app, medical={"enabled": True})
        app.dashboard.auth.add_user("gast", "another long password", "viewer")
        login = {"username": "gast", "password": "another long password"}
        assert (await client.post("/api/login", json=login)).status == 200
        assert (await client.get("/api/medical")).status == 403
    finally:
        await client.close()
        app.db.close()

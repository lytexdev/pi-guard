"""Emergency medical information: encrypted, open when logged in, files with the password."""

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


async def test_logged_in_admins_open_a_sheet_and_files_need_the_password(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        app.dashboard.guard.login_allowed = lambda ip: True
        headers = {"X-CSRF-Token": await _login(client)}
        enable(app, medical={"enabled": True})
        created = await client.post("/api/medical", json={"record": LENA}, headers=headers)
        record_id = (await created.json())["id"]
        listed = await (await client.get("/api/medical")).json()
        assert listed[0]["name"] == "Lena Krause" and "allergies" not in listed[0]
        seen = await client.post("/api/medical/view", json={"id": record_id}, headers=headers)
        assert (await seen.json())["medications"].startswith("Marcumar")
        pdf = await client.post(f"/api/medical/{record_id}/pdf", json={}, headers=headers)
        assert pdf.status == 403  # a file leaves the screen: password
        gone = await client.delete(f"/api/medical/{record_id}", json={}, headers=headers)
        assert gone.status == 403
        # No rescue links any more: no public way to the data.
        assert (
            await client.post(f"/api/medical/{record_id}/link", json={}, headers=headers)
        ).status in (404, 405)
        client.session.cookie_jar.clear()
        assert (await client.get("/api/emergency/" + "a" * 32)).status == 401
        assert (await client.get("/emergency/" + "a" * 32)).status == 404
        assert (await client.get("/api/medical")).status == 401
        actions = [r[0] for r in app.db.execute("SELECT action FROM audit")]
        assert "medical.save" in actions and "medical.view" in actions
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


# --- the sheet as PDF and BMP, medicines, linked accounts -----------------------------------

FULL = {**LENA, "meds": [
    {"name": "Ramipril", "strength": "5 mg", "morning": "1", "evening": "1", "unit": "Stück",
     "reason": "Blutdruck", "source": "doctor"},
    {"name": "Ibuprofen", "strength": "400 mg", "schedule": "bei Schmerzen", "kind": "as_needed",
     "source": "self"},
    {"note": "no name: dropped"}],
    "supplements": [{"name": "Vitamin D", "dose": "1000 IE"}],
    "organ_donor": "yes",
    "notes": "# Schlüssel\n- **beim Nachbarn**\n  - Klingel links"}  # fmt: skip


def test_medicines_choices_and_markdown_are_kept(tmp_path: Path) -> None:
    from pi_guard.addons.medical import clean

    values = clean({**FULL, "meds": [*FULL["meds"], {"name": "X", "source": "evil"}]})
    assert [m["name"] for m in values["meds"]] == ["Ramipril", "Ibuprofen", "X"]
    assert values["meds"][0]["kind"] == "regular" and values["meds"][1]["source"] == "self"
    assert values["meds"][2]["source"] == "doctor"  # an unknown choice becomes the default
    assert values["notes"] == "# Schlüssel\n- **beim Nachbarn**\n  - Klingel links"
    old = clean({"name": "A", "organ_donor": "Ausweis im Portemonnaie"})
    assert "organ_donor" not in old and old["organ_donor_note"] == "Ausweis im Portemonnaie"
    with pytest.raises(MedicalError, match="email"):
        clean({"name": "A", "email": "nope"})


def test_the_sheet_as_pdf_puts_danger_first() -> None:
    import io

    import pypdf

    from pi_guard.addons import sheet

    lines = sheet.lines(FULL, "de")
    styles = [style for style, _ in lines]
    assert styles[0] == "title" and styles[1] == "alert"  # allergies before anything else
    texts = [text for _, text in lines]
    assert any("1-0-1-0 Stück" in x and "ärztlich verordnet" in x for x in texts)
    assert any("Eigenregie" in x and "bei Bedarf" in x for x in texts)
    assert "beim Nachbarn" in texts and "**" not in " ".join(texts)
    body = sheet.pdf(FULL, "de", "Stand: heute")
    text = pypdf.PdfReader(io.BytesIO(body)).pages[0].extract_text()
    assert "Lena Krause" in text and "Penicillin" in text and "Schlüssel" in text
    long = {"name": "A", "notes": "\n".join(f"- Zeile {n}" for n in range(200))}
    assert len(pypdf.PdfReader(io.BytesIO(sheet.pdf(long, "en"))).pages) > 1


def test_a_medication_plan_goes_both_ways() -> None:
    from pi_guard.addons import bmp

    plan = bmp.parse(bmp.build(FULL))
    assert [m["name"] for m in plan["meds"]] == ["Ramipril", "Ibuprofen"]
    assert plan["meds"][1] | {"kind": "as_needed", "source": "self"} == plan["meds"][1]
    assert plan["meds"][0]["strength"] == "5 mg" and plan["supplements"][0]["name"] == "Vitamin D"
    assert plan["allergies"] == "Penicillin"
    coded = (
        '<MP v="026"><P g="Max" f="Muster"/><S c="411"><M p="1234567" m="1"/></S>'
        '<S c="414"><M a="Aspirin"><W w="Acetylsalicylsäure" s="500 mg"/></M></S></MP>'
    )
    read = bmp.parse(coded)
    assert read["meds"][0] == {"morning": "1", "name": "PZN 1234567", "kind": "as_needed",
                               "source": "doctor"}  # fmt: skip
    assert read["meds"][1]["source"] == "self" and read["meds"][1]["strength"] == "500 mg"
    for bad in ("", "<x/>", "not xml", '<!DOCTYPE MP [<!ENTITY a "b">]><MP/>', "<MP/>"):
        with pytest.raises(MedicalError):
            bmp.parse(bad)


async def test_files_accounts_and_sending_from_the_dashboard(tmp_path: Path) -> None:
    from types import SimpleNamespace

    client, app = await _client(tmp_path)
    try:
        app.dashboard.guard.login_allowed = lambda ip: True
        headers = {"X-CSRF-Token": await _login(client)}
        enable(app, medical={"enabled": True})
        assert await (await client.get("/api/medical/accounts")).json() == ["admin"]
        body = {"record": {**FULL, "account": "nobody"}, "password": PASSWORD}
        assert (await client.post("/api/medical", json=body, headers=headers)).status == 400
        body["record"]["account"] = "admin"
        record_id = (await (await client.post("/api/medical", json=body, headers=headers)).json())[
            "id"
        ]
        other = {"record": {"name": "B", "account": "Admin"}, "password": PASSWORD}
        assert (await client.post("/api/medical", json=other, headers=headers)).status == 409
        assert app.addons.medical.for_account("ADMIN")[0] == record_id

        pdf = await client.post(f"/api/medical/{record_id}/pdf", headers=headers,
                                json={"password": PASSWORD})  # fmt: skip
        assert pdf.content_type == "application/pdf" and (await pdf.read()).startswith(b"%PDF")
        lines = await client.post(f"/api/medical/{record_id}/pdf", headers=headers,
                                  json={"password": PASSWORD, "format": "lines"})  # fmt: skip
        assert (await lines.json())["lines"][0] == ["title", "Lena Krause"]
        xml = await client.post(f"/api/medical/{record_id}/bmp", headers=headers,
                                json={"password": "wrong"})  # fmt: skip
        assert xml.status == 403
        plan = await client.post("/api/medical/bmp", headers=headers, json={"text": "<x/>"})
        assert plan.status == 400

        sent: list[Any] = []

        async def send(provider: str, chat: str, reply: Any) -> None:
            sent.append((chat, reply.text, reply.document and reply.document.read_bytes()[:4]))

        app.providers["signal"] = SimpleNamespace(supports_buttons=False)
        app.messenger.send = send  # type: ignore[method-assign]
        contacts = app.escalation.contacts
        by_note = contacts.save(None, {"name": "Oma", "medical_mode": "link", "channels": [
            {"provider": "signal", "address": "+4917000001"}]})  # fmt: skip
        by_file = contacts.save(None, {"name": "Arzt", "medical_mode": "sheet", "channels": [
            {"provider": "signal", "address": "+4917000002"}]})  # fmt: skip
        for contact in (by_note, by_file):
            answer = await client.post(f"/api/medical/{record_id}/send", headers=headers,
                                       json={"password": PASSWORD, "contact": contact})  # fmt: skip
            assert answer.status == 200
        # An old "link" contact now gets a note only: no link, no data.
        assert "emergency data" in sent[0][1] and "Penicillin" not in sent[0][1]
        assert "http" not in sent[0][1] and sent[0][2] is None
        assert sent[1][0] == "+4917000002" and sent[1][2] == b"%PDF"
        actions = [r[0] for r in app.db.execute("SELECT action FROM audit")]
        assert {"medical.pdf", "medical.picture", "medical.sent_note", "medical.sent_sheet"} <= set(
            actions
        )

        app.dashboard.auth.add_user("lena", "another long password", "admin")
        users = {u["username"]: u["id"] for u in app.dashboard.auth.users()}
        record = {**FULL, "account": "lena"}
        await client.put(f"/api/medical/{record_id}", json={"record": record, "password": PASSWORD},
                         headers=headers)  # fmt: skip
        assert (
            await client.delete(f"/api/dashboard-users/{users['lena']}", headers=headers)
        ).status == 200
        assert app.addons.medical.for_account("lena") is None  # a new "lena" gets nothing
    finally:
        await client.close()
        app.db.close()

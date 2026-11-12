"""Emergency medical information for logged-in admins; files, sending and deleting need the
password. There are no rescue links: they only worked at home or over the VPN.

See addons/medical.py for how the data are kept. Every view, change and file goes into the
audit log, with the person's record number, never the content.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from pi_guard.addons.medical import MedicalError, MedicalRecords
from pi_guard.core.timeutil import utcnow
from pi_guard.i18n import format_time, t
from pi_guard.web.server import current, dashboard, json_body_limit, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/medical", people),
        web.post("/api/medical/view", view),
        web.post("/api/medical", save),
        web.put("/api/medical/{id:\\d+}", save),
        web.delete("/api/medical/{id:\\d+}", delete),
        web.post("/api/medical/{id:\\d+}/pdf", sheet_pdf),
        web.post("/api/medical/{id:\\d+}/bmp", sheet_bmp),
        web.post("/api/medical/{id:\\d+}/send", send),
        web.post("/api/medical/bmp", read_bmp),
        web.get("/api/medical/accounts", accounts),
        web.get("/api/medical/linked", linked),
        web.put("/api/medical/link-account", link_account),
    ]


def _records(request: web.Request) -> MedicalRecords:
    records = dashboard(request).app.addons.medical
    if records is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return records


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def _confirmed(request: web.Request) -> dict[str, Any]:
    """The request body, after the admin's password: for files, sending and deleting."""
    d = dashboard(request)
    data = await read_json(request)
    if d.auth.check_password(d.auth.account()["username"], str(data.get("password", ""))) is None:
        d.guard.login_failed(d.guard.client_ip(request))
        raise web.HTTPForbidden(text="web.wrong_password")
    return data


async def people(request: web.Request) -> web.Response:
    return web.json_response(_records(request).people())


async def view(request: web.Request) -> web.Response:
    """Logged in is enough to look: the dashboard login is the lock (and it is audited)."""
    records = _records(request)
    data = await read_json(request)
    record_id = int(data.get("id", 0))
    sheet = records.read(record_id)
    if sheet is None:
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "medical.view", str(record_id))
    return web.json_response(sheet, headers={"Cache-Control": "no-store"})


def _account_names(request: web.Request) -> list[str]:
    auth = dashboard(request).auth
    return [auth.account()["username"], *(u["username"] for u in auth.users())]


async def accounts(request: web.Request) -> web.Response:
    """Dashboard accounts a person can be linked to: an SOS from its phone then knows whose
    emergency data it is."""
    return web.json_response(_account_names(request))


async def save(request: web.Request) -> web.Response:
    records = _records(request)
    data = await read_json(request)
    record_id = int(request.match_info["id"]) if "id" in request.match_info else None
    record = dict(data.get("record") or {})
    account = str(record.get("account") or "")
    if account and account.casefold() not in {n.casefold() for n in _account_names(request)}:
        raise web.HTTPBadRequest(text="web.md_unknown_account")
    if account and any(
        p["id"] != record_id and str((records.read(p["id"]) or {}).get("account", "")).casefold()
        == account.casefold() for p in records.people()
    ):  # fmt: skip
        raise web.HTTPConflict(text="web.md_account_taken")
    try:
        saved = records.save(record_id, record)
    except MedicalError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "medical.save", str(saved))
    return web.json_response({"id": saved})


async def linked(request: web.Request) -> web.Response:
    """Which account each sheet belongs to: names and numbers, no health data."""
    records = _records(request)
    rows = []
    for person in records.people():
        sheet = records.read(person["id"]) or {}
        account = sheet.get("account", "")
        rows.append({"id": person["id"], "name": person["name"], "account": account})
    return web.json_response(rows)


async def link_account(request: web.Request) -> web.Response:
    """Give an account its emergency card (or none): from the user list under Access."""
    records = _records(request)
    data = await read_json(request)
    account = str(data.get("account", ""))
    if account.casefold() not in {n.casefold() for n in _account_names(request)}:
        raise web.HTTPBadRequest(text="web.md_unknown_account")
    wanted = int(data.get("id") or 0)
    for person in records.people():
        sheet = records.read(person["id"]) or {}
        mine = str(sheet.get("account", "")).casefold() == account.casefold()
        if person["id"] == wanted and not mine:
            records.save(person["id"], {**sheet, "account": account})
        elif person["id"] != wanted and mine:
            records.save(person["id"], {**sheet, "account": ""})
    dashboard(request).app.db.audit(_actor(request), "medical.account", str(wanted))
    return web.json_response({"ok": True})


async def delete(request: web.Request) -> web.Response:
    records = _records(request)
    await _confirmed(request)
    record_id = int(request.match_info["id"])
    if not records.delete(record_id):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "medical.delete", str(record_id))
    return web.json_response({"ok": True})


def _sheet(request: web.Request, records: MedicalRecords) -> tuple[int, dict[str, Any]]:
    record_id = int(request.match_info["id"])
    sheet = records.read(record_id)
    if sheet is None:
        raise web.HTTPNotFound()
    return record_id, sheet


def _file_name(sheet: dict[str, Any], suffix: str) -> str:
    safe = "".join(c if c.isalnum() else "-" for c in str(sheet.get("name", "")))
    return f"notfall-{safe.strip('-').lower() or 'person'}{suffix}"


async def sheet_pdf(request: web.Request) -> web.Response:
    """The sheet as a PDF, to save, print or pass on by hand; `format: lines` gives its lines
    for the picture the dashboard draws."""
    from pi_guard.addons import sheet as sheets

    records = _records(request)
    data = await _confirmed(request)
    record_id, sheet = _sheet(request, records)
    app = dashboard(request).app
    lang = app.settings.language
    if data.get("format") == "lines":  # the dashboard draws the picture from these
        app.db.audit(_actor(request), "medical.picture", str(record_id))
        return web.json_response({"lines": sheets.lines(sheet, lang)},
                                 headers={"Cache-Control": "no-store"})  # fmt: skip
    stamp = format_time(utcnow(), app.settings.tz, lang, with_date=True)
    footer = t("web.md_pdf_footer", lang, time=stamp)
    body = await asyncio.to_thread(sheets.pdf, sheet, lang, footer)
    app.db.audit(_actor(request), "medical.pdf", str(record_id))
    return web.Response(body=body, content_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{_file_name(sheet, ".pdf")}"',
        "Cache-Control": "no-store"})  # fmt: skip


async def sheet_bmp(request: web.Request) -> web.Response:
    """The medicines as the text of a federal medication plan (BMP)."""
    from pi_guard.addons import bmp

    records = _records(request)
    await _confirmed(request)
    record_id, sheet = _sheet(request, records)
    dashboard(request).app.db.audit(_actor(request), "medical.bmp", str(record_id))
    return web.Response(text=bmp.build(sheet), content_type="application/xml", headers={
        "Content-Disposition": f'attachment; filename="{_file_name(sheet, "-bmp.xml")}"',
        "Cache-Control": "no-store"})  # fmt: skip


async def read_bmp(request: web.Request) -> web.Response:
    """A BMP text from a scanner app, read into medicines for the form. Nothing is stored."""
    from pi_guard.addons import bmp

    _records(request)
    data = await json_body_limit(bmp.MAX_BYTES * 2)(request)
    try:
        return web.json_response(bmp.parse(str(data.get("text", ""))))
    except MedicalError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None


async def send(request: web.Request) -> web.Response:
    """The sheet to one emergency contact, as that contact is set to get it (a note or PDF)."""
    from pi_guard.notifications import sharing

    records = _records(request)
    data = await _confirmed(request)
    record_id, sheet = _sheet(request, records)
    app = dashboard(request).app
    contact = app.escalation.contacts.get(int(data.get("contact", 0)))
    if contact is None:
        raise web.HTTPNotFound(text="web.ec_not_found")
    channels = [(p, a) for p, a in contact.channels if p in app.providers]
    if not channels:
        raise web.HTTPConflict(text="web.ec_unreachable")
    url = sharing.base_url(app, f"{request.scheme}://{request.host}")
    sent = 0
    for provider, address in channels:
        if await sharing.send_medical(app, provider, address, contact, record_id, sheet, url,
                                      _actor(request)):  # fmt: skip
            sent += 1
    if not sent:
        raise web.HTTPBadGateway(text="web.ec_unreachable")
    return web.json_response({"sent": sent, "mode": contact.medical_mode})

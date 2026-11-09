"""Emergency medical information: admins with their password, rescuers with a short link.

See addons/medical.py for how the data are kept. Every view, change and link goes into the
audit log, with the person's record number, never the content.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from pi_guard.addons.medical import MedicalError, MedicalRecords
from pi_guard.web.server import STATIC, current, dashboard, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/medical", people),
        web.post("/api/medical/view", view),
        web.post("/api/medical", save),
        web.put("/api/medical/{id:\\d+}", save),
        web.delete("/api/medical/{id:\\d+}", delete),
        web.get("/api/medical/links", links),
        web.post("/api/medical/{id:\\d+}/link", make_link),
        web.delete("/api/medical/links/{id:[0-9a-f]{16}}", end_link),
        web.get("/emergency/{token:[A-Za-z0-9_-]{20,80}}", rescue_page),
        web.get("/api/emergency/{token:[A-Za-z0-9_-]{20,80}}", rescue_state),
        web.post("/api/emergency/{token:[A-Za-z0-9_-]{20,80}}", rescue_open),
    ]


def _records(request: web.Request) -> MedicalRecords:
    records = dashboard(request).app.addons.medical
    if records is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return records


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def _confirmed(request: web.Request) -> dict[str, Any]:
    """The request body, after the admin's password. Health data need it every time."""
    d = dashboard(request)
    data = await read_json(request)
    if d.auth.check_password(d.auth.account()["username"], str(data.get("password", ""))) is None:
        d.guard.login_failed(d.guard.client_ip(request))
        raise web.HTTPForbidden(text="web.wrong_password")
    return data


async def people(request: web.Request) -> web.Response:
    return web.json_response(_records(request).people())


async def view(request: web.Request) -> web.Response:
    records = _records(request)
    data = await _confirmed(request)
    record_id = int(data.get("id", 0))
    sheet = records.read(record_id)
    if sheet is None:
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "medical.view", str(record_id))
    return web.json_response(sheet, headers={"Cache-Control": "no-store"})


async def save(request: web.Request) -> web.Response:
    records = _records(request)
    data = await _confirmed(request)
    record_id = int(request.match_info["id"]) if "id" in request.match_info else None
    try:
        saved = records.save(record_id, dict(data.get("record") or {}))
    except MedicalError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "medical.save", str(saved))
    return web.json_response({"id": saved})


async def delete(request: web.Request) -> web.Response:
    records = _records(request)
    await _confirmed(request)
    record_id = int(request.match_info["id"])
    if not records.delete(record_id):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "medical.delete", str(record_id))
    return web.json_response({"ok": True})


async def links(request: web.Request) -> web.Response:
    return web.json_response(_records(request).links())


async def make_link(request: web.Request) -> web.Response:
    records = _records(request)
    data = await _confirmed(request)
    record_id = int(request.match_info["id"])
    try:
        minutes, with_pin = int(data.get("minutes", 30)), data.get("pin", True) is not False
        token, pin, expires = records.make_link(record_id, minutes, with_pin, _actor(request))
    except MedicalError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "medical.link", str(record_id))
    url = f"{request.scheme}://{request.host}/emergency/{token}"
    return web.json_response({"url": url, "pin": pin, "expires": expires},
                             headers={"Cache-Control": "no-store"})  # fmt: skip


async def end_link(request: web.Request) -> web.Response:
    if not _records(request).end_link(request.match_info["id"]):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "medical.link_end")
    return web.json_response({"ok": True})


# --- the rescuer's side: no login, the link is the permission ---------------------------------


async def rescue_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(
        STATIC / "emergency.html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex",
        },
    )


async def rescue_state(request: web.Request) -> web.Response:
    """Whether the link is valid and needs a PIN; nothing about the person."""
    records = _records(request)
    state = records.link_state(request.match_info["token"])
    if state is None:
        raise web.HTTPGone(text="expired")
    return web.json_response({**state, "language": dashboard(request).settings.language},
                             headers={"Cache-Control": "no-store"})  # fmt: skip


async def rescue_open(request: web.Request) -> web.Response:
    d = dashboard(request)
    ip = d.guard.client_ip(request)
    if not d.guard.login_allowed(ip):
        raise web.HTTPTooManyRequests(text="Too many attempts")
    records = _records(request)
    pin = str((await read_json(request)).get("pin", ""))[:12]
    try:
        opened = await asyncio.to_thread(records.open_link, request.match_info["token"], pin)
    except MedicalError:
        d.guard.login_failed(ip)
        d.app.db.audit(f"web:{ip}", "medical.link_wrong_pin")
        raise web.HTTPForbidden(text="wrong pin") from None
    if opened is None:
        raise web.HTTPGone(text="expired")
    _, sheet = opened
    d.app.db.audit(f"rescue:{ip}", "medical.link_open")
    return web.json_response(sheet, headers={"Cache-Control": "no-store"})

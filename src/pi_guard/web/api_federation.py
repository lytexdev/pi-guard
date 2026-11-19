"""Linked households in the dashboard: codes, links, and opening a helped dashboard.

A helped dashboard opens under /fed/<id>/ on this Pi, with this Pi's own page and scripts. A
service worker scoped there sends the page's /api/ requests to /fed/<id>/api/, and this Pi
passes them through the link. Only admins of this Pi may open it; the helped Pi decides what
they may do there. Answers are treated as data: nothing from there runs here.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

from aiohttp import web

from pi_guard.federation import protocol as p
from pi_guard.federation.store import FederationError
from pi_guard.web.auth import Session
from pi_guard.web.server import COOKIE, STATIC, confirm_password, current, dashboard, read_json

SAFE_DISPOSITION = re.compile(r'^(attachment|inline)(; ?filename="[A-Za-z0-9._ -]{1,80}")?$')


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/federation", overview),
        web.post("/api/federation/invite", invite),
        web.post("/api/federation/join", join),
        web.patch("/api/federation/links/{id:\\d+}", change),
        web.delete("/api/federation/links/{id:\\d+}", remove),
        web.get("/fed/{id:\\d+}/", boot),
        web.get("/fed/{id:\\d+}/fed-sw.js", worker),
        web.get("/fed/{id:\\d+}/app/", page),
        web.route("*", "/fed/{id:\\d+}/{tail:api/.*}", tunnel),
    ]


def _service(request: web.Request) -> Any:
    federation = dashboard(request).federation
    if federation is None:
        raise web.HTTPConflict(text="web.fed_off")
    return federation


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


def _err(exc: FederationError | p.ProtocolError) -> web.HTTPException:
    key = exc.key if isinstance(exc, FederationError) else str(exc)
    return web.HTTPBadRequest(text=key if key.startswith("web.") else "web.error")


async def overview(request: web.Request) -> web.Response:
    d = dashboard(request)
    s = d.settings
    federation = d.federation
    if federation is None:
        return web.json_response({"enabled": False})
    address: str | None = None
    problem: str | None = None
    try:
        address = federation.address()
    except FederationError as exc:
        problem = exc.key
    links = []
    for link in federation.store.links():
        entry = link.as_dict()
        if link.side == "helper":
            entry["online"] = link.id in federation.peers
            entry["status"] = federation.status.get(link.id)
            entry["alerts"] = list(federation.alerts.get(link.id, []))[:10]
        else:
            entry["online"] = link.id in federation.connected
        links.append(entry)
    return web.json_response({
        "enabled": True, "listen": s.federation_listen, "port": s.federation_port,
        "listen_problem": federation.listen_problem if s.federation_listen else None,
        "address": address, "address_problem": problem, "name": federation.own_name(),
        "fingerprint": p.fingerprint(federation.store.public()), "links": links,
        "roles": list(p.ROLES), "extras": list(p.EXTRAS),
    }, headers={"Cache-Control": "no-store"})  # fmt: skip


async def invite(request: web.Request) -> web.Response:
    """A one-time code for a household this Pi will help. It opens a link: password first."""
    federation = _service(request)
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    try:
        code = federation.invite()
    except FederationError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(_actor(request), "federation.invite", "")
    return web.json_response({"code": code, "hours": 24}, headers={"Cache-Control": "no-store"})


async def join(request: web.Request) -> web.Response:
    """Enter a helper's code here: from now on this Pi connects to it, with the chosen rights."""
    federation = _service(request)
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    raw = data.get("extras")
    extras = [str(x) for x in raw] if isinstance(raw, list) else []
    try:
        code = p.parse_code(str(data.get("code", "")))
        link_id = await federation.join(code, str(data.get("role", "")), extras)
    except (FederationError, p.ProtocolError) as exc:
        raise _err(exc) from None
    link = federation.store.get(link_id)
    detail = f"{link_id} {link.role if link else ''} {','.join(link.extras) if link else ''}"
    dashboard(request).app.db.audit(_actor(request), "federation.join", detail)
    return web.json_response({"id": link_id})


async def change(request: web.Request) -> web.Response:
    """A new name, or (on the helped side) what the helper may do."""
    federation = _service(request)
    data = await read_json(request)
    link_id = int(request.match_info["id"])
    link = federation.store.get(link_id)
    if link is None:
        raise web.HTTPNotFound()
    try:
        if "name" in data:
            federation.store.rename(link_id, str(data["name"]))
        if "role" in data or "extras" in data:
            if link.side != "helped":
                raise web.HTTPBadRequest(text="web.fed_not_yours")
            confirm_password(request, str(data.get("password", "")))
            raw = data.get("extras", link.extras)
            extras = [str(x) for x in raw] if isinstance(raw, list) else link.extras
            federation.store.set_grants(link_id, str(data.get("role", link.role)), extras)
    except FederationError as exc:
        raise _err(exc) from None
    changed = federation.store.get(link_id)
    detail = f"{link_id} {changed.role} {','.join(changed.extras)}" if changed else str(link_id)
    dashboard(request).app.db.audit(_actor(request), "federation.change", detail)
    return web.json_response(changed.as_dict() if changed else {})


async def remove(request: web.Request) -> web.Response:
    """Either side ends a link at once; an open connection is closed."""
    federation = _service(request)
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    link_id = int(request.match_info["id"])
    link = federation.store.remove(link_id)
    if link is None:
        raise web.HTTPNotFound()
    peer = federation.peers.pop(link_id, None)
    if peer is not None:
        await peer.ws.close()
    socket = federation._sockets.pop(link_id, None)
    if socket is not None:
        await socket.close()
    federation.status.pop(link_id, None)
    dashboard(request).app.db.audit(_actor(request), "federation.remove", f"{link_id} {link.side}")
    return web.json_response({"ok": True})


# --- opening a helped dashboard -----------------------------------------------------------


def _admin(request: web.Request, *, writes: bool) -> tuple[Session, int]:
    """These pages are outside /api/: the session and CSRF are checked here."""
    d = dashboard(request)
    _service(request)
    session = d.auth.session(request.cookies.get(COOKIE))
    if session is None or session.pending or session.username.startswith("fed:"):
        raise web.HTTPUnauthorized(text="login required")
    if d.auth.role_of(session.username) != "admin":
        raise web.HTTPForbidden(text="web.fed_admins_only")
    if writes:
        token = request.headers.get("X-CSRF-Token", "")
        if not secrets.compare_digest(token, session.csrf):
            raise web.HTTPForbidden(text="invalid CSRF token")
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != request.host:
            raise web.HTTPForbidden(text="cross-origin request")
    link_id = int(request.match_info["id"])
    link = d.federation.store.get(link_id) if d.federation else None
    if link is None or link.side != "helper":
        raise web.HTTPNotFound()
    return session, link_id


async def boot(request: web.Request) -> web.Response:
    """Installs the service worker for this household, then opens its dashboard."""
    _admin(request, writes=False)
    page = ('<!doctype html><html lang="de"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="robots" content="noindex"><title>pi-guard</title>'
            '<link rel="stylesheet" href="/static/css/app.css">'
            '<script type="module" src="/static/js/fed-boot.js"></script></head>'
            '<body><main class="fed-boot"><p data-fed-boot>…</p></main></body></html>')  # fmt: skip
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})


async def worker(request: web.Request) -> web.FileResponse:
    _admin(request, writes=False)
    headers = {"Content-Type": "text/javascript", "Cache-Control": "no-cache"}
    return web.FileResponse(STATIC / "js" / "fed-sw.js", headers=headers)


async def page(request: web.Request) -> web.FileResponse:
    """This Pi's own dashboard page: only the API answers come from the household."""
    _admin(request, writes=False)
    return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


def _shown(ctype: str, disposition: str) -> tuple[str, str]:
    """A household's answer is data: known safe types inline, everything else as a download,
    so no page from there ever runs on this Pi's address."""
    base = ctype.split(";", 1)[0].strip().lower()
    named = bool(SAFE_DISPOSITION.match(disposition))
    if base in p.INLINE_TYPES:
        return base, disposition if named else ""
    download = disposition if named and disposition.startswith("attachment") else "attachment"
    return "application/octet-stream", download


async def tunnel(request: web.Request) -> web.Response:
    writes = request.method not in ("GET", "HEAD", "OPTIONS")
    session, link_id = _admin(request, writes=writes)
    d = dashboard(request)
    federation = _service(request)
    tail = request.match_info["tail"]
    target = f"/{tail}" + (f"?{request.query_string}" if request.query_string else "")
    if request.content_length and request.content_length > p.MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=p.MAX_BODY, actual_size=request.content_length)
    body = await request.content.read(p.MAX_BODY + 1) if writes else b""
    if len(body) > p.MAX_BODY:
        raise web.HTTPRequestEntityTooLarge(max_size=p.MAX_BODY, actual_size=len(body))
    answer = await federation.request(link_id, request.method, target,
                                        request.headers.get("Content-Type", ""), body)  # fmt: skip
    data = p.unb64(str(answer.get("b", "")))
    status = int(answer.get("s", 502))
    ctype, disposition = _shown(str(answer.get("ct", "")), str(answer.get("cd", "")))
    if target == "/api/session" and status == 200:
        # The helped page sends this Pi's CSRF token back; this Pi checks it (see _admin).
        try:
            session_data = json.loads(data)
        except ValueError:
            raise web.HTTPBadGateway(text="web.fed_bad_answer") from None
        link = federation.store.get(link_id)
        session_data["csrf"] = session.csrf
        session_data["federation"] = {"id": link_id, "name": link.name if link else ""}
        data = json.dumps(session_data).encode()
    headers = {"Cache-Control": "no-store"}
    if disposition:
        headers["Content-Disposition"] = disposition
    if writes:  # what a helper changed, on this side too (the helped Pi keeps its own audit)
        detail = f"{link_id} {request.method} {target.split('?')[0][:80]} {status}"
        d.app.db.audit(f"web:{session.username}", "federation.request", detail)
    return web.Response(body=data, status=status, headers={**headers, "Content-Type": ctype})

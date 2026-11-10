"""Reverse proxy add-on: this Pi as server or client of pgproxy, managed from the dashboard."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from pi_guard.addons.proxy import ANY_ADDRESS, NAME_RE, Proxy, ProxyError
from pi_guard.web.server import confirm_password, current, dashboard, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/proxy", show),
        web.put("/api/proxy/role", role),
        web.post("/api/proxy/clients", add_client),
        web.delete("/api/proxy/clients/{name}", remove_client),
        web.put("/api/proxy/client", set_client),
    ]


def _proxy(request: web.Request) -> Proxy:
    proxy = dashboard(request).app.addons.proxy
    if proxy is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return proxy  # type: ignore[no-any-return]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def show(request: web.Request) -> web.Response:
    return web.json_response(_proxy(request).public())


async def role(request: web.Request) -> web.Response:
    """Opens or closes ports on this Pi, so it needs the password."""
    proxy = _proxy(request)
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    try:
        proxy.set_role(str(data.get("role", "")), data.get("listen_port"))
    except ProxyError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    dashboard(request).app.db.audit(_actor(request), "proxy.role", str(data.get("role")))
    return web.json_response(proxy.public())


async def add_client(request: web.Request) -> web.Response:
    """A token outlives the session: password first. The token is shown once and not kept."""
    proxy = _proxy(request)
    data: dict[str, Any] = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    name = str(data.get("name", "")).strip()
    raw = data.get("ports")
    ports: list[Any] = raw if isinstance(raw, list) else []
    host = str(data.get("host", "")).strip() or request.host.rsplit(":", 1)[0]
    try:
        token = proxy.add_client(name, ports, str(data.get("bind") or ANY_ADDRESS))
    except ProxyError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    dashboard(request).app.db.audit(_actor(request), "proxy.client_added", name)
    return web.json_response({"token": token, "config": proxy.client_config(name, token, host)})


async def remove_client(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if not NAME_RE.match(name) or not _proxy(request).remove_client(name):
        raise web.HTTPNotFound()
    dashboard(request).app.db.audit(_actor(request), "proxy.client_removed", name)
    return web.json_response({"ok": True})


async def set_client(request: web.Request) -> web.Response:
    """This Pi as a client: a service here becomes reachable on another server."""
    proxy = _proxy(request)
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    try:
        proxy.set_client(data)
    except ProxyError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    dashboard(request).app.db.audit(_actor(request), "proxy.client_set", str(data.get("server")))
    return web.json_response(proxy.public())

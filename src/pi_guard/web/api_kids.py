"""Kid Protect in the dashboard: profiles, the devices that use them, pauses and extra time.

Admins only. Every change is written to Pi-hole (and the router) at once, so a parent sees
whether it worked; the add-on's loop keeps it so, also across times without internet.
"""

from __future__ import annotations

from aiohttp import web

from pi_guard.addons.kids import CATEGORIES, DEFAULT_CATEGORIES, Kids, KidsError
from pi_guard.network.pihole import PiholeError
from pi_guard.web.api_network import LABEL_PREFIX
from pi_guard.web.server import current, dashboard, read_json


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/kids", overview),
        web.post("/api/kids/profiles", save),
        web.put("/api/kids/profiles/{id:\\d+}", save),
        web.delete("/api/kids/profiles/{id:\\d+}", delete),
        web.post("/api/kids/profiles/{id:\\d+}/pause", pause),
        web.post("/api/kids/profiles/{id:\\d+}/extra", extra),
        web.put("/api/kids/devices", assign),
    ]


def _kids(request: web.Request) -> Kids:
    kids = dashboard(request).app.addons.kids
    if kids is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return kids


def _audit(request: web.Request, action: str, detail: str = "") -> None:
    dashboard(request).app.db.audit(f"web:{current(request).username}", action, detail)


async def _apply(request: web.Request, kids: Kids) -> str | None:
    """Writes the change through; the problem as text if Pi-hole or the router failed."""
    try:
        result = await kids.sync()
        if result["lists_changed"]:
            dashboard(request).gravity_state = True
            try:
                await kids._pihole.update_gravity()  # type: ignore[union-attr]
            finally:
                dashboard(request).gravity_state = False
        await kids.apply()
    except (PiholeError, OSError, RuntimeError) as exc:
        return str(exc)
    return None


async def _devices(request: web.Request) -> list[dict[str, str]]:
    """Devices in the home network to choose from: MAC, address and the best name known."""
    app = dashboard(request).app
    found: dict[str, dict[str, str]] = {}
    try:
        if app.fritz is not None:
            for host in await app.fritz.hosts():
                found[host.mac.lower()] = {
                    "mac": host.mac.lower(),
                    "ip": host.ip,
                    "name": host.name,
                }
        if app.pihole is not None:
            for device in await app.pihole.devices():
                mac = (device.mac or "").lower()
                if mac and mac not in found:
                    name = device.name or device.vendor or ""
                    found[mac] = {"mac": mac, "ip": next(iter(device.ips), ""), "name": name}
    except Exception:  # the list is a help; the page works without it
        return []
    for mac, item in found.items():
        label = app.db.get(LABEL_PREFIX + mac) or {}
        if isinstance(label, dict) and label.get("name"):
            item["name"] = label["name"]
    return sorted(found.values(), key=lambda d: (d["name"] or "~").casefold())


async def overview(request: web.Request) -> web.Response:
    kids = _kids(request)
    return web.json_response({
        "profiles": kids.profiles(), "categories": list(CATEGORIES),
        "defaults": list(DEFAULT_CATEGORIES), "filters": kids.filters, "router": kids.router,
        "network": await _devices(request),
    })  # fmt: skip


async def save(request: web.Request) -> web.Response:
    kids = _kids(request)
    data = await read_json(request)
    profile_id = int(request.match_info["id"]) if "id" in request.match_info else None
    try:
        saved = kids.save(profile_id, {k: data.get(k) for k in ("name", "categories", "offline")})
    except KidsError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    _audit(request, "kids.save", str(saved))
    return web.json_response({"id": saved, "problem": await _apply(request, kids)})


async def delete(request: web.Request) -> web.Response:
    kids = _kids(request)
    profile_id = int(request.match_info["id"])
    if not kids.delete(profile_id):
        raise web.HTTPNotFound()
    _audit(request, "kids.delete", str(profile_id))
    return web.json_response({"ok": True, "problem": await _apply(request, kids)})


async def _timed(request: web.Request, what: str) -> web.Response:
    kids = _kids(request)
    profile_id = int(request.match_info["id"])
    minutes = (await read_json(request)).get("minutes", 0)
    try:
        getattr(kids, what)(profile_id, int(minutes))
    except (KidsError, ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=getattr(exc, "key", "web.kp_bad_minutes")) from None
    _audit(request, f"kids.{what}", f"{profile_id}:{minutes}")
    return web.json_response({"ok": True, "problem": await _apply(request, kids)})


async def pause(request: web.Request) -> web.Response:
    return await _timed(request, "pause")


async def extra(request: web.Request) -> web.Response:
    return await _timed(request, "extra")


async def assign(request: web.Request) -> web.Response:
    kids = _kids(request)
    data = await read_json(request)
    profile = data.get("profile")
    try:
        kids.assign(str(data.get("mac", "")), int(profile) if profile else None,
                    str(data.get("name", "")))  # fmt: skip
    except KidsError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    _audit(request, "kids.device", str(profile or "-"))
    return web.json_response({"ok": True, "problem": await _apply(request, kids)})

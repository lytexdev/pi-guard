"""The public API for your own scripts and home automation: /api/v1, with a Bearer token.

Tokens are created in the dashboard under Settings › API, either read-only or with control
rights. See docs/api.md; the description below is served as OpenAPI at /api/v1/openapi.json.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from pi_guard.addons.smarthome import SmartHomeError
from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.core.modes import AutoUnavailableError, Mode
from pi_guard.web.server import current, dashboard, read_json

ALARM_SECONDS = 60
ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("GET", "/api/v1/status", "Mode, version and counts"),
    ("GET", "/api/v1/cameras", "All cameras with their state"),
    ("GET", "/api/v1/cameras/{id}/snapshot", "Current picture as JPEG"),
    ("GET", "/api/v1/events", "Latest events (?limit=50)"),
    ("GET", "/api/v1/mode", "Current mode"),
    ("POST", "/api/v1/mode", 'Set the mode: {"mode": "HOME" | "AWAY" | "AUTO"}'),
    ("GET", "/api/v1/addons", "Add-ons and plugins"),
    ("GET", "/api/v1/services", "Service watcher: state of each service"),
    ("GET", "/api/v1/smarthome", "Smart home devices"),
    ("POST", "/api/v1/smarthome/{id}", 'Switch a device: {"on": true | false} or {} to toggle'),
    ("POST", "/api/v1/alarm", 'Alarm on the speaker: {"on": true | false}'),
)


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/v1/openapi.json", openapi),
        web.get("/api/v1/status", status),
        web.get("/api/v1/cameras", cameras),
        web.get("/api/v1/cameras/{id}/snapshot", snapshot),
        web.get("/api/v1/events", events),
        web.get("/api/v1/mode", get_mode),
        web.post("/api/v1/mode", set_mode),
        web.get("/api/v1/addons", addons),
        web.get("/api/v1/services", services),
        web.get("/api/v1/smarthome", smarthome),
        web.post("/api/v1/smarthome/{id}", switch),
        web.post("/api/v1/alarm", alarm),
        web.get("/api/tokens", tokens),
        web.post("/api/tokens", create_token),
        web.delete("/api/tokens/{id:\\d+}", delete_token),
        web.get("/api/docs", docs),
    ]


def _camera(pipeline: Any) -> dict[str, Any]:
    camera = pipeline.camera
    return {"id": camera.id, "name": camera.name, "room": camera.room, "source": camera.source,
            "state": pipeline.status.state.value, "resolution": camera.resolution,
            "recording": pipeline.segment_dir is not None}  # fmt: skip


async def status(request: web.Request) -> web.Response:
    app = dashboard(request).app
    return web.json_response({
        "mode": app.modes.effective.value, "cameras": len(app.cameras),
        "open_alerts": len(app.escalation.open_alerts()), "uptime": round(app.uptime_seconds),
    })  # fmt: skip


async def cameras(request: web.Request) -> web.Response:
    return web.json_response([_camera(p) for p in dashboard(request).app.cameras.values()])


async def snapshot(request: web.Request) -> web.FileResponse:
    app = dashboard(request).app
    camera_id = request.match_info["id"]
    if camera_id not in app.cameras:
        raise web.HTTPNotFound()
    try:
        path = await app.take_photo(camera_id, prefix="api")
    except SnapshotUnavailableError:
        raise web.HTTPServiceUnavailable(text="no picture") from None
    return web.FileResponse(path, headers={"Content-Type": "image/jpeg"})


async def events(request: web.Request) -> web.Response:
    app = dashboard(request).app
    limit = min(int(request.query.get("limit", "50")), 500)
    rows = app.db.execute("SELECT ts, type, camera FROM events ORDER BY id DESC LIMIT ?", (limit,))
    return web.json_response([dict(r) for r in rows.fetchall()])


async def get_mode(request: web.Request) -> web.Response:
    return web.json_response({"mode": dashboard(request).app.modes.effective.value})


async def set_mode(request: web.Request) -> web.Response:
    app = dashboard(request).app
    wanted = str((await read_json(request)).get("mode", "")).upper()
    actor = current(request).username
    if wanted == "AUTO":
        try:
            app.modes.set_auto(actor=actor)
        except AutoUnavailableError:
            raise web.HTTPConflict(text="presence detection is off") from None
    elif wanted in ("HOME", "AWAY"):
        app.modes.set_manual(Mode(wanted), actor=actor)
    else:
        raise web.HTTPBadRequest(text="mode must be HOME, AWAY or AUTO")
    app.db.audit(actor, "api.mode", wanted)
    return web.json_response({"mode": app.modes.effective.value})


async def addons(request: web.Request) -> web.Response:
    app = dashboard(request).app
    names = ("heartbeat", "analytics", "smarthome", "remote", "assistant")
    return web.json_response({
        "addons": {name: getattr(app.addons, name) is not None for name in names},
        "plugins": [p.id for p in app.addons.plugins],
    })  # fmt: skip


def _needed(request: web.Request, name: str) -> Any:
    addon = getattr(dashboard(request).app.addons, name)
    if addon is None:
        raise web.HTTPConflict(text=f"add-on {name} is off")
    return addon


async def services(request: web.Request) -> web.Response:
    return web.json_response(_needed(request, "heartbeat").overview())


async def smarthome(request: web.Request) -> web.Response:
    return web.json_response(_needed(request, "smarthome").overview())


async def switch(request: web.Request) -> web.Response:
    home = _needed(request, "smarthome")
    on = (await read_json(request)).get("on")
    try:
        await home.switch(request.match_info["id"], on if isinstance(on, bool) else None)
    except SmartHomeError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    return web.json_response({"ok": True})


async def alarm(request: web.Request) -> web.Response:
    app = dashboard(request).app
    if app.audio is None:
        raise web.HTTPConflict(text="no speaker")
    if (await read_json(request)).get("on") is True:
        app.audio.start_alarm(ALARM_SECONDS)
    else:
        await app.audio.stop_alarm()
    app.db.audit(current(request).username, "api.alarm")
    return web.json_response({"ok": True})


async def openapi(request: web.Request) -> web.Response:
    paths: dict[str, dict[str, Any]] = {}
    for method, path, summary in ENDPOINTS:
        paths.setdefault(path, {})[method.lower()] = {
            "summary": summary, "responses": {"200": {"description": "OK"}},
        }  # fmt: skip
    return web.json_response({
        "openapi": "3.1.0", "info": {"title": "pi-guard API", "version": "1"}, "paths": paths,
        "components": {"securitySchemes": {"token": {"type": "http", "scheme": "bearer"}}},
        "security": [{"token": []}],
    })  # fmt: skip


async def docs(request: web.Request) -> web.Response:
    """For the API page of the dashboard."""
    plugins = [{"id": p.id, "base": f"/api/plugins/{p.id}/"}
               for p in dashboard(request).app.addons.plugins]  # fmt: skip
    rows = [{"method": m, "path": p, "summary": s} for m, p, s in ENDPOINTS]
    return web.json_response({"endpoints": rows, "plugins": plugins})


async def tokens(request: web.Request) -> web.Response:
    return web.json_response(dashboard(request).auth.tokens())


async def create_token(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    name = str(data.get("name", "")).strip()
    scope = str(data.get("scope", "read"))
    if not name or scope not in ("read", "control"):
        raise web.HTTPBadRequest(text="web.api_bad_token")
    d.app.db.audit(f"web:{current(request).username}", "api.token_add", f"{name} ({scope})")
    return web.json_response({"token": d.auth.create_token(name, scope)})


async def delete_token(request: web.Request) -> web.Response:
    d = dashboard(request)
    if not d.auth.delete_token(int(request.match_info["id"])):
        raise web.HTTPNotFound()
    d.app.db.audit(f"web:{current(request).username}", "api.token_remove")
    return web.json_response({"ok": True})

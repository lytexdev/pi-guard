"""Overview, events, statistics, logs and mode."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any

from aiohttp import web

from pi_guard import CREDIT, __version__
from pi_guard.core.modes import AutoUnavailableError, Mode
from pi_guard.core.timeutil import utcnow
from pi_guard.diagnostics.system import system_snapshot
from pi_guard.i18n import LANGUAGES, catalog
from pi_guard.logs import RECENT
from pi_guard.security.files import is_within
from pi_guard.web.server import current, dashboard, read_json

MEDIA_KINDS = ("snapshots", "clips", "audio", "evidence")


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/i18n/{lang}", i18n),
        web.get("/api/overview", overview),
        web.get("/api/events", events),
        web.get("/api/stats", stats),
        web.get("/api/logs", logs),
        web.get("/api/audit", audit),
        web.post("/api/mode", set_mode),
        web.get("/api/alerts", alerts),
        web.post("/api/alerts/ack", acknowledge),
        web.get("/api/media/{kind}/{name}", media),
    ]


async def i18n(request: web.Request) -> web.Response:
    lang = request.match_info["lang"]
    if lang not in LANGUAGES:
        raise web.HTTPNotFound()
    texts = {k: v for k, v in catalog(lang).items()
             if k.startswith(("web.", "menu.", "mode.", "event.", "camera.state.", "presence.",
                              "image.", "faces.", "status.", "addons.", "remote.", "smarthome.",
                              "assistant.", "analytics.", "knowledge.", "cmd.", "voice."))
             }  # fmt: skip
    response = web.json_response(
        texts, headers={"Cache-Control": "max-age=300", "Vary": "Accept-Encoding"}
    )
    response.enable_compression()  # the largest file of a start; holds no secrets (BREACH)
    return response


def camera_info(pipeline: Any) -> dict[str, Any]:
    status = pipeline.status
    last = status.last_frame_at
    return {
        "id": pipeline.camera.id,
        "name": pipeline.camera.name,
        "room": pipeline.camera.room,
        "source": pipeline.camera.source,
        "state": status.state.value,
        "recording": pipeline.segment_dir is not None,
        "resolution": pipeline.camera.resolution,
        "fps": pipeline.camera.fps,
        "encoder": status.encoder,
        "input_format": status.input_format,
        "error": status.last_error,
        "frame_age": round(time.monotonic() - last, 1) if last else None,
        "microphone": pipeline.microphone,
    }


async def overview(request: web.Request) -> web.Response:
    app = dashboard(request).app
    snap = system_snapshot(app.settings.recordings_path)
    state = app.modes.state
    day_ago = utcnow() - timedelta(days=1)
    counts = app.db.execute(
        "SELECT type, COUNT(*) AS n FROM events WHERE ts >= ? GROUP BY type",
        (day_ago.isoformat(),),
    ).fetchall()
    return web.json_response({
        "version": __version__,
        "credit": CREDIT,
        "uptime": round(app.uptime_seconds),
        "cameras": [camera_info(p) for p in app.cameras.values()],
        "mode": {
            "effective": state.effective.value,
            "selection": state.selection.value,
            "presence": state.presence.value,
            "until": state.manual_until.isoformat() if state.manual_until else None,
            "presence_enabled": app.modes.presence_enabled,
        },
        "system": {
            "disk_used_percent": round(snap.disk_used_percent, 1),
            "disk_free_gb": round(snap.disk_free_gb, 1),
            "load": round(snap.load1, 2),
            "cpus": snap.cpu_count,
            "memory_used_percent": round(snap.memory_used_percent, 1),
            "temperature": snap.temperature_c,
        },
        "components": [
            {"name": c.name, "running": c.running, "restarts": c.restarts, "error": c.last_error}
            for c in app.components.values()
        ],
        "last_24h": {row["type"]: row["n"] for row in counts},
        "open_alerts": len(app.escalation.open_alerts()),
        "emergency_contacts": len(app.escalation.contacts.all()),
        "emergency_after_alerts": app.escalation.after_alerts,
        "emergency_after_minutes": int(app.escalation.after.total_seconds() // 60),
        "features": {
            "audio_playback": bool(app.audio and app.audio.playback_enabled),
            "audio_recording": bool(app.audio and app.audio.recording_enabled),
            "faces": app.faces is not None,
            "presence": app.presence is not None,
            "pihole": app.pihole is not None,
            "push": app.webpush is not None,
        },
    })  # fmt: skip


async def events(request: web.Request) -> web.Response:
    app = dashboard(request).app
    q = request.query
    limit = min(int(q.get("limit", "50")), 500)
    sql, params = "SELECT * FROM events WHERE 1=1", []
    if q.get("type"):
        sql += " AND type = ?"
        params.append(q["type"])
    if q.get("camera"):
        sql += " AND camera = ?"
        params.append(q["camera"])
    if q.get("before"):
        sql += " AND ts < ?"
        params.append(q["before"])
    rows = app.db.execute(sql + " ORDER BY ts DESC LIMIT ?", (*params, limit)).fetchall()
    return web.json_response([
        {"id": r["id"], "ts": r["ts"], "type": r["type"], "camera": r["camera"],
         "data": json.loads(r["data"])}
        for r in rows
    ])  # fmt: skip


async def stats(request: web.Request) -> web.Response:
    app = dashboard(request).app
    days = min(int(request.query.get("days", "14")), 90)
    since = (utcnow() - timedelta(days=days)).isoformat()
    daily = app.db.execute(
        "SELECT substr(ts, 1, 10) AS day, type, COUNT(*) AS n FROM events WHERE ts >= ? "
        "GROUP BY day, type ORDER BY day",
        (since,),
    ).fetchall()
    hourly = app.db.execute(
        "SELECT CAST(substr(ts, 12, 2) AS INTEGER) AS hour, COUNT(*) AS n FROM events "
        "WHERE ts >= ? AND type = 'motion_detected' GROUP BY hour",
        (since,),
    ).fetchall()
    return web.json_response({
        "daily": [dict(r) for r in daily],
        "motion_by_hour_utc": {r["hour"]: r["n"] for r in hourly},
        "timezone": app.settings.timezone,
    })  # fmt: skip


async def logs(request: web.Request) -> web.Response:
    level = request.query.get("level", "").upper()
    order = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    minimum = order.get(level, 0)
    records = [r for r in RECENT.records if order.get(r["level"], 0) >= minimum]
    return web.json_response(records[-int(request.query.get("limit", "300")) :][::-1])


async def audit(request: web.Request) -> web.Response:
    app = dashboard(request).app
    rows = app.db.execute("SELECT ts, actor, action, detail FROM audit ORDER BY ts DESC LIMIT 200")
    return web.json_response([dict(r) for r in rows])


async def set_mode(request: web.Request) -> web.Response:
    app = dashboard(request).app
    data = await read_json(request)
    actor = f"web:{current(request).username}"
    target = str(data.get("mode", "")).upper()
    try:
        if target == "AUTO":
            app.modes.set_auto(actor=actor)
        elif target in ("HOME", "AWAY"):
            if target == "HOME":
                app.escalation.acknowledge(actor)
            minutes = data.get("minutes")
            duration = timedelta(minutes=int(minutes)) if minutes else None
            app.modes.set_manual(Mode(target), duration, actor=actor)
        else:
            raise web.HTTPBadRequest(text="mode must be HOME, AWAY or AUTO")
    except AutoUnavailableError:
        raise web.HTTPConflict(text="presence detection is not enabled") from None
    return web.json_response({"effective": app.modes.effective.value})


async def media(request: web.Request) -> web.FileResponse:
    app = dashboard(request).app
    kind, name = request.match_info["kind"], request.match_info["name"]
    root = app.media_dir / kind
    path = root / name
    if kind not in MEDIA_KINDS or "/" in name or not is_within(path, root) or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def alerts(request: web.Request) -> web.Response:
    app = dashboard(request).app
    return web.json_response([
        {"id": a.id, "ts": a.ts.isoformat(), "type": a.type, "camera": a.camera,
         "snapshot": a.snapshot, "escalated": a.escalated}
        for a in app.escalation.open_alerts()
    ])  # fmt: skip


async def acknowledge(request: web.Request) -> web.Response:
    app = dashboard(request).app
    count = app.escalation.acknowledge(f"web:{current(request).username}")
    return web.json_response({"acknowledged": count})

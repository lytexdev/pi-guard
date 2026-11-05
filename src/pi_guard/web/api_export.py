"""Take your data and settings with you: JSON, TXT and CSV, and a config.json to import again."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from pi_guard import __version__
from pi_guard.config import OVERRIDE_ENV, read_overrides
from pi_guard.config.files import FileConfig
from pi_guard.config.settings import Settings
from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import atomic_write
from pi_guard.web.api_manage import saved_config, write_config
from pi_guard.web.server import current, dashboard, read_json
from pi_guard.web.settings_schema import BY_KEY, FIELDS

FORMAT_VERSION = 1


def routes() -> list[web.RouteDef]:
    return [
        web.post("/api/export/data", export_data),
        web.post("/api/export/config", export_config),
        web.post("/api/import/config", import_config),
    ]


def _confirm(request: web.Request, data: dict[str, Any]) -> None:
    d = dashboard(request)
    if d.auth.check_password(d.auth.account()["username"], str(data.get("password", ""))) is None:
        raise web.HTTPForbidden(text="web.wrong_password")


def _rows(app: Any, sql: str) -> list[dict[str, Any]]:
    return [dict(r) for r in app.db.execute(sql).fetchall()]


def collect(request: web.Request) -> dict[str, list[dict[str, Any]]]:
    return collect_all(dashboard(request))


def collect_all(d: Any) -> dict[str, list[dict[str, Any]]]:
    """Everything stored about this home. Keys, tokens and face prints stay out."""
    app = d.app
    data = {
        "events": _rows(app, "SELECT ts, type, camera, data FROM events ORDER BY id LIMIT 100000"),
        "audit": _rows(app, "SELECT ts, actor, action, detail FROM audit ORDER BY id LIMIT 100000"),
        "users": _rows(app, "SELECT provider, user_id, name, role, language FROM users"),
        "cameras": [{"id": c.id, "name": c.name, "room": c.room, "source": c.source}
                    for c in app.config.file.cameras],
    }  # fmt: skip
    if app.faces is not None:
        data["people"] = [{"name": n, "samples": c, "enrolled": e,
                           "profile": app.faces.store.profile(n)}
                          for n, c, e in app.faces.store.people()]  # fmt: skip
    if d.wg_peers is not None:
        data["vpn_devices"] = [{k: p[k] for k in ("name", "address", "created")}
                               for p in d.wg_peers.list()]  # fmt: skip
    if app.addons.blocks is not None:
        data["blocked_sites"] = [{k: b[k] for k in ("domain", "device", "label", "until")}
                                 for b in app.addons.blocks.list()]  # fmt: skip
    if app.addons.agents is not None:
        data["computers"] = [{k: a[k] for k in ("name", "created", "last_seen")}
                             for a in app.addons.agents.list()]  # fmt: skip
    labels = app.db.execute("SELECT key, value FROM kv WHERE key LIKE 'lan.label.%'").fetchall()
    data["device_names"] = [{"mac": r["key"].rsplit(".", 1)[-1], **json.loads(r["value"])}
                            for r in labels]  # fmt: skip
    return data


def as_text(data: dict[str, list[dict[str, Any]]]) -> str:
    lines = [f"pi-guard export, {utcnow():%Y-%m-%d %H:%M} UTC", ""]
    for section, rows in data.items():
        lines += [f"== {section} ({len(rows)}) =="]
        lines += [" | ".join(f"{k}: {v}" for k, v in row.items()) for row in rows]
        lines.append("")
    return "\n".join(lines)


def as_csv_zip(data: dict[str, list[dict[str, Any]]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for section, rows in data.items():
            text = io.StringIO()
            if rows:
                writer = csv.DictWriter(text, fieldnames=list(rows[0]), extrasaction="ignore")
                writer.writeheader()
                writer.writerows([{k: _safe_cell(v) for k, v in row.items()} for row in rows])
            bundle.writestr(f"{section}.csv", text.getvalue())
    return buffer.getvalue()


def _safe_cell(value: Any) -> Any:
    """Spreadsheets run cells starting with these as formulas; events can contain foreign text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _attachment(body: bytes | str, name: str, kind: str) -> web.Response:
    payload = body.encode() if isinstance(body, str) else body
    headers = {"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"}
    return web.Response(body=payload, content_type=kind, headers=headers)


async def export_data(request: web.Request) -> web.Response:
    body = await read_json(request)
    _confirm(request, body)
    kind = str(body.get("format", "json"))
    data = await asyncio.to_thread(collect, request)
    dashboard(request).app.db.audit(f"web:{current(request).username}", "export.data", kind)
    stamp = f"{utcnow():%Y%m%d}"
    if kind == "json":
        return _attachment(json.dumps(data, indent=2, ensure_ascii=False),
                           f"pi-guard-{stamp}.json", "application/json")  # fmt: skip
    if kind == "txt":
        return _attachment(as_text(data), f"pi-guard-{stamp}.txt", "text/plain")
    if kind == "csv":
        return _attachment(as_csv_zip(data), f"pi-guard-{stamp}-csv.zip", "application/zip")
    raise web.HTTPBadRequest(text="format")


async def export_config(request: web.Request) -> web.Response:
    d = dashboard(request)
    body = await read_json(request)
    _confirm(request, body)
    secrets_too = body.get("secrets") is True
    settings: dict[str, Any] = {}
    for field in FIELDS:
        value = getattr(d.settings, field.key)
        if field.kind == "secret":
            if secrets_too and value is not None:
                settings[field.key] = value.get_secret_value()
            continue
        settings[field.key] = value.value if hasattr(value, "value") else value
    config = {
        "pi_guard_config": FORMAT_VERSION, "version": __version__,
        "exported": utcnow().isoformat(), "contains_secrets": secrets_too,
        "settings": json.loads(json.dumps(settings, default=str)), "config": saved_config(d),
    }  # fmt: skip
    d.app.db.audit(f"web:{current(request).username}", "export.config",
                   "with secrets" if secrets_too else "")  # fmt: skip
    return _attachment(json.dumps(config, indent=2, ensure_ascii=False), "config.json",
                       "application/json")  # fmt: skip


async def import_config(request: web.Request) -> web.Response:
    """Everything is checked first; a file with a mistake changes nothing."""
    d = dashboard(request)
    body = await read_json(request)
    _confirm(request, body)
    data = body.get("config")
    if not isinstance(data, dict) or data.get("pi_guard_config") != FORMAT_VERSION:
        raise web.HTTPBadRequest(text="web.ex_bad_file")
    try:
        file = FileConfig.model_validate(data.get("config") or {})
    except ValidationError as exc:
        raise web.HTTPBadRequest(text=str(exc.errors()[0]["msg"])[:200]) from None
    from pi_guard.web.settings_schema import to_env

    rendered = dict(read_overrides(d.settings.data_dir))
    skipped = 0
    try:
        for key, value in (data.get("settings") or {}).items():
            field = BY_KEY.get(key)
            if field is None or value is None or value == "":
                skipped += 1
                continue
            rendered[key] = to_env(field, value)
        base = {k: v for k, v in d.settings.model_dump().items() if k != "telegram_bot_id"}
        Settings(**{**base, **rendered})
    except (ValueError, ValidationError) as exc:
        raise web.HTTPBadRequest(text=str(exc)[:300]) from None
    lines = ["# Written by the pi-guard dashboard; takes precedence over pi-guard.env."]
    lines += [f"PI_GUARD_{key.upper()}={value}" for key, value in sorted(rendered.items())]
    await asyncio.to_thread(atomic_write, d.settings.data_dir / OVERRIDE_ENV,
                            ("\n".join(lines) + "\n").encode())  # fmt: skip
    await write_config(d, file)
    d.app.db.audit(f"web:{current(request).username}", "import.config")
    return web.json_response({"ok": True, "skipped": skipped, "restart_required": True})

"""The apps around pi-guard: which versions this Pi hands out, and invitations to download them.

An invitation is a link that is good for a few days and only for downloads: the phone app and
the desktop app, both signed, the same files as on GitHub. It opens nothing else; whoever gets
it still needs an account to log in. Links are kept as SHA-256 only, like API tokens.
"""

from __future__ import annotations

import hashlib
import html
import json
import secrets
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from pi_guard import __version__
from pi_guard.web.server import current, dashboard, read_json

INVITES = "apps.invites"
INVITE_DAYS = 7
MAX_INVITES = 20
_TOKEN_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
DESKTOP_FILES = {
    "linux-x64": ("PiGuard-linux-x64.tar.gz", "application/gzip"),
    "win-x64": ("PiGuard-Setup.exe", "application/vnd.microsoft.portable-executable"),
}
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/apps", apps),
        web.post("/api/apps/invite", invite_create),
        web.delete("/api/apps/invite", invite_clear),
        web.get("/get/{token}", invite_page),
        web.get("/get/{token}/android", invite_android),
        web.get("/get/{token}/desktop/{platform}", invite_desktop),
    ]


def _meta(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def versions(data_dir: Path) -> dict[str, Any]:
    """What this Pi runs and hands out to its devices."""
    android = _meta(data_dir / "apps" / "android" / "latest.json") or {}
    desktop = {platform: (_meta(data_dir / "apps" / "desktop" / platform / "latest.json") or {})
               .get("version") for platform in DESKTOP_FILES}  # fmt: skip
    agent_dir = data_dir / "apps" / "agent"
    agents = {p.name: (_meta(p / "latest.json") or {}).get("version")
              for p in sorted(agent_dir.iterdir())} if agent_dir.is_dir() else {}  # fmt: skip
    return {
        "pi": __version__,
        "android": android.get("version_name"),
        "android_code": android.get("version_code"),
        "desktop": {k: v for k, v in desktop.items() if v},
        "agent": {k: v for k, v in agents.items() if v},
    }


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _live(d: Any) -> list[dict[str, Any]]:
    now = time.time()
    return [i for i in (d.app.db.get(INVITES) or []) if i.get("expires", 0) > now]


def _valid(request: web.Request) -> None:
    token = request.match_info["token"]
    if not 20 <= len(token) <= 64 or not set(token) <= _TOKEN_CHARS:
        raise web.HTTPNotFound()
    wanted = _digest(token)
    if not any(secrets.compare_digest(wanted, i["hash"]) for i in _live(dashboard(request))):
        raise web.HTTPNotFound()


async def apps(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response({**versions(d.settings.data_dir), "invites": len(_live(d)),
                              "invite_days": INVITE_DAYS})  # fmt: skip


async def invite_create(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    note = " ".join(str(data.get("note", "")).split())[:40]
    token = secrets.token_urlsafe(24)
    invites = _live(d)[-(MAX_INVITES - 1) :]
    expires = time.time() + INVITE_DAYS * 86400
    invites.append({"hash": _digest(token), "expires": expires, "note": note})
    d.app.db.set(INVITES, invites)
    d.app.db.audit(f"web:{current(request).username}", "apps.invite", note)
    return web.json_response({"path": f"/get/{token}", "expires": expires})


async def invite_clear(request: web.Request) -> web.Response:
    d = dashboard(request)
    d.app.db.set(INVITES, [])
    d.app.db.audit(f"web:{current(request).username}", "apps.invite_clear")
    return web.json_response({"ok": True})


def _page(title: str, body: str) -> web.Response:
    page = (f'<!doctype html><html lang="de"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex"><title>{html.escape(title)}</title>'
            f'<link rel="stylesheet" href="/static/css/emergency.css"></head>'
            f'<body><main class="invite">{body}</main></body></html>')  # fmt: skip
    return web.Response(text=page, content_type="text/html", headers=NO_STORE)


async def invite_page(request: web.Request) -> web.Response:
    _valid(request)
    d = dashboard(request)
    token = request.match_info["token"]
    have = versions(d.settings.data_dir)
    name = html.escape((d.app.db.get("web.appearance") or {}).get("name") or "pi-guard")
    links = []
    if have["android"]:
        links.append(f'<li><a class="button" href="/get/{token}/android">Android-App '
                     f'{html.escape(str(have["android"]))} (APK)</a></li>')  # fmt: skip
    labels = {"linux-x64": "Linux", "win-x64": "Windows"}
    for platform, version in have["desktop"].items():
        links.append(f'<li><a class="button" href="/get/{token}/desktop/{platform}">Desktop-App '
                     f'{labels[platform]} {html.escape(str(version))}</a></li>')  # fmt: skip
    items = "".join(links) or "<li>Noch keine Apps auf diesem Gerät. / No apps here yet.</li>"
    body = (f"<h1>{name}</h1>"
            "<p>Du wurdest eingeladen, die App herunterzuladen. Danach meldest du dich mit deinem "
            "eigenen Konto an. / You were invited to download the app; you then log in with your "
            "own account.</p>"
            f'<ul class="downloads">{items}</ul>'
            "<p><small>Android: Installation aus dieser Quelle einmal erlauben. Die Apps sind "
            "signiert und aktualisieren sich danach selbst. / Android: allow installing from this "
            "source once. The apps are signed and keep themselves current.</small></p>"
            )  # fmt: skip
    return _page(name, body)


async def invite_android(request: web.Request) -> web.StreamResponse:
    _valid(request)
    path = dashboard(request).settings.data_dir / "apps" / "android" / "latest.apk"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": 'attachment; filename="PiGuard.apk"', "Cache-Control": "no-store",
    })  # fmt: skip


async def invite_desktop(request: web.Request) -> web.StreamResponse:
    _valid(request)
    platform = request.match_info["platform"]
    if platform not in DESKTOP_FILES:
        raise web.HTTPNotFound()
    path = dashboard(request).settings.data_dir / "apps" / "desktop" / platform / "latest.bin"
    if not path.is_file():
        raise web.HTTPNotFound()
    filename, kind = DESKTOP_FILES[platform]
    return web.FileResponse(path, headers={
        "Content-Type": kind, "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })  # fmt: skip

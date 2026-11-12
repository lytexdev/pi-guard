"""Hands signed releases to agents and desktop apps.

This only passes files on. They are signed with a key that never comes to this device, and
every client checks that itself, so nothing here has to be trusted, and nothing here is secret.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from aiohttp import web

from pi_guard.web.server import dashboard

PRODUCTS = ("agent", "desktop")
_PLATFORM = re.compile(r"[a-z0-9_-]{1,32}")


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/updates/{product}/{platform}", latest),
        web.get("/api/updates/{product}/{platform}/file", download),
    ]


def _folder(request: web.Request) -> Path:
    product, platform = request.match_info["product"], request.match_info["platform"]
    if product not in PRODUCTS or not _PLATFORM.fullmatch(platform):
        raise web.HTTPNotFound()
    return dashboard(request).settings.data_dir / "apps" / product / platform


async def latest(request: web.Request) -> web.Response:
    folder = _folder(request)
    if request.query.get("check") == "1":  # "Check for updates" in the app; rate-limited there
        await dashboard(request).app.app_updates.ask()
    meta = folder / "latest.json"
    if not meta.is_file():
        return web.json_response({})
    return web.json_response(json.loads(meta.read_text()))


async def download(request: web.Request) -> web.StreamResponse:
    path = _folder(request) / "latest.bin"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "application/octet-stream"})

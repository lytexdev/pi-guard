"""Moving: what changed in the network, what to do, and a moving file for a new Pi.

Admins only. The moving file is an encrypted backup (see backup.py) handed to the browser
instead of a backup target, so a new Pi can take everything over with scripts/restore.sh.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from aiohttp import web

from pi_guard.backup import MIN_PASSPHRASE, PREFIX, SUFFIX, archive, collect, encrypt
from pi_guard.core.timeutil import utcnow
from pi_guard.network import move
from pi_guard.network.reach import with_port
from pi_guard.web.server import confirm_password, current, dashboard, read_json

CONFIG_DIR = Path("/etc/pi-guard")


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/move", report),
        web.post("/api/move/home", set_home),
        web.post("/api/move/endpoint", set_endpoint),
        web.post("/api/move/package", package),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


async def report(request: web.Request) -> web.Response:
    """The checks. Asks the internet for the public address only with ?check=1."""
    from pi_guard.web.api_addons import home_ip

    public = await home_ip() if request.query.get("check") else None
    return web.json_response(await move.report(dashboard(request).app, public),
                             headers={"Cache-Control": "no-store"})  # fmt: skip


async def set_home(request: web.Request) -> web.Response:
    """The network the Pi sees now is home from now on."""
    app = dashboard(request).app
    net = await move.current()
    app.db.set(move.HOME_KEY, net.as_dict())
    app.db.audit(_actor(request), "move.home")
    return web.json_response({"ok": True, "home": net.as_dict()})


async def set_endpoint(request: web.Request) -> web.Response:
    """The VPN's address for phones and computers becomes the new public address."""
    from pi_guard.web.api_addons import home_ip
    from pi_guard.web.api_manage import apply_settings

    d = dashboard(request)
    confirm_password(request, str((await read_json(request)).get("password", "")))
    public = await home_ip()
    if not public:
        raise web.HTTPBadGateway(text="web.mv_no_public")
    old = d.settings.wireguard_endpoint or ""
    port = old.rsplit(":", 1)[1] if old.count(":") == 1 else None
    endpoint = with_port(public, port) if port else public
    await apply_settings(d, {"wireguard_endpoint": endpoint}, _actor(request))
    d.app.db.audit(_actor(request), "move.endpoint")
    return web.json_response({"endpoint": endpoint, "restart_required": True})


async def package(request: web.Request) -> web.StreamResponse:
    """An encrypted backup of configuration and data, straight to the browser."""
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    passphrase = str(data.get("passphrase", ""))
    if len(passphrase) < MIN_PASSPHRASE:
        raise web.HTTPBadRequest(text="web.mv_short_passphrase")
    d = dashboard(request)
    s = d.settings
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    name = f"{PREFIX}{stamp}{SUFFIX}"
    with tempfile.TemporaryDirectory(prefix="pg-move-", dir=s.data_dir) as tmp:
        work = Path(tmp)
        work.chmod(0o700)
        staging, plain, sealed = work / "staging", work / "move.tar.gz", work / name

        def build() -> None:
            config = CONFIG_DIR if CONFIG_DIR.is_dir() else None
            collect(s.data_dir, config, staging, bool(data.get("recordings")))
            archive(staging, plain)
            encrypt(plain, sealed, passphrase)

        await asyncio.to_thread(build)
        d.app.db.audit(_actor(request), "move.package")
        response = web.StreamResponse(headers={
            "Content-Type": "application/octet-stream", "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Length": str(sealed.stat().st_size)})  # fmt: skip
        await response.prepare(request)
        with sealed.open("rb") as handle:
            while chunk := await asyncio.to_thread(handle.read, 1 << 20):
                await response.write(chunk)
        await response.write_eof()
        return response

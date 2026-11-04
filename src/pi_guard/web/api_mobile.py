"""The app's side of the server, and the dashboard's page for phones."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from pi_guard.addons.mobile import MobileHub, map_link
from pi_guard.core.permissions import Role, User
from pi_guard.core.schedule import CALENDAR_KEY, clean_calendar
from pi_guard.i18n import t
from pi_guard.messaging.base import ProviderUnavailableError, Reply
from pi_guard.network.wgpeers import PeerError
from pi_guard.security.files import atomic_write
from pi_guard.web.api_network import _config, _server, _store
from pi_guard.web.server import COOKIE, current, dashboard, read_json

log = logging.getLogger(__name__)

MAX_MESSAGE = 1_000_000
MAX_APK = 200_000_000
APP_DIR = "apps/android"


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/hello", hello),
        web.post("/api/mobile/pair", pair),
        web.post("/api/mobile/session", device_session),
        web.get("/api/mobile/ws", device_socket),
        web.post("/api/mobile/report", report),
        web.post("/api/mobile/emergency", emergency),
        web.post("/api/mobile/calendar", calendar),
        web.post("/api/mobile/vpn", vpn),
        web.get("/api/mobile/app/latest", app_latest),
        web.get("/api/mobile/app/apk", app_apk),
        web.post("/api/mobile/app", app_upload),
        web.get("/api/mobile/devices", devices),
        web.delete("/api/mobile/devices/{id:\\d+}", device_delete),
        web.post("/api/mobile/devices/{id:\\d+}/find", device_find),
        web.post("/api/mobile/devices/{id:\\d+}/stop", device_stop),
    ]


def _hub(request: web.Request) -> MobileHub:
    hub = dashboard(request).app.mobile
    if hub is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return hub


def _label(device_id: int) -> str:
    return f"pi-guard app {device_id}"


def _device(request: web.Request) -> dict[str, Any]:
    d = dashboard(request)
    header = request.headers.get("Authorization", "")
    found = (
        _hub(request).authenticate(header[7:].strip()[:100])
        if header.startswith("Bearer ")
        else None
    )
    if found is None:
        ip = d.guard.client_ip(request)
        d.guard.login_failed(ip)
        d.app.db.audit(f"web:{ip}", "login.failed", "mobile token")
        raise web.HTTPUnauthorized(text="invalid token")
    return found


async def hello(request: web.Request) -> web.Response:
    """Lets the app recognise an instance in the network. Says nothing else about it."""
    return web.json_response({"app": "pi-guard"})


async def pair(request: web.Request) -> web.Response:
    """After a normal login, and only from the home network or the VPN."""
    d = dashboard(request)
    hub = _hub(request)
    address = ipaddress.ip_address(d.guard.client_ip(request))
    if not address.is_private:
        raise web.HTTPForbidden(text="web.mobile_home_only")
    data = await read_json(request)
    username = current(request).username
    try:
        device_id, token = hub.create(
            str(data.get("name", "")), str(data.get("platform", "")), username
        )
    except ValueError:
        raise web.HTTPBadRequest(text="web.error") from None
    role = Role(d.auth.role_of(username))
    name = (hub.get(device_id) or {}).get("name", "")
    d.app.users.save(User("mobile", str(device_id), role, name=name))
    # The login that paired it is the phone's now, and ends with it.
    d.auth.label_session(request.cookies.get(COOKIE, ""), _label(device_id))
    d.app.db.audit(f"web:{username}", "mobile.pair", name)
    return web.json_response({"id": device_id, "token": token})


async def device_session(request: web.Request) -> web.Response:
    """The app's stored login: opens the dashboard without asking for the password again."""
    d = dashboard(request)
    device = _device(request)
    token = d.auth.create_session(device["username"], d.guard.client_ip(request),
                                  _label(device["id"]))  # fmt: skip
    response = web.json_response({"ok": True})
    d.set_cookie(response, token)
    return response


async def device_socket(request: web.Request) -> web.WebSocketResponse:
    hub = _hub(request)
    device = _device(request)
    socket = web.WebSocketResponse(heartbeat=45, max_msg_size=MAX_MESSAGE)
    await socket.prepare(request)
    hub.attach(device["id"], socket.send_str)
    try:
        async for message in socket:
            if message.type is not WSMsgType.TEXT:
                break
            with contextlib.suppress(ValueError):
                data = json.loads(message.data)
                if isinstance(data, dict) and data.get("type") == "report":
                    hub.report(device["id"], data)
    finally:
        hub.detach(device["id"], socket.send_str)
    return socket


async def report(request: web.Request) -> web.Response:
    device = _device(request)
    _hub(request).report(device["id"], await read_json(request))
    return web.json_response({"ok": True})


async def emergency(request: web.Request) -> web.Response:
    """Sent after the countdown in the app ran out."""
    d = dashboard(request)
    app = d.app
    device = _device(request)
    hub = _hub(request)
    info = hub.report(device["id"], await read_json(request))
    lang = app.settings.language
    mode = t(f"mode.{app.modes.effective.value.lower()}", lang)
    lines = [t("mobile.emergency", lang, name=device["name"]),
             t("mobile.emergency_mode", lang, mode=mode)]  # fmt: skip
    link = map_link(info)
    lines.append(t("mobile.emergency_place", lang, link=link) if link
                 else t("mobile.emergency_no_place", lang))  # fmt: skip
    if "accuracy" in info:
        lines.append(t("mobile.accuracy", lang, meters=round(info["accuracy"])))
    if "battery" in info:
        lines.append(t("mobile.battery", lang, percent=info["battery"]))
    details = [info.get(k) for k in ("model", "network", "ssid", "ip") if info.get(k)]
    if details:
        lines.append(" · ".join(map(str, details)))
    text = "\n".join(lines)
    reached = 0
    for provider, chat in app.escalation.contacts:
        if provider in app.providers:
            try:
                await app.messenger.send(provider, chat, Reply(text))
                reached += 1
            except (ProviderUnavailableError, RuntimeError, OSError):
                log.warning("emergency message via %s failed", provider)
    await app.messenger.notify_admins(text)
    app.db.audit(f"mobile:{device['id']}", "mobile.emergency", str(reached))
    return web.json_response({"contacts": reached})


async def calendar(request: web.Request) -> web.Response:
    """Busy times only: the app sends no titles, places or people."""
    d = dashboard(request)
    _device(request)
    data = await read_json(request)
    try:
        d.app.db.set(CALENDAR_KEY, clean_calendar(data.get("events")))
    except ValueError:
        raise web.HTTPBadRequest(text="web.plan_invalid") from None
    return web.json_response({"ok": True})


async def vpn(request: web.Request) -> web.Response:
    """A VPN access for this phone. Asked again, it answers with the same one."""
    d = dashboard(request)
    device = _device(request)
    store = _store(request)
    _server(request)
    data = await read_json(request)
    peer: int = device.get("vpn_peer") or 0
    if not any(p["id"] == peer for p in store.list()):
        try:
            peer = await asyncio.to_thread(store.add, f"App {device['name']} {device['id']}"[:32],
                                           data.get("full_tunnel") is not False)  # fmt: skip
        except PeerError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        _hub(request).set_vpn_peer(device["id"], peer)
        d.app.db.audit(f"mobile:{device['id']}", "wireguard.add", device["name"])
    _, config = _config(request, peer)
    return web.json_response({"config": config}, headers={"Cache-Control": "no-store"})


def _apk_dir(request: web.Request) -> Path:
    return dashboard(request).settings.data_dir / APP_DIR


def _allowed_download(request: web.Request) -> None:
    d = dashboard(request)
    if request.headers.get("Authorization", "").startswith("Bearer "):
        _device(request)
    elif d._resolve(request) is None:
        raise web.HTTPUnauthorized(text="login required")


async def app_latest(request: web.Request) -> web.Response:
    _allowed_download(request)
    meta = _apk_dir(request) / "latest.json"
    if not meta.is_file():
        return web.json_response({})
    return web.json_response(json.loads(meta.read_text()))


async def app_apk(request: web.Request) -> web.StreamResponse:
    _allowed_download(request)
    path = _apk_dir(request) / "latest.apk"
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Content-Type": "application/vnd.android.package-archive",
        "Content-Disposition": 'attachment; filename="PiGuard.apk"',
    })  # fmt: skip


def _store_apk(source: Path, target: Path, fields: dict[str, str]) -> dict[str, Any]:
    if not zipfile.is_zipfile(source):
        raise ValueError("no apk")
    with zipfile.ZipFile(source) as archive:
        names = set(archive.namelist())
    if "AndroidManifest.xml" not in names or "classes.dex" not in names:
        raise ValueError("no apk")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    meta = {"version_code": int(fields["version_code"]),
            "version_name": fields["version_name"][:30],
            "sha256": digest.hexdigest(), "size": source.stat().st_size}  # fmt: skip
    target.mkdir(parents=True, exist_ok=True)
    shutil.move(source, target / "latest.apk")
    atomic_write(target / "latest.json", json.dumps(meta).encode(), 0o600)
    return meta


async def app_upload(request: web.Request) -> web.Response:
    """A new version for the phones. Android itself refuses one signed with another key."""
    d = dashboard(request)
    fields: dict[str, str] = {}
    work = Path(tempfile.mkdtemp(dir=d.settings.data_dir))
    source = work / "upload.apk"
    try:
        reader = await request.multipart()
        total = 0
        async for part in reader:
            if getattr(part, "filename", None):
                with source.open("wb") as out:
                    while chunk := await part.read_chunk():  # type: ignore[union-attr]
                        total += len(chunk)
                        if total > MAX_APK:
                            raise web.HTTPRequestEntityTooLarge(max_size=MAX_APK, actual_size=total)
                        out.write(chunk)
            else:
                fields[str(part.name)] = (await part.text())[:100]  # type: ignore[union-attr]
        name = d.auth.account(current(request).username)["username"]
        if d.auth.check_password(name, fields.get("password", "")) is None:
            raise web.HTTPForbidden(text="web.wrong_password")
        try:
            meta = await asyncio.to_thread(_store_apk, source, _apk_dir(request), fields)
        except (KeyError, ValueError, OSError):
            raise web.HTTPBadRequest(text="web.mobile_bad_apk") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    d.app.db.audit(f"web:{current(request).username}", "mobile.apk", meta["version_name"])
    return web.json_response(meta)


async def devices(request: web.Request) -> web.Response:
    hub = _hub(request)
    rows = [{**d, "map": map_link(d["info"])} for d in hub.list()]
    meta = _apk_dir(request) / "latest.json"
    latest = json.loads(meta.read_text()) if meta.is_file() else None
    return web.json_response({"devices": rows, "app": latest})


async def device_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    device_id = int(request.match_info["id"])
    hub = _hub(request)
    device = hub.get(device_id)
    if device is None:
        raise web.HTTPNotFound()
    # Removing a lost phone must take everything it could still use.
    hub.delete(device_id)
    d.auth.end_sessions(_label(device_id))
    d.app.users.remove("mobile", str(device_id))
    if device.get("vpn_peer"):
        with contextlib.suppress(web.HTTPException, PeerError):
            await asyncio.to_thread(_store(request).remove, device["vpn_peer"])
    d.app.db.audit(f"web:{current(request).username}", "mobile.remove", str(device_id))
    return web.json_response({"ok": True})


async def device_find(request: web.Request) -> web.Response:
    d = dashboard(request)
    device_id = int(request.match_info["id"])
    try:
        await _hub(request).send(device_id, type="find", ring=True)
    except ProviderUnavailableError:
        raise web.HTTPConflict(text="web.mobile_offline") from None
    d.app.db.audit(f"web:{current(request).username}", "mobile.find", str(device_id))
    return web.json_response({"ok": True})


async def device_stop(request: web.Request) -> web.Response:
    device_id = int(request.match_info["id"])
    with contextlib.suppress(ProviderUnavailableError):
        await _hub(request).send(device_id, type="find", ring=False)
    return web.json_response({"ok": True})

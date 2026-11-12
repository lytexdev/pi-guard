"""The app's side of the server, and the dashboard's page for phones."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import ipaddress
import json
import logging
import shutil
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
from aiohttp import WSMsgType, web

from pi_guard import __version__
from pi_guard.addons.mobile import EMERGENCY_MINUTES, LOST_EVERY, MobileHub, map_link
from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.core.permissions import Role, User
from pi_guard.core.schedule import CALENDAR_KEY, clean_calendar
from pi_guard.i18n import t
from pi_guard.messaging.base import ProviderUnavailableError
from pi_guard.network import awg
from pi_guard.network.reach import addresses, endpoints, tailscale
from pi_guard.network.wgpeers import PeerError
from pi_guard.network.wireguard import read_status, status_file
from pi_guard.notifications import sharing
from pi_guard.security.files import atomic_write
from pi_guard.web.api_network import _config, _server, _store, quantum_public_key
from pi_guard.web.server import (
    COOKIE,
    confirm_password,
    current,
    dashboard,
    read_body,
    read_json,
)

log = logging.getLogger(__name__)

MAX_MESSAGE = 1_000_000
MAX_ZOOM = 19
PQ_KEY_SIZE = 524160
MAX_PQ_REQUEST = 800_000
MAX_TILES = 200
USER_AGENT = f"pi-guard/{__version__} (+https://github.com/lytexdev/pi-guard)"
EMERGENCY_PHOTO_SECONDS = 4
MAX_EMERGENCY_PHOTOS = 4
MAX_APK = 200_000_000
MAX_SOS_MEDIA = 8_000_000  # a photo or a minute of sound from the phone
MAX_SOS_FILES = 6  # per emergency, so the endpoint cannot become a free upload
HOME_CLIP_SECONDS = 20
_tasks: set[asyncio.Task[None]] = set()
APP_DIR = "apps/android"
_tiles: dict[tuple[int, int, int], bytes] = {}


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/hello", hello),
        web.post("/api/mobile/pair", pair),
        web.post("/api/mobile/session", device_session),
        web.get("/api/mobile/ws", device_socket),
        web.post("/api/mobile/report", report),
        web.post("/api/mobile/action", alert_action),
        web.post("/api/mobile/crash", crash_report),
        web.post("/api/mobile/emergency", emergency),
        web.post("/api/mobile/emergency/media", emergency_media),
        web.post("/api/mobile/emergency/end", emergency_end),
        web.post("/api/mobile/calendar", calendar),
        web.post("/api/mobile/vpn", vpn),
        web.post("/api/mobile/vpn/pq", vpn_quantum),
        web.get("/api/mobile/reach", reach),
        web.get("/api/mobile/map/{z:\\d+}/{x:\\d+}/{y:\\d+}.png", map_tile),
        web.get("/api/mobile/app/latest", app_latest),
        web.get("/api/mobile/app/apk", app_apk),
        web.post("/api/mobile/app", app_upload),
        web.get("/api/mobile/devices", devices),
        web.delete("/api/mobile/devices/{id:\\d+}", device_delete),
        web.post("/api/mobile/devices/{id:\\d+}/find", device_find),
        web.post("/api/mobile/devices/{id:\\d+}/stop", device_stop),
        web.post("/api/mobile/devices/{id:\\d+}/locate", device_locate),
        web.post("/api/mobile/devices/{id:\\d+}/lost", device_lost),
        web.post("/api/mobile/devices/{id:\\d+}/found", device_found),
        web.get("/api/mobile/devices/{id:\\d+}/track", device_track),
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
    if found is not None and not d.auth.exists(found["username"]):
        found = None  # removed from the dashboard, also if that was before this check existed
    if found is None:
        ip = d.guard.client_ip(request)
        d.guard.login_failed(ip)
        d.app.db.audit(f"web:{ip}", "login.failed", "mobile token")
        raise web.HTTPUnauthorized(text="invalid token")
    return found


def _admin_device(request: web.Request) -> dict[str, Any]:
    """A VPN access reaches the whole home network; viewers may only watch."""
    device = _device(request)
    if dashboard(request).auth.role_of(device["username"]) != "admin":
        raise web.HTTPForbidden(text="web.vpn_admins_only")
    return device


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
    with contextlib.suppress(ProviderUnavailableError, ConnectionResetError):
        await _tell_state(hub, device["id"])
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


async def crash_report(request: web.Request) -> web.Response:
    """A crash of the app, sent only with its user's consent. Kept for the anonymous
    statistics, which pass it on only if this Pi's owner switched them on."""
    from pi_guard.diagnostics.contribution import clean_crash, keep_crash

    _device(request)
    crash = clean_crash(await read_json(request))
    if crash is None:
        raise web.HTTPBadRequest(text="not a crash report")
    keep_crash(dashboard(request).app, crash)
    return web.json_response({"ok": True})


async def alert_action(request: web.Request) -> web.Response:
    """A button in an alert or a quick action of the app: everything is fine, the alarm on or
    off, home or away. Admins only."""
    from pi_guard.notifications import actions

    d = dashboard(request)
    device = _device(request)
    if d.auth.role_of(device["username"]) != "admin":
        raise web.HTTPForbidden(text="auth.admin_only")
    action = str((await read_json(request)).get("action", ""))
    if action not in actions.QUICK:
        raise web.HTTPBadRequest(text="web.unknown_action")
    try:
        result = await actions.quick(d.app, f"phone:{device['id']}", action)
    except actions.ActionError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    return web.json_response(result)


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
    if not hub.lost(device["id"]):
        hub.start(device["id"], "emergency")  # first, so the place of the SOS is in its track
    info = hub.report(device["id"], await read_json(request))
    lang = app.settings.language
    mode = t(f"mode.{app.modes.effective.value.lower()}", lang)
    head = [t("mobile.emergency", lang, name=device["name"]),
            t("mobile.emergency_mode", lang, mode=mode)]  # fmt: skip
    place = []
    link = map_link(info, app.settings.map_provider)
    place.append(t("mobile.emergency_place", lang, link=link) if link
                 else t("mobile.emergency_no_place", lang))  # fmt: skip
    if "accuracy" in info:
        place.append(t("mobile.accuracy", lang, meters=round(info["accuracy"])))
    if "battery" in info:
        head.append(t("mobile.battery", lang, percent=info["battery"]))
    details = [info.get(k) for k in ("model", "network", "ssid", "ip") if info.get(k)]
    if details:
        place.append(" · ".join(map(str, details)))
    photos = await _current_pictures(app)
    address = f"{request.scheme}://{request.host}"
    reached = await sharing.sos(app, device["username"], head, place, photos,
                                f"mobile:{device['id']}", address)  # fmt: skip
    await app.messenger.notify_admins("\n".join(head + place), photos, tone="alert")
    app.db.audit(f"mobile:{device['id']}", "mobile.emergency", str(reached))
    if app.settings.mobile_sos_home_audio:
        _background(_home_clip(d, device["name"]))
    return web.json_response({"contacts": reached, "track_minutes": EMERGENCY_MINUTES})


def _background(job: Any) -> None:
    task = asyncio.create_task(job)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _tell_state(hub: MobileHub, device_id: int) -> None:
    """A phone that comes online learns that it is lost, or still in an emergency."""
    lost = hub.lost(device_id)
    if lost:
        await hub.send(device_id, type="lost", message=lost.get("message", ""), every=LOST_EVERY)
    elif hub.tracking(device_id):
        await hub.send(device_id, type="emergency", every=LOST_EVERY)


async def _to_everyone(app: Any, text: str, photo: Path | None = None,
                       audio: Path | None = None, need: str | None = None) -> int:  # fmt: skip
    """Emergency contacts that get this kind of file, and admins, with the words even if the
    file fails."""
    reached = await sharing.to_contacts(app, text, need, photo=photo, audio=audio)
    await app.messenger.notify_admins(text, [photo] if photo else [], tone="alert", audio=audio)
    return reached


def _sniff_media(kind: str, body: bytes) -> str | None:
    """The suffix, if the bytes are what the phone says they are."""
    if kind == "photo" and body[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if kind == "audio" and body[4:8] == b"ftyp":
        return ".m4a"
    return None


async def emergency_media(request: web.Request) -> web.Response:
    """A photo or sound clip taken by the phone after its SOS. Only during an emergency or
    while the phone is lost, and only a few, so it cannot become a free upload."""
    d = dashboard(request)
    device = _device(request)
    hub = _hub(request)
    if not hub.tracking(device["id"]):
        raise web.HTTPConflict(text="web.mobile_no_emergency")
    if not hub.count_file(device["id"], MAX_SOS_FILES):
        raise web.HTTPTooManyRequests(text="web.mobile_too_many_files")
    kind = request.query.get("kind", "")
    body = await read_body(request, MAX_SOS_MEDIA)
    suffix = _sniff_media(kind, body)
    if suffix is None:
        raise web.HTTPBadRequest(text="web.mobile_bad_media")
    lang = d.app.settings.language
    text = t(f"mobile.sos_{kind}", lang, name=device["name"])
    with tempfile.TemporaryDirectory(prefix="pg-sos-") as folder:
        path = Path(folder) / f"sos-{device['id']}{suffix}"
        path.write_bytes(body)
        reached = await _to_everyone(d.app, text, photo=path if kind == "photo" else None,
                                     audio=path if kind == "audio" else None,
                                     need=f"phone_{kind}")  # fmt: skip
    d.app.db.audit(f"mobile:{device['id']}", f"mobile.sos_{kind}", str(reached))
    return web.json_response({"contacts": reached})


async def emergency_end(request: web.Request) -> web.Response:
    """The person is safe. Ends the emergency, not a lost phone: a thief could do that."""
    d = dashboard(request)
    device = _device(request)
    hub = _hub(request)
    if hub.lost(device["id"]) or not hub.tracking(device["id"]):
        raise web.HTTPConflict(text="web.mobile_no_emergency")
    hub.stop(device["id"])
    await _to_everyone(d.app, t("mobile.sos_ended", d.app.settings.language, name=device["name"]))
    d.app.db.audit(f"mobile:{device['id']}", "mobile.sos_ended")
    return web.json_response({"ok": True})


async def _home_clip(d: Any, name: str) -> None:
    """What the Pi's microphone hears, for the contacts. Off unless the owner switched it on,
    and with the same chime as listening live, so nobody at home is recorded unknowingly."""
    from pi_guard.audio.service import AudioError
    from pi_guard.web.api_live import MicrophoneStream

    app = d.app
    audio = app.audio
    if audio is None or not audio.recording_enabled:
        return
    pipeline = next((p for p in app.cameras.values()
                     if p.microphone and p.segment_dir and p.live_fps > 0), None)  # fmt: skip
    standalone: MicrophoneStream | None = None
    if pipeline is not None:
        queue = pipeline.live_audio.subscribe()
    elif audio.settings.microphone:
        device = audio.settings.microphone
        standalone = d.microphones.setdefault(device, MicrophoneStream(device))
        queue = await standalone.subscribe()
    else:
        return
    chime = audio.sounds.path("chime")
    if audio.playback_enabled and chime:
        with contextlib.suppress(AudioError):
            await audio.play(chime, ignore_quiet_hours=True)
    sound = bytearray()
    try:
        async with asyncio.timeout(HOME_CLIP_SECONDS):
            while True:
                sound += await queue.get()
    except TimeoutError:
        pass
    finally:
        if standalone is not None:
            await standalone.unsubscribe(queue)
        elif pipeline is not None:
            pipeline.live_audio.unsubscribe(queue)
    if not sound:
        return
    app.db.audit("mobile", "mobile.sos_home_audio")
    with tempfile.TemporaryDirectory(prefix="pg-sos-") as folder:
        path = Path(folder) / "home.mp3"
        path.write_bytes(bytes(sound))
        await _to_everyone(app, t("mobile.sos_home_audio", app.settings.language, name=name),
                           audio=path, need="home_audio")  # fmt: skip


async def _current_pictures(app: Any) -> list[Path]:
    """What the cameras see now. A camera that does not answer must not delay the call for help."""

    async def picture(camera_id: str) -> Path | None:
        try:
            return await asyncio.wait_for(app.take_photo(camera_id, prefix="emergency"),
                                          EMERGENCY_PHOTO_SECONDS)  # fmt: skip
        except (SnapshotUnavailableError, TimeoutError, OSError):
            return None

    ids = list(app.cameras)[:MAX_EMERGENCY_PHOTOS]
    return [p for p in await asyncio.gather(*map(picture, ids)) if p is not None]


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
    device = _admin_device(request)
    store = _store(request)
    _server(request)
    data = await read_json(request)
    peer: int = device.get("vpn_peer") or 0
    if not any(p["id"] == peer for p in store.list()):
        try:
            peer = await asyncio.to_thread(store.add, f"App {device['id']} {device['name']}"[:32],
                                           data.get("full_tunnel") is not False)  # fmt: skip
        except PeerError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        _hub(request).set_vpn_peer(device["id"], peer)
        d.app.db.audit(f"mobile:{device['id']}", "wireguard.add", device["name"])
    name, config = _config(request, peer)
    status = read_status(status_file(d.settings, "wireguard.status")) or {}
    port = (status.get("interface") or {}).get("listen_port")
    ways = endpoints(d.settings, port)
    # Separate from "ways": apps before 1.8 would try them as plain WireGuard and wait in vain.
    hidden = awg.ways(ways, await asyncio.to_thread(awg.read))
    return web.json_response({"config": config, "name": name, "ways": ways, "obfuscated": hidden},
                             headers={"Cache-Control": "no-store"})  # fmt: skip


async def vpn_quantum(request: web.Request) -> web.Response:
    """Swaps public Rosenpass keys. The connection is only for delivery: Rosenpass itself checks."""
    d = dashboard(request)
    device = _admin_device(request)
    store = _store(request)
    body = await read_body(request, MAX_PQ_REQUEST)
    try:
        key = base64.b64decode(json.loads(body)["key"], validate=True)
    except (ValueError, KeyError, TypeError):
        raise web.HTTPBadRequest(text="web.error") from None
    if len(key) != PQ_KEY_SIZE:
        raise web.HTTPBadRequest(text="web.error")
    peer = next((p for p in store.list() if p["id"] == device.get("vpn_peer")), None)
    if peer is None:
        raise web.HTTPConflict(text="web.vpn_no_access")
    server = quantum_public_key()
    if server is None:
        raise web.HTTPConflict(text="web.pq_not_installed")
    path = store.pq_path(peer["public_key"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    await asyncio.to_thread(atomic_write, path, key)
    d.app.db.audit(f"mobile:{device['id']}", "wireguard.quantum", device["name"])
    return web.json_response({"server_key": base64.b64encode(server).decode()})


async def reach(request: web.Request) -> web.Response:
    """Where else the app finds the dashboard, for example over Tailscale."""
    d = dashboard(request)
    _device(request)
    net = await asyncio.to_thread(tailscale)
    return web.json_response({"addresses": addresses(d.settings, net)})


async def map_tile(request: web.Request) -> web.Response:
    """Fetched here: OpenStreetMap refuses browsers without a referrer, which this site never
    sends, and so it only ever sees the Pi, not who looks at the map from where."""
    z, x, y = (int(request.match_info[k]) for k in "zxy")
    if z > MAX_ZOOM or x >= 2**z or y >= 2**z:
        raise web.HTTPNotFound()
    tile = _tiles.get((z, x, y))
    if tile is None:
        try:
            async with httpx.AsyncClient(timeout=8, headers={"User-Agent": USER_AGENT}) as client:
                answer = await client.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png")
            answer.raise_for_status()
        except httpx.HTTPError:
            raise web.HTTPBadGateway(text="web.mob_map_failed") from None
        tile = _tiles[(z, x, y)] = answer.content
        while len(_tiles) > MAX_TILES:
            del _tiles[next(iter(_tiles))]
    return web.Response(body=tile, content_type="image/png",
                        headers={"Cache-Control": "private, max-age=86400"})  # fmt: skip


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
    provider = dashboard(request).settings.map_provider
    states = hub.states()
    rows = [{**d, "map": map_link(d["info"], provider), "tracking": states.get(str(d["id"]))}
            for d in hub.list()]  # fmt: skip
    meta = _apk_dir(request) / "latest.json"
    latest = json.loads(meta.read_text()) if meta.is_file() else None
    return web.json_response({"devices": rows, "app": latest})


async def device_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    device_id = int(request.match_info["id"])
    device = _hub(request).get(device_id)
    if device is None:
        raise web.HTTPNotFound()
    await _forget(request, device)
    d.app.db.audit(f"web:{current(request).username}", "mobile.remove", str(device_id))
    return web.json_response({"ok": True})


async def _forget(request: web.Request, device: dict[str, Any]) -> None:
    """Removing a device must take everything it could still use."""
    d = dashboard(request)
    _hub(request).delete(device["id"])
    d.auth.end_sessions(_label(device["id"]))
    d.app.users.remove("mobile", str(device["id"]))
    if device.get("vpn_peer"):
        with contextlib.suppress(web.HTTPException, PeerError):
            await asyncio.to_thread(_store(request).remove, device["vpn_peer"])


async def role_changed(request: web.Request, username: str, role: str) -> None:
    """Devices follow their person: someone who only watches now keeps no VPN access."""
    d = dashboard(request)
    hub = d.app.mobile
    for device in hub.of_user(username) if hub else []:
        user = d.app.users.get("mobile", str(device["id"]))
        if user is not None:
            d.app.users.save(replace(user, role=Role(role)))
        if role != "admin" and device.get("vpn_peer"):
            with contextlib.suppress(web.HTTPException, PeerError):
                await asyncio.to_thread(_store(request).remove, device["vpn_peer"])
            _hub(request).set_vpn_peer(device["id"], 0)


async def forget_devices(request: web.Request, username: str) -> int:
    """Someone removed from the dashboard loses their phones and computers with it."""
    hub = dashboard(request).app.mobile
    devices = hub.of_user(username) if hub else []
    for device in devices:
        await _forget(request, device)
    return len(devices)


async def device_find(request: web.Request) -> web.Response:
    d = dashboard(request)
    device_id = int(request.match_info["id"])
    try:
        await _hub(request).send(device_id, type="find", ring=True)
    except ProviderUnavailableError:
        raise web.HTTPConflict(text="web.mobile_offline") from None
    d.app.db.audit(f"web:{current(request).username}", "mobile.find", str(device_id))
    return web.json_response({"ok": True})


async def device_locate(request: web.Request) -> web.Response:
    """Ask the phone for a fresh place, without ringing. It answers with its next report,
    and only if location sharing is on there."""
    d = dashboard(request)
    device_id = int(request.match_info["id"])
    try:
        await _hub(request).send(device_id, type="locate")
    except ProviderUnavailableError:
        raise web.HTTPConflict(text="web.mobile_offline") from None
    d.app.db.audit(f"web:{current(request).username}", "mobile.locate", str(device_id))
    return web.json_response({"ok": True})


def _known(request: web.Request) -> int:
    device_id = int(request.match_info["id"])
    if _hub(request).get(device_id) is None:
        raise web.HTTPNotFound()
    return device_id


async def device_lost(request: web.Request) -> web.Response:
    """The phone reports its place every minute until it is found. The phone says so on its
    screen. Needs the password: the track outlives the session."""
    d = dashboard(request)
    device_id = _known(request)
    data = await read_json(request)
    confirm_password(request, str(data.get("password", "")))
    message = " ".join(str(data.get("message", "")).split())[:200]
    hub = _hub(request)
    hub.start(device_id, "lost", message, current(request).username)
    with contextlib.suppress(ProviderUnavailableError):
        await _tell_state(hub, device_id)
    d.app.db.audit(f"web:{current(request).username}", "mobile.lost", str(device_id))
    return web.json_response({"ok": True})


async def device_found(request: web.Request) -> web.Response:
    """Ends a lost phone or an emergency, and deletes its track."""
    d = dashboard(request)
    device_id = _known(request)
    hub = _hub(request)
    hub.stop(device_id)
    with contextlib.suppress(ProviderUnavailableError):
        await hub.send(device_id, type="found")
    d.app.db.audit(f"web:{current(request).username}", "mobile.found", str(device_id))
    return web.json_response({"ok": True})


async def device_track(request: web.Request) -> web.Response:
    device_id = _known(request)
    hub = _hub(request)
    provider = dashboard(request).settings.map_provider
    track = [{**p, "map": map_link(p, provider)} for p in hub.track(device_id)]
    return web.json_response({"state": hub.states().get(str(device_id)), "track": track})


async def device_stop(request: web.Request) -> web.Response:
    device_id = int(request.match_info["id"])
    with contextlib.suppress(ProviderUnavailableError):
        await _hub(request).send(device_id, type="find", ring=False)
    return web.json_response({"ok": True})

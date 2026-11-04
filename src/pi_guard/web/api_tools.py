"""Tools in the dashboard: Signal linking, Wi-Fi access, weekly plan, support contact,
navigation, security scan and storage. Everything here is for admins, see VIEWER_GET."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import secrets
import shutil
import sqlite3
import string
import tempfile
import time
from pathlib import Path
from typing import Any

from aiohttp import web

from pi_guard.audio import check
from pi_guard.audio import devices as audio_devices
from pi_guard.audio.service import AudioError
from pi_guard.core.permissions import Role
from pi_guard.core.schedule import PLAN_KEY, clean_plan
from pi_guard.diagnostics import storage as disk
from pi_guard.diagnostics.scan import HELPER, read_report, request_root
from pi_guard.i18n import t
from pi_guard.messaging.base import ProviderUnavailableError, Reply
from pi_guard.messaging.signal.provider import rpc
from pi_guard.network.fritzbox import FritzError
from pi_guard.network.lan import neighbour_table
from pi_guard.network.pihole import PiholeError
from pi_guard.web.api_account import _qr_svg
from pi_guard.web.api_manage import apply_settings
from pi_guard.web.server import MODULES, current, dashboard, read_json

log = logging.getLogger(__name__)

DONATIONS = (
    ("monero", "Monero (XMR)", "monero",
     "89udnKEYMZCBCaoaTYwSLuJBnQRMVPWxW4kNanra4d4GCdgKN5UNjHKU9zk77jvrU726YQYQqeM2PAUhR8jTMrYU6d5cLMp"),
    ("bitcoin", "Bitcoin (BTC)", "bitcoin", "3B8PdpJnmL7rQyHhBytKtvUT7NgQfraBEH"),
    ("litecoin", "Litecoin (LTC)", "litecoin", "MShzshtse85i6ZkwiB1VKNGRNmEL5Dc3VX"),
)  # fmt: skip
NEEDS_NO_SETUP = ("mobile",)  # the others start only once their settings are filled in
LINK_SECONDS = 300
TEST_SECONDS = 3
SUPPORT_FIELDS = {"name": 100, "phone": 40, "email": 100, "address": 300}
NAV_ROUTES = re.compile(r"^[a-z]{2,20}$")
MAX_NAV = 20
ENCRYPTED_DNS_PORTS = (443, 853)
WIFI_KEY_LENGTH = 20
WIFI_ALPHABET = string.ascii_letters + string.digits
_links: dict[str, Any] = {}


def routes() -> list[web.RouteDef]:
    return [
        web.post("/api/devices/test/speaker", test_speaker),
        web.post("/api/devices/test/microphone", test_microphone),
        web.get("/api/modules", modules_get),
        web.put("/api/modules/{id}", module_set),
        web.get("/api/support/donate", donate),
        web.post("/api/email/test", email_test),
        web.post("/api/email/export", email_export),
        web.get("/api/discord", discord_status),
        web.post("/api/discord/test", discord_test),
        web.get("/api/signal", signal_status),
        web.post("/api/signal/link", signal_link),
        web.post("/api/network/wifi/qr", wifi_qr),
        web.post("/api/network/wifi/{index:[123]}/renew", wifi_renew),
        web.get("/api/network/internet/{ip}", internet_state),
        web.post("/api/network/internet/{ip}", internet_set),
        web.get("/api/network/exposure", exposure),
        web.get("/api/schedule", schedule_get),
        web.put("/api/schedule", schedule_put),
        web.put("/api/support", support_put),
        web.post("/api/me/nav", nav_put),
        web.get("/api/scan", scan_get),
        web.post("/api/scan", scan_start),
        web.get("/api/storage", storage_get),
        web.post("/api/storage/clean", storage_clean),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


def _confirm(request: web.Request, data: dict[str, Any]) -> None:
    """Changes that lock people out need the password again."""
    d = dashboard(request)
    name = d.auth.account(current(request).username)["username"]
    if d.auth.check_password(name, str(data.get("password", ""))) is None:
        raise web.HTTPForbidden(text="web.wrong_password")


# --- trying out audio devices ---------------------------------------------------------------


async def _audio_device(request: web.Request, capability: str) -> str:
    """Only names the system itself reports reach ffmpeg."""
    device = str((await read_json(request)).get("device", ""))
    found = await asyncio.to_thread(audio_devices.discover)
    if not any(a.alsa_name == device and getattr(a, capability) for a in found):
        raise web.HTTPBadRequest(text="web.dev_test_unknown")
    return device


async def test_speaker(request: web.Request) -> web.Response:
    d = dashboard(request)
    device = await _audio_device(request, "playback")
    with tempfile.TemporaryDirectory(dir=d.settings.data_dir) as folder:
        sound = Path(folder) / "test.wav"
        await asyncio.to_thread(check.write_test_sound, sound)
        try:
            await check.play_on(device, sound)
        except AudioError as exc:
            raise web.HTTPConflict(text=exc.key) from None
    d.app.db.audit(_actor(request), "audio.test_speaker", device[:80])
    return web.json_response({"ok": True})


async def _from_running(app: Any, device: str, output: Path) -> bool:
    """A camera may hold the microphone; then its own sound stream is the sample."""
    pipeline = next((p for p in app.cameras.values() if p.microphone == device
                     and p.segment_dir and p.live_fps > 0), None)  # fmt: skip
    if pipeline is None:
        return False
    queue = pipeline.live_audio.subscribe()
    sample = bytearray()
    end = time.monotonic() + TEST_SECONDS
    try:
        while (left := end - time.monotonic()) > 0:
            with contextlib.suppress(TimeoutError):
                sample += await asyncio.wait_for(queue.get(), left)
    finally:
        pipeline.live_audio.unsubscribe(queue)
    if not sample:
        raise AudioError("web.dev_test_failed")
    await asyncio.to_thread(output.write_bytes, bytes(sample))
    return True


async def test_microphone(request: web.Request) -> web.StreamResponse:
    """A short sample for the person choosing, not kept. Announced like listening in."""
    d = dashboard(request)
    app = d.app
    device = await _audio_device(request, "capture")
    app.db.audit(_actor(request), "audio.test_microphone", device[:80])
    audio = app.audio
    chime = audio.sounds.path("chime") if audio and audio.playback_enabled else None
    if audio and chime:
        with contextlib.suppress(AudioError):
            await audio.play(chime, ignore_quiet_hours=True)
    with tempfile.TemporaryDirectory(dir=d.settings.data_dir) as folder:
        sample = Path(folder) / "test.mp3"
        try:
            if not await _from_running(app, device, sample):
                await check.record_from(device, TEST_SECONDS, sample)
        except AudioError as exc:
            raise web.HTTPConflict(text=exc.key) from None
        peak = await check.peak_db(sample)
        body = await asyncio.to_thread(sample.read_bytes)
    silent = peak is None or peak < check.SILENT_BELOW_DB
    return web.Response(body=body, content_type="audio/mpeg", headers={
        "X-Peak-Db": "" if peak is None or peak == float("-inf") else f"{peak:.1f}",
        "X-Silent": "1" if silent else "0", "Cache-Control": "no-store",
    })  # fmt: skip


# --- modules --------------------------------------------------------------------------------


async def modules_get(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response([{"id": m, "enabled": on, "running": m in d.app.providers}
                              for m, on in d.modules().items()])  # fmt: skip


async def module_set(request: web.Request) -> web.Response:
    """Off also stops the messenger at the next start; its entries are kept for later."""
    d = dashboard(request)
    module = request.match_info["id"]
    if module not in MODULES:
        raise web.HTTPNotFound()
    enabled = (await read_json(request)).get("enabled") is True
    running = getattr(d.settings, f"{module}_enabled")
    if running != enabled and (not enabled or module in NEEDS_NO_SETUP):
        await apply_settings(d, {f"{module}_enabled": enabled}, _actor(request))
    saved = d.app.db.get("web.modules") or {}
    d.app.db.set("web.modules", {**saved, module: enabled})
    d.app.db.audit(_actor(request), "module", f"{module} {'on' if enabled else 'off'}")
    return web.json_response({"ok": True, "restart": enabled != (module in d.app.providers)
                              and (not enabled or module in NEEDS_NO_SETUP)})  # fmt: skip


# --- donations ------------------------------------------------------------------------------


async def donate(request: web.Request) -> web.Response:
    return web.json_response([
        {"id": coin, "name": name, "address": address, "qr": _qr_svg(f"{scheme}:{address}")}
        for coin, name, scheme, address in DONATIONS
    ])  # fmt: skip


# --- email ----------------------------------------------------------------------------------


def _email(request: web.Request) -> Any:
    provider = dashboard(request).app.providers.get("email")
    if provider is None:
        raise web.HTTPConflict(text="web.email_off")
    return provider


async def email_test(request: web.Request) -> web.Response:
    d = dashboard(request)
    provider = _email(request)
    text = t("email.test", d.settings.language)
    try:
        for recipient in d.settings.email_recipients:
            await provider.send(recipient, Reply(text))
    except ProviderUnavailableError:
        raise web.HTTPBadGateway(text="web.email_failed") from None
    return web.json_response({"ok": True})


async def email_export(request: web.Request) -> web.Response:
    d = dashboard(request)
    _confirm(request, await read_json(request))
    try:
        sent = await _email(request).send_export()
    except ProviderUnavailableError:
        raise web.HTTPBadGateway(text="web.email_failed") from None
    d.app.db.audit(_actor(request), "export.email", str(sent))
    return web.json_response({"sent": sent})


# --- Discord --------------------------------------------------------------------------------


async def discord_status(request: web.Request) -> web.Response:
    d = dashboard(request)
    provider = d.app.providers.get("discord")
    bot = getattr(provider, "bot_id", None)
    invite = f"https://discord.com/oauth2/authorize?client_id={bot}&scope=bot" if bot else None
    return web.json_response({
        "enabled": d.settings.discord_enabled, "connected": bool(provider and provider.connected),
        "name": getattr(provider, "username", None), "invite": invite,
        "problem": getattr(provider, "problem", None),
    })  # fmt: skip


async def discord_test(request: web.Request) -> web.Response:
    d = dashboard(request)
    app = d.app
    if "discord" not in app.providers:
        raise web.HTTPConflict(text="web.discord_off")
    sent = 0
    for user in app.users.all("discord"):
        if user.role is Role.ADMIN:
            try:
                await app.messenger.send(
                    "discord", user.user_id, Reply(t("discord.test", d.settings.language))
                )
                sent += 1
            except (ProviderUnavailableError, RuntimeError):
                log.warning("discord test message not delivered")
    if not sent:
        raise web.HTTPBadGateway(text="web.discord_not_delivered")
    return web.json_response({"sent": sent})


# --- Signal ---------------------------------------------------------------------------------


async def signal_status(request: web.Request) -> web.Response:
    d = dashboard(request)
    s = d.settings
    provider = d.app.providers.get("signal")
    version, accounts = None, []
    try:
        version = (await rpc(s.signal_cli_address, "version", wait=3) or {}).get("version")
        found = await rpc(s.signal_cli_address, "listAccounts", wait=3) or []
        accounts = [a.get("number") for a in found if isinstance(a, dict)]
    except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
        log.debug("signal-cli: %s", type(exc).__name__)
    return web.json_response({
        "enabled": s.signal_enabled, "installed": shutil.which("signal-cli") is not None,
        "running": version is not None, "version": version,
        "linked": s.signal_phone_number in accounts, "accounts": len(accounts),
        "connected": bool(provider and provider.connected),
        "link": {k: v for k, v in _links.items() if k != "task"},
    })  # fmt: skip


async def _finish_link(address: str, uri: str) -> None:
    try:
        await rpc(address, "finishLink", {"deviceLinkUri": uri, "deviceName": "pi-guard"},
                  wait=LINK_SECONDS)  # fmt: skip
        _links.update(state="linked")
    except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
        log.warning("linking Signal failed: %s", type(exc).__name__)
        _links.update(state="failed")


async def signal_link(request: web.Request) -> web.Response:
    d = dashboard(request)
    address = d.settings.signal_cli_address
    try:
        started = await rpc(address, "startLink", wait=20)
    except (OSError, TimeoutError, ValueError, RuntimeError):
        raise web.HTTPConflict(text="web.signal_not_running") from None
    uri = str(started["deviceLinkUri"])
    _links.clear()
    _links.update(state="waiting", task=asyncio.create_task(_finish_link(address, uri)))
    d.app.db.audit(_actor(request), "signal.link")
    return web.json_response({"qr": _qr_svg(uri)})


# --- Wi-Fi ----------------------------------------------------------------------------------


def _wifi_text(ssid: str, key: str) -> str:
    def escaped(value: str) -> str:
        return re.sub(r'([\\;,:"])', r"\\\1", value)

    return f"WIFI:T:WPA;S:{escaped(ssid)};P:{escaped(key)};;"


def _fritz(request: web.Request) -> Any:
    fritz = dashboard(request).app.fritz
    if fritz is None:
        raise web.HTTPConflict(text="web.wifi_no_fritz")
    return fritz


async def wifi_qr(request: web.Request) -> web.Response:
    """From the FRITZ!Box, or typed in. The key is only passed on, never stored."""
    d = dashboard(request)
    data = await read_json(request)
    ssid, key = str(data.get("ssid", "")).strip(), str(data.get("key", ""))
    index = data.get("index")
    if index in (1, 2, 3):
        fritz = _fritz(request)
        try:
            wlans = {w.index: w for w in await fritz.wlans()}
            ssid, key = wlans[index].ssid, await fritz.wifi_key(index)
        except (FritzError, KeyError):
            raise web.HTTPBadGateway(text="web.wifi_no_key") from None
    if not ssid or not 8 <= len(key) <= 63:
        raise web.HTTPBadRequest(text="web.wifi_bad_input")
    d.app.db.audit(_actor(request), "network.wifi_qr", ssid[:32])
    return web.json_response({"ssid": ssid, "key": key, "qr": _qr_svg(_wifi_text(ssid, key))})


async def _on_wifi() -> bool:
    """True if this Pi itself is connected by Wi-Fi: a new key would cut it off."""
    process = await asyncio.create_subprocess_exec(
        "ip", "-4", "route", "show", "default",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    output, _ = await process.communicate()
    return " dev wl" in output.decode()


async def wifi_renew(request: web.Request) -> web.Response:
    """A new key: the only way the router offers to make devices log in again."""
    d = dashboard(request)
    data = await read_json(request)
    _confirm(request, data)
    fritz = _fritz(request)
    index = int(request.match_info["index"])
    if index != 3 and await _on_wifi():
        raise web.HTTPConflict(text="web.wifi_pi_on_wifi")
    key = "".join(secrets.choice(WIFI_ALPHABET) for _ in range(WIFI_KEY_LENGTH))
    try:
        wlans = await fritz.wlans()
        targets = [w for w in wlans if w.guest == (index == 3)]
        for wlan in targets:
            await fritz.set_wifi_key(wlan.index, key)
    except FritzError:
        raise web.HTTPBadGateway(text="web.wifi_no_key") from None
    d.app.db.audit(_actor(request), "network.wifi_renew", str(index))
    ssid = targets[0].ssid if targets else ""
    return web.json_response({"ssid": ssid, "key": key, "qr": _qr_svg(_wifi_text(ssid, key))})


def _private_ip(request: web.Request) -> str:
    ip = request.match_info["ip"]
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        raise web.HTTPBadRequest(text="analytics.bad_ip") from None
    if address.version != 4 or not address.is_private:
        raise web.HTTPBadRequest(text="analytics.bad_ip")
    return ip


async def internet_state(request: web.Request) -> web.Response:
    ip = _private_ip(request)
    fritz = dashboard(request).app.fritz
    if fritz is None:
        return web.json_response({"available": False})
    try:
        return web.json_response({"available": True, "allowed": await fritz.internet_allowed(ip)})
    except FritzError:
        return web.json_response({"available": False})


async def internet_set(request: web.Request) -> web.Response:
    d = dashboard(request)
    ip = _private_ip(request)
    data = await read_json(request)
    _confirm(request, data)
    allowed = data.get("allowed") is True
    try:
        await _fritz(request).set_internet(ip, allowed)
    except FritzError:
        raise web.HTTPBadGateway(text="web.wifi_no_key") from None
    d.app.db.audit(_actor(request), "network.internet", f"{ip} {'on' if allowed else 'off'}")
    return web.json_response({"ok": True, "allowed": allowed})


async def _upstreams(app: Any) -> list[dict[str, Any]]:
    if app.pihole is None:
        return []
    try:
        data = await app.pihole.request("GET", "/api/stats/upstreams")
    except PiholeError:
        return []
    found = [u for u in data.get("upstreams", []) if u.get("port", 0) > 0]
    return [{"name": u.get("name") or u.get("ip"), "port": u.get("port")} for u in found]


async def exposure(request: web.Request) -> web.Response:
    """What this device gives away, from what the home network itself can see of it."""
    d = dashboard(request)
    app = d.app
    ip = d.guard.client_ip(request)
    vpn = False
    with contextlib.suppress(ValueError):
        vpn = ipaddress.ip_address(ip) in ipaddress.ip_network("10.8.0.0/24")
    mac = ""
    try:
        table = await neighbour_table(app.config.file.presence.interface)
        mac = next((m for m, entry in table.items() if entry[0] == ip), "")
    except (OSError, TimeoutError):
        pass
    name = ""
    if app.fritz is not None and mac:
        with contextlib.suppress(FritzError):
            name = next((h.name for h in await app.fritz.hosts() if h.mac == mac), "")
    usage = None
    if app.addons.analytics is not None:
        try:
            usage = await app.addons.analytics.device(ip)
        except (PiholeError, ValueError):
            usage = None
    upstreams = await _upstreams(app)
    return web.json_response({
        "ip": ip, "vpn": vpn, "mac": mac, "name": name, "usage": usage, "upstreams": upstreams,
        "dns_encrypted": bool(upstreams) and all(
            u["port"] in ENCRYPTED_DNS_PORTS for u in upstreams
        ),
    })  # fmt: skip


# --- plan, support, navigation --------------------------------------------------------------


async def schedule_get(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response({**d.app.schedule.plan(),
                              "presence": d.settings.presence_enabled})  # fmt: skip


async def schedule_put(request: web.Request) -> web.Response:
    d = dashboard(request)
    try:
        plan = clean_plan(await read_json(request))
    except (ValueError, AttributeError):
        raise web.HTTPBadRequest(text="web.plan_invalid") from None
    d.app.db.set(PLAN_KEY, plan)
    d.app.db.audit(_actor(request), "schedule.save", str(len(plan["blocks"])))
    return web.json_response(plan)


async def support_put(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    saved = {k: str(data.get(k, "")).strip()[:size] for k, size in SUPPORT_FIELDS.items()}
    d.app.db.set("web.support", saved)
    return web.json_response(saved)


async def nav_put(request: web.Request) -> web.Response:
    d = dashboard(request)
    order = (await read_json(request)).get("order")
    if not isinstance(order, list) or len(order) > MAX_NAV:
        raise web.HTTPBadRequest(text="web.error")
    if not all(isinstance(r, str) and NAV_ROUTES.match(r) for r in order):
        raise web.HTTPBadRequest(text="web.error")
    d.app.db.set(f"web.nav.{current(request).username}", order)
    return web.json_response({"ok": True})


# --- security scan --------------------------------------------------------------------------


async def scan_get(request: web.Request) -> web.Response:
    app = dashboard(request).app
    return web.json_response({"running": app.scanner.running, "last": app.scanner.last,
                              "root_job": HELPER.exists()})  # fmt: skip


async def scan_start(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    if not d.app.scanner.start(data.get("report") is True):
        raise web.HTTPConflict(text="web.scan_busy")
    d.app.db.audit(_actor(request), "security.scan_start")
    return web.json_response({"ok": True})


# --- storage --------------------------------------------------------------------------------


async def storage_get(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await asyncio.to_thread(disk.analyse, d.app)
    system = read_report("disk")
    if HELPER.exists() and (system is None or request.query.get("refresh")):
        await asyncio.to_thread(request_root, d.settings.data_dir, "disk")
    return web.json_response({**data, "system": system, "root_job": HELPER.exists()})


async def storage_clean(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    target = str(data.get("target", ""))
    if target in ("journal", "apt"):
        if not HELPER.exists():
            raise web.HTTPConflict(text="web.st_no_job")
        await asyncio.to_thread(request_root, d.settings.data_dir, f"clean-{target}")
        d.app.db.audit(_actor(request), "storage.clean", target)
        return web.json_response({"ok": True, "started": True})
    if target == "database":
        try:
            await asyncio.to_thread(d.app.db.execute, "VACUUM")
        except sqlite3.OperationalError:  # busy right now
            raise web.HTTPConflict(text="web.st_busy") from None
        d.app.db.audit(_actor(request), "storage.clean", target)
        return web.json_response({"ok": True, "files": 0, "bytes": 0})
    try:
        files, freed = await asyncio.to_thread(
            disk.clean, d.app, target, int(data.get("days", 0)), str(data.get("camera", ""))
        )
    except ValueError:
        raise web.HTTPBadRequest(text="web.error") from None
    d.app.db.audit(_actor(request), "storage.clean", f"{target} {files} files")
    return web.json_response({"ok": True, "files": files, "bytes": freed})

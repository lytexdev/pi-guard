"""Tools in the dashboard: Signal linking, Wi-Fi access, weekly plan,
navigation, security scan and storage. Everything here is for admins, see VIEWER_GET."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
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

from pi_guard.app import CHAT_APPS
from pi_guard.audio import check
from pi_guard.audio import devices as audio_devices
from pi_guard.audio.service import AudioError
from pi_guard.core.calendars import CalendarError, Feeds
from pi_guard.core.permissions import Role
from pi_guard.core.schedule import CALENDAR_KEY, PLAN_KEY, clean_plan
from pi_guard.diagnostics import fixes
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
from pi_guard.web.server import (
    MODULE_SETTINGS,
    MODULES,
    confirm_password,
    current,
    dashboard,
    read_json,
)

log = logging.getLogger(__name__)

DONATIONS = (
    ("monero", "Monero (XMR)", "monero",
     "89udnKEYMZCBCaoaTYwSLuJBnQRMVPWxW4kNanra4d4GCdgKN5UNjHKU9zk77jvrU726YQYQqeM2PAUhR8jTMrYU6d5cLMp"),
    ("bitcoin", "Bitcoin (BTC)", "bitcoin", "3B8PdpJnmL7rQyHhBytKtvUT7NgQfraBEH"),
    ("litecoin", "Litecoin (LTC)", "litecoin", "MShzshtse85i6ZkwiB1VKNGRNmEL5Dc3VX"),
)  # fmt: skip
# Others start only once their settings are filled in.
NEEDS_NO_SETUP = ("chatapps", "vpn", "pihole")
LINK_SECONDS = 300
LINK_VALID = 110  # signal-cli gives up on a code after 120 s; a new one comes just before
TEST_SECONDS = 3
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
        web.get("/api/messengers", messengers),
        web.post("/api/messengers/{name}/test", messenger_test),
        web.get("/api/signal", signal_status),
        web.get("/api/signal/link", signal_link_state),
        web.post("/api/signal/link", signal_link),
        web.post("/api/signal/test", signal_test),
        web.post("/api/network/wifi/qr", wifi_qr),
        web.get("/api/network/wifi/profiles", wifi_profiles_get),
        web.put("/api/network/wifi/profiles/{slot:home|guest}", wifi_profile_put),
        web.delete("/api/network/wifi/profiles/{slot:home|guest}", wifi_profile_delete),
        web.post("/api/network/wifi/{index:[123]}/renew", wifi_renew),
        web.get("/api/network/internet/{ip}", internet_state),
        web.post("/api/network/internet/{ip}", internet_set),
        web.get("/api/network/exposure", exposure),
        web.get("/api/calendars", calendars_list),
        web.post("/api/calendars", calendars_add),
        web.post("/api/calendars/refresh", calendars_refresh),
        web.delete("/api/calendars/{id}", calendars_remove),
        web.get("/api/schedule", schedule_get),
        web.put("/api/schedule", schedule_put),
        web.post("/api/me/nav", nav_put),
        web.get("/api/scan", scan_get),
        web.post("/api/scan", scan_start),
        web.post("/api/scan/fix", scan_fix),
        web.post("/api/scan/review", scan_review),
        web.get("/api/storage", storage_get),
        web.post("/api/storage/clean", storage_clean),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


def _confirm(request: web.Request, data: dict[str, Any]) -> None:
    """Changes that lock people out need the password again."""
    confirm_password(request, str(data.get("password", "")))


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


def _module_setting(module: str) -> str:
    return MODULE_SETTINGS.get(module, f"{module}_enabled")


def _module_running(d: Any, module: str) -> bool:
    if module == "vpn":
        return bool(d.settings.wireguard_enabled and d.wg_peers is not None)
    if module == "pihole":
        return d.app.pihole is not None
    if module == "chatapps":
        return any(name in d.app.providers for name in CHAT_APPS)
    return module in d.app.providers


async def modules_get(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response(
        [
            {"id": m, "enabled": on, "running": _module_running(d, m)}
            for m, on in d.modules().items()
        ]
    )


async def module_set(request: web.Request) -> web.Response:
    """Off also stops the messenger at the next start; its entries are kept for later."""
    d = dashboard(request)
    module = request.match_info["id"]
    if module not in MODULES:
        raise web.HTTPNotFound()
    enabled = (await read_json(request)).get("enabled") is True
    setting = _module_setting(module)
    running = getattr(d.settings, setting)
    if running != enabled and (not enabled or module in NEEDS_NO_SETUP):
        await apply_settings(d, {setting: enabled}, _actor(request))
    saved = d.app.db.get("web.modules") or {}
    d.app.db.set("web.modules", {**saved, module: enabled})
    d.app.db.audit(_actor(request), "module", f"{module} {'on' if enabled else 'off'}")
    return web.json_response(
        {
            "ok": True,
            "restart": enabled != _module_running(d, module)
            and (not enabled or module in NEEDS_NO_SETUP),
        }
    )


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
    recipients = list(d.settings.email_recipients)
    if not recipients:
        raise web.HTTPConflict(text="web.email_no_recipients")
    text = t("email.test", d.settings.language)
    try:
        for recipient in recipients:
            await provider.send(recipient, Reply(text))
    except ProviderUnavailableError:
        raise web.HTTPBadGateway(text="web.email_failed") from None
    return web.json_response({"ok": True, "sent": len(recipients)})


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
        "admins": sum(1 for u in d.app.users.all("discord") if u.role is Role.ADMIN),
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


async def messengers(request: web.Request) -> web.Response:
    """Every messenger that is set up, for test messages in the expert view."""
    d = dashboard(request)
    app = d.app
    out = []
    for name, provider in sorted(app.providers.items()):
        if name == "email":
            admins = len(d.settings.email_recipients)
        else:
            admins = sum(1 for u in app.users.all(name) if u.role is Role.ADMIN)
        out.append(
            {
                "name": name,
                "connected": bool(getattr(provider, "connected", True)),
                "admins": admins,
            }
        )
    return web.json_response(out)


async def messenger_test(request: web.Request) -> web.Response:
    """One test message to each admin there, email recipients, or a chat-app channel."""
    d = dashboard(request)
    app, name = d.app, request.match_info["name"]
    if name not in app.providers:
        raise web.HTTPConflict(text="web.messenger_off")
    # Email uses the same path as /api/email/test (recipients, not linked admins).
    if name == "email":
        return await email_test(request)
    chats = [u.user_id for u in app.users.all(name) if u.role is Role.ADMIN]
    if not chats and name in CHAT_APPS:
        chats = [""]  # a chat app posts into its one channel
    text = t("web.messenger_test_message", d.settings.language)
    sent = 0
    for chat in chats:
        try:
            await app.messenger.send(name, chat, Reply(text))
            sent += 1
        except (ProviderUnavailableError, RuntimeError, OSError):
            log.warning("%s test message not delivered", name)
    if not sent:
        raise web.HTTPBadGateway(text="web.messenger_not_delivered")
    d.app.db.audit(f"web:{current(request).username}", "messenger.test", name)
    return web.json_response({"sent": sent})


# --- Signal ---------------------------------------------------------------------------------


async def signal_status(request: web.Request) -> web.Response:
    d = dashboard(request)
    s = d.settings
    provider = d.app.providers.get("signal")
    version, accounts, busy = None, [], False
    try:
        version = (await rpc(s.signal_cli_address, "version", wait=3) or {}).get("version")
        found = await rpc(s.signal_cli_address, "listAccounts", wait=3) or []
        accounts = [a.get("number") for a in found if isinstance(a, dict)]
    except TimeoutError:
        busy = True  # it took the connection but does not answer: for a minute after linking
    except (OSError, ValueError, RuntimeError) as exc:
        log.debug("signal-cli: %s", type(exc).__name__)
    return web.json_response({
        "enabled": s.signal_enabled, "installed": shutil.which("signal-cli") is not None,
        "running": version is not None or busy, "version": version, "busy": busy,
        "linked": s.signal_phone_number in accounts or _links.get("state") == "linked",
        "accounts": len(accounts),
        "connected": bool(provider and provider.connected),
        "commands": s.signal_commands,
        "admins": sum(1 for u in d.app.users.all("signal") if u.role is Role.ADMIN),
        "link": {k: v for k, v in _links.items() if k != "task"},
    })  # fmt: skip


async def _finish_link(d: Any, uri: str, actor: str) -> None:
    address = d.settings.signal_cli_address
    try:
        result = await rpc(address, "finishLink", {"deviceLinkUri": uri, "deviceName": "pi-guard"},
                           wait=LINK_SECONDS)  # fmt: skip
    except RuntimeError as exc:
        expired = "timed out" in str(exc).lower()
        if not expired:
            text = re.sub(r"sgnl://\S+", "(code)", str(exc))
            log.warning("linking Signal failed: %s", text[:160])
        _links.update(state="expired" if expired else "failed")
        return
    except (OSError, TimeoutError, ValueError) as exc:
        log.warning("linking Signal failed: %s", type(exc).__name__)
        _links.update(state="failed")
        return
    # The number comes from the phone, so nobody has to type it and get it slightly wrong.
    number = (result or {}).get("number")
    if not number:
        found = await rpc(address, "listAccounts", wait=5) or []
        number = found[0].get("number") if len(found) == 1 else None
    if number:
        try:
            values = {"signal_phone_number": number, "signal_enabled": True}
            if not d.settings.signal_admin_numbers:  # whoever links their phone runs this Pi
                values["signal_admin_numbers"] = [number]
            await apply_settings(d, values, actor)
        except (OSError, ValueError, web.HTTPException) as exc:
            log.warning("storing the Signal number failed: %s", type(exc).__name__)
    _links.update(state="linked", number=number)


async def signal_link_state(request: web.Request) -> web.Response:
    """Asks nobody: signal-cli hardly answers while it takes over a phone's data."""
    return web.json_response({k: v for k, v in _links.items() if k != "task"})


async def signal_test(request: web.Request) -> web.Response:
    d = dashboard(request)
    number = _links.get("number") or d.settings.signal_phone_number
    if not number:
        raise web.HTTPConflict(text="web.signal_unlinked")
    text = t("web.signal_test_message", d.settings.language)
    try:
        await rpc(d.settings.signal_cli_address, "send",
                  {"account": number, "recipient": [number], "message": text}, wait=30)  # fmt: skip
    except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
        log.warning("Signal test message failed: %s", type(exc).__name__)
        raise web.HTTPBadGateway(text="web.signal_test_failed") from None
    return web.json_response({"ok": True})


async def signal_link(request: web.Request) -> web.Response:
    d = dashboard(request)
    address = d.settings.signal_cli_address
    if _links.get("state") == "linked":  # a code renewed a moment too late must not undo this
        return web.json_response({"linked": True})
    try:
        started = await rpc(address, "startLink", wait=20)
    except (OSError, TimeoutError, ValueError, RuntimeError):
        raise web.HTTPConflict(text="web.signal_not_running") from None
    uri = str(started["deviceLinkUri"])
    if previous := _links.get("task"):  # its code is gone from the screen
        previous.cancel()
    _links.clear()
    actor = _actor(request)
    _links.update(state="waiting", task=asyncio.create_task(_finish_link(d, uri, actor)))
    d.app.db.audit(actor, "signal.link")
    return web.json_response({"qr": _qr_svg(uri), "seconds": LINK_VALID})


# --- calendars ------------------------------------------------------------------------------


async def calendars_list(request: web.Request) -> web.Response:
    db = dashboard(request).app.db
    return web.json_response({"feeds": Feeds(db).public(),
                              "phone": len(db.get(CALENDAR_KEY) or [])})  # fmt: skip


async def calendars_add(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    try:
        await Feeds(d.app.db).add(str(data.get("name", "")), str(data.get("link", "")))
    except CalendarError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    d.app.db.audit(_actor(request), "calendar.add", str(data.get("name", ""))[:40])
    return web.json_response({"ok": True})


async def calendars_refresh(request: web.Request) -> web.Response:
    await Feeds(dashboard(request).app.db).refresh()
    return web.json_response({"ok": True})


async def calendars_remove(request: web.Request) -> web.Response:
    d = dashboard(request)
    if not Feeds(d.app.db).remove(request.match_info["id"]):
        raise web.HTTPNotFound()
    d.app.db.audit(_actor(request), "calendar.remove")
    return web.json_response({"ok": True})


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


WIFI_PROFILES_KEY = "network.wifi_profiles"
# Two saved networks, for homes without a FRITZ!Box: the own one and the one for guests.
WIFI_SLOTS = ("home", "guest")


_WIFI_AAD = b"pi-guard-wifi"


def _wifi_cipher(d: Any) -> Any:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(hashlib.sha256(b"pi-guard wifi key\0" + d._secret_key()).digest())


def _seal_wifi(d: Any, key: str) -> str:
    nonce = secrets.token_bytes(12)
    return base64.b64encode(
        nonce + _wifi_cipher(d).encrypt(nonce, key.encode(), _WIFI_AAD)
    ).decode()


def _open_wifi(d: Any, sealed: str) -> str:
    from cryptography.exceptions import InvalidTag

    try:
        raw = base64.b64decode(sealed)
        return str(_wifi_cipher(d).decrypt(raw[:12], raw[12:], _WIFI_AAD).decode())
    except (InvalidTag, ValueError):
        return ""  # the dashboard secret key changed: the key has to be typed again


def _save_wifi(d: Any, profiles: list[dict[str, Any]]) -> None:
    """Stored with the Wi-Fi key sealed, like other secrets on the Pi."""
    stored = [{**{k: v for k, v in p.items() if k != "key"}, "key_sealed": _seal_wifi(d, p["key"])}
              for p in profiles]  # fmt: skip
    d.app.db.set(WIFI_PROFILES_KEY, stored)


def _wifi_profiles(d: Any) -> list[dict[str, Any]]:
    """The saved networks with their keys opened, at most one per slot.

    Before 1.14 there was a free list, and before 1.13 keys were kept in plain text: the first
    network of each kind becomes its slot, sealed, the first time it is read."""
    raw = d.app.db.get(WIFI_PROFILES_KEY) or []
    if not isinstance(raw, list):
        return []
    profiles: dict[str, dict[str, Any]] = {}
    for p in raw:
        if not isinstance(p, dict) or not p.get("ssid"):
            continue
        slot = (
            str(p.get("id")) if p.get("id") in WIFI_SLOTS else "guest" if p.get("guest") else "home"
        )
        if slot in profiles:
            continue
        key = str(p["key"]) if "key" in p else _open_wifi(d, str(p.get("key_sealed", "")))
        profiles[slot] = {"id": slot, "ssid": str(p["ssid"])[:32], "key": key}
    opened = [profiles[slot] for slot in WIFI_SLOTS if slot in profiles]
    if [p.get("id") for p in raw if isinstance(p, dict)] != [p["id"] for p in opened] or any(
        isinstance(p, dict) and "key" in p for p in raw
    ):
        _save_wifi(d, opened)
    return opened


async def wifi_profiles_get(request: web.Request) -> web.Response:
    """The two saved networks; keys are never returned here."""
    profiles = [
        {"id": p["id"], "ssid": p["ssid"], "guest": p["id"] == "guest", "has_key": bool(p["key"])}
        for p in _wifi_profiles(dashboard(request))
    ]
    return web.json_response({"profiles": profiles})


async def wifi_profile_put(request: web.Request) -> web.Response:
    """Save the home or guest network (name and key) after the password."""
    d = dashboard(request)
    data = await read_json(request)
    _confirm(request, data)
    slot = request.match_info["slot"]
    ssid, key = str(data.get("ssid", "")).strip()[:32], str(data.get("key", ""))
    saved = {p["id"]: p for p in _wifi_profiles(d)}
    if not key and slot in saved:
        key = saved[slot]["key"]  # only the name changed
    if not ssid or not 8 <= len(key) <= 63:
        raise web.HTTPBadRequest(text="web.wifi_bad_input")
    saved[slot] = {"id": slot, "ssid": ssid, "key": key}
    _save_wifi(d, [saved[s] for s in WIFI_SLOTS if s in saved])
    d.app.db.audit(_actor(request), "network.wifi_profile", slot)
    return await wifi_profiles_get(request)


async def wifi_profile_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    data = await read_json(request)
    _confirm(request, data)
    slot = request.match_info["slot"]
    _save_wifi(d, [p for p in _wifi_profiles(d) if p["id"] != slot])
    d.app.db.audit(_actor(request), "network.wifi_profile_delete", slot)
    return await wifi_profiles_get(request)


async def wifi_qr(request: web.Request) -> web.Response:
    """From a saved network or the FRITZ!Box."""
    d = dashboard(request)
    data = await read_json(request)
    ssid, key = "", ""
    index = data.get("index")
    profile_id = str(data.get("profile") or "").strip()
    if profile_id:
        match = next(
            (p for p in _wifi_profiles(d) if str(p.get("id")) == profile_id),
            None,
        )
        if match is None:
            raise web.HTTPNotFound(text="web.wifi_no_profile")
        ssid, key = str(match.get("ssid") or ""), str(match.get("key") or "")
    elif index in (1, 2, 3):
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


# --- plan, navigation ------------------------------------------------------------------------


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
    last = app.scanner.last
    assistant = app.addons.assistant
    return web.json_response({
        "running": app.scanner.running, "progress": app.scanner.progress, "last": last,
        "root_job": HELPER.exists(),
        "fixes": {"offered": fixes.offered(last), "applied": fixes.applied(),
                  "result": fixes.result()},
        "review": assistant is not None and assistant.ready,
    })  # fmt: skip


async def scan_fix(request: web.Request) -> web.Response:
    """One fix from the fixed list, or its undo. Needs the password and the fix's name typed."""
    d = dashboard(request)
    data = await read_json(request)
    name, undo = str(data.get("id", "")), data.get("undo") is True
    if name not in fixes.FIXES:
        raise web.HTTPNotFound()
    confirm_password(request, str(data.get("password", "")))
    if not undo and str(data.get("confirm", "")).strip().casefold() != name:
        raise web.HTTPBadRequest(text="web.scan_fix_confirm_wrong")
    if not HELPER.exists():
        raise web.HTTPConflict(text="web.scan_no_job")
    request_root(d.settings.data_dir, f"{'undo' if undo else 'fix'}-{name}")
    d.app.db.audit(_actor(request), "security.fix", f"{'undo ' if undo else ''}{name}")
    return web.json_response({"ok": True})


async def scan_review(request: web.Request) -> web.Response:
    """The assistant's view of the latest result: steps, and which fixed fix does each."""
    from pi_guard.addons.assistant import LANGUAGE_NAMES, AssistantError

    d = dashboard(request)
    assistant = d.app.addons.assistant
    last = d.app.scanner.last
    if assistant is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    if not last:
        raise web.HTTPConflict(text="web.scan_none_yet")
    language = LANGUAGE_NAMES.get(d.settings.language, "German")
    try:
        answer, _ = await assistant.complete_checked(fixes.review_prompt(language),
                                                     fixes.review_input(last))  # fmt: skip
    except AssistantError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    d.app.db.audit(_actor(request), "security.review")
    return web.json_response(fixes.parse_review(answer))


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

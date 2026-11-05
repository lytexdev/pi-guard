"""Dashboard API of the add-ons, and the connection information on the overview page."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from aiohttp import web
from pydantic import ValidationError

from pi_guard.addons.assistant import AssistantError
from pi_guard.addons.knowledge import Knowledge, KnowledgeError
from pi_guard.addons.languages import LANGUAGES as language_names
from pi_guard.addons.languages import installed, translate_all
from pi_guard.addons.remote import ConfirmationError
from pi_guard.addons.smarthome import SmartHomeError
from pi_guard.audio.service import AudioError
from pi_guard.config.files import AddonSettings, FileConfig
from pi_guard.i18n import remove_extra
from pi_guard.messaging.commands.addons import known_names
from pi_guard.network.pihole import PiholeError
from pi_guard.network.wgpeers import SUBNET
from pi_guard.network.wireguard import read_status
from pi_guard.web.api_manage import saved_config, write_config
from pi_guard.web.server import current, dashboard, read_body, read_json

log = logging.getLogger(__name__)

ADDONS = ("heartbeat", "analytics", "smarthome", "remote", "assistant", "agents", "languages")
# What an add-on leaves on the disk.
ADDON_FILES = {
    "assistant": ("knowledge.db", "knowledge.db-wal", "knowledge.db-shm"),
    "languages": ("i18n",),
}
MAX_VOICE_BYTES = 5_000_000
HOME_IP_SECONDS = 600
TOR_LIST_SECONDS = 6 * 3600
TOR_LIST_URL = "https://check.torproject.org/torbulkexitlist"
VPN_HINTS = (
    "vpn", "mullvad", "proton", "nord", "surfshark", "expressvpn", "cyberghost", "private internet",
    "m247", "datacamp", "hosting", "datacenter", "data center", "server", "cloud", "ovh", "hetzner",
    "digitalocean", "linode", "vultr", "amazon", "google llc", "microsoft", "choopa", "leaseweb",
)  # fmt: skip


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/addons", catalog),
        web.put("/api/addons/{addon:[a-z]+}", save_addon),
        web.delete("/api/addons/{addon}", remove_addon),
        web.get("/api/addons/heartbeat/status", heartbeat_status),
        web.get("/api/addons/analytics/overview", analytics_overview),
        web.get("/api/addons/analytics/device/{ip}", analytics_device),
        web.get("/api/addons/smarthome/status", smarthome_status),
        web.post("/api/addons/smarthome/{id}", smarthome_switch),
        web.get("/api/addons/remote/status", remote_status),
        web.post("/api/addons/remote/begin", remote_begin),
        web.post("/api/addons/remote/confirm", remote_confirm),
        web.post("/api/addons/remote/cancel", remote_cancel),
        web.get("/api/addons/assistant/status", assistant_status),
        web.post("/api/addons/assistant/ask", assistant_ask),
        web.post("/api/assistant/chat", assistant_chat),
        web.post("/api/assistant/chat/voice", assistant_chat_voice),
        web.post("/api/assistant/confirm", assistant_confirm),
        web.get("/api/assistant/jobs", assistant_jobs),
        web.delete("/api/assistant/jobs/{id:\\d+}", assistant_job_delete),
        web.post("/api/addons/assistant/voice", assistant_voice),
        web.get("/api/addons/assistant/knowledge", knowledge_list),
        web.post("/api/addons/assistant/knowledge", knowledge_add),
        web.delete("/api/addons/assistant/knowledge/{id:\\d+}", knowledge_delete),
        web.delete("/api/addons/assistant/answers", answers_forget),
        web.get("/api/addons/languages/status", languages_status),
        web.post("/api/addons/languages/{code}", language_add),
        web.delete("/api/addons/languages/{code}", language_delete),
        web.get("/api/connection", connection),
        web.post("/api/connection/check", connection_check),
    ]


def _actor(request: web.Request) -> str:
    return f"web:{current(request).username}"


def _saved_config(request: web.Request) -> dict[str, Any]:
    return saved_config(dashboard(request))


async def catalog(request: web.Request) -> web.Response:
    d = dashboard(request)
    app = d.app
    saved = AddonSettings.model_validate(_saved_config(request).get("addons") or {})
    config = saved.model_dump(mode="json")
    s = d.settings
    needs = {
        "analytics": None if s.pihole_enabled else "web.addon_needs_pihole",
        "smarthome": None if s.mqtt_host else "web.addon_needs_mqtt",
    }
    return web.json_response({
        "addons": [{
            "id": addon, "enabled": config[addon]["enabled"],
            "running": bool(getattr(app.addons, addon)), "needs": needs.get(addon),
            "size": sum(_size(p) for p in _removable(d, addon)),
        } for addon in ADDONS],
        "config": config,
        "secrets": {
            "assistant_api_key": bool(s.assistant_api_key),
            "assistant_deepl_key": bool(s.assistant_deepl_key),
        },
        "plugins": [{"id": p.id, "title": p.title, "description": p.description}
                    for p in app.addons.plugins],
    })  # fmt: skip


def _removable(d: Any, addon: str) -> list[Path]:
    return [d.settings.data_dir / name for name in ADDON_FILES.get(addon, ())]


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.is_dir() else 0


async def remove_addon(request: web.Request) -> web.Response:
    """Off, with its settings and data gone. Installing it again starts from nothing."""
    addon = request.match_info["addon"]
    if addon not in ADDONS:
        raise web.HTTPNotFound()
    d = dashboard(request)
    config = _saved_config(request)
    config.setdefault("addons", {}).pop(addon, None)
    await write_config(d, FileConfig.model_validate(config))
    freed = 0
    for path in _removable(d, addon):
        freed += _size(path)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    d.app.db.audit(_actor(request), "addon.remove", addon)
    return web.json_response({"ok": True, "freed": freed, "restart_required": True})


async def save_addon(request: web.Request) -> web.Response:
    addon = request.match_info["addon"]
    if addon not in ADDONS:
        raise web.HTTPNotFound()
    data = await read_json(request)
    config = _saved_config(request)
    config.setdefault("addons", {})[addon] = data
    try:
        validated = FileConfig.model_validate(config)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, e['loc'][2:]))}: {e['msg']}" for e in exc.errors()[:5]]
        raise web.HTTPBadRequest(text="\n".join(errors)) from None
    d = dashboard(request)
    await write_config(d, validated)
    d.app.db.audit(_actor(request), "addon.config", addon)
    return web.json_response({"ok": True, "restart_required": True})


def _running(request: web.Request, addon: str) -> Any:
    instance = getattr(dashboard(request).app.addons, addon)
    if instance is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return instance


# --- heartbeat, analytics, smart home ------------------------------------------------------


async def heartbeat_status(request: web.Request) -> web.Response:
    return web.json_response(_running(request, "heartbeat").overview())


async def analytics_overview(request: web.Request) -> web.Response:
    analytics = _running(request, "analytics")
    try:
        return web.json_response(await analytics.overview(known_names(dashboard(request).app)))
    except PiholeError as exc:
        raise web.HTTPBadGateway(text=str(exc)) from None


async def analytics_device(request: web.Request) -> web.Response:
    analytics = _running(request, "analytics")
    try:
        return web.json_response(await analytics.device(request.match_info["ip"]))
    except ValueError:
        raise web.HTTPBadRequest(text="analytics.bad_ip") from None
    except PiholeError as exc:
        raise web.HTTPBadGateway(text=str(exc)) from None


async def smarthome_status(request: web.Request) -> web.Response:
    home = _running(request, "smarthome")
    return web.json_response({"connected": home.connected, "devices": home.overview()})


async def smarthome_switch(request: web.Request) -> web.Response:
    home = _running(request, "smarthome")
    data = await read_json(request)
    on = data.get("on")
    try:
        await home.switch(request.match_info["id"], on if isinstance(on, bool) else None)
    except SmartHomeError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    dashboard(request).app.db.audit(_actor(request), "smarthome.switch", request.match_info["id"])
    return web.json_response({"ok": True})


# --- remote actions -------------------------------------------------------------------------


async def remote_status(request: web.Request) -> web.Response:
    remote = _running(request, "remote")
    return web.json_response({
        "public_key": await remote.public_key(),
        "actions": [{"id": a.id, "name": a.name, "host": a.host} for a in remote.actions.values()],
    })  # fmt: skip


async def remote_begin(request: web.Request) -> web.Response:
    remote = _running(request, "remote")
    action_id = str((await read_json(request)).get("id", ""))
    try:
        token = remote.begin(action_id, _actor(request))
    except ConfirmationError as exc:
        raise web.HTTPBadRequest(text=exc.key) from None
    return web.json_response({"token": token})


async def remote_confirm(request: web.Request) -> web.Response:
    """First call: returns the token for the second step. Second call: also needs the action's
    name typed by hand, then runs it."""
    remote = _running(request, "remote")
    data = await read_json(request)
    token, typed = str(data.get("token", "")), str(data.get("typed", "")).strip()
    try:
        action, following = remote.confirm(token, _actor(request))
    except ConfirmationError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    if following is not None:
        return web.json_response({"token": following, "name": action.name, "host": action.host})
    if typed.casefold() != action.name.casefold():
        raise web.HTTPBadRequest(text="remote.expired")
    app = dashboard(request).app
    app.db.audit(_actor(request), "remote.run", action.id)
    result = await remote.run(action)
    app.db.audit(_actor(request), "remote.result", f"{action.id}: {result.exit_code}")
    return web.json_response({"done": True, "ok": result.ok, "output": result.output})


async def remote_cancel(request: web.Request) -> web.Response:
    _running(request, "remote").cancel(str((await read_json(request)).get("token", "")))
    return web.json_response({"ok": True})


# --- assistant ------------------------------------------------------------------------------


async def assistant_status(request: web.Request) -> web.Response:
    return web.json_response(_running(request, "assistant").status())


async def _reply(request: web.Request, question: str, speak: bool) -> web.Response:
    d = dashboard(request)
    assistant = _running(request, "assistant")
    lang = d.settings.language
    answer = await assistant.ask(question, lang, _actor(request), private=True)
    spoken = False
    if speak and d.app.audio is not None:
        path = await assistant.speak(answer.text)
        if path is not None:
            try:
                await d.app.audio.play(path, ignore_quiet_hours=True, speech=True)
                spoken = True
            except AudioError:
                pass
            finally:
                path.unlink(missing_ok=True)
    return web.json_response({"question": question, "answer": answer.text, "source": answer.source,
                              "tokens": answer.tokens, "spoken": spoken,
                              "documents": list(answer.documents),
                              "tokens_today": assistant.tokens_today})  # fmt: skip


async def assistant_ask(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        return await _reply(request, str(data.get("question", "")), data.get("speak") is True)
    except AssistantError as exc:
        raise web.HTTPConflict(text=exc.key) from None


async def assistant_voice(request: web.Request) -> web.Response:
    assistant = _running(request, "assistant")
    body = await read_body(request, MAX_VOICE_BYTES)
    if not body:
        raise web.HTTPBadRequest(text="web.error")
    path = Path(tempfile.gettempdir()) / f"pi-guard-ask-{uuid.uuid4().hex}.webm"
    path.write_bytes(body)
    try:
        question = await assistant.transcribe(path, dashboard(request).settings.language)
        return await _reply(request, question, request.query.get("speak") == "1")
    except AssistantError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    finally:
        path.unlink(missing_ok=True)


def _shown(outcome: Any) -> dict[str, Any]:
    photo = f"/api/media/snapshots/{outcome.photo.name}" if outcome.photo else None
    return {"reply": outcome.text, "pending": outcome.pending, "photo": photo}


async def _chat(request: web.Request, text: str) -> web.Response:
    d = dashboard(request)
    assistant = _running(request, "assistant")
    lang = d.settings.language
    commander = d.app.addons.commander
    assert commander is not None  # exists whenever the assistant does
    try:
        outcome = await commander.propose(text, lang, _actor(request))
        if outcome is not None:
            return web.json_response({"question": text, **_shown(outcome)})
        answer = await assistant.ask(text, lang, _actor(request), private=True)
    except AssistantError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    return web.json_response({"question": text, "reply": answer.text, "pending": None,
                              "documents": list(answer.documents)})  # fmt: skip


async def assistant_chat(request: web.Request) -> web.Response:
    return await _chat(request, str((await read_json(request)).get("text", "")))


async def assistant_chat_voice(request: web.Request) -> web.Response:
    assistant = _running(request, "assistant")
    body = await read_body(request, MAX_VOICE_BYTES)
    if not body:
        raise web.HTTPBadRequest(text="web.error")
    path = Path(tempfile.gettempdir()) / f"pi-guard-chat-{uuid.uuid4().hex}.webm"
    path.write_bytes(body)
    try:
        text = await assistant.transcribe(path, dashboard(request).settings.language)
    except AssistantError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    finally:
        path.unlink(missing_ok=True)
    return await _chat(request, text)


async def assistant_confirm(request: web.Request) -> web.Response:
    d = dashboard(request)
    commander = d.app.addons.commander
    if commander is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    data = await read_json(request)
    actor = _actor(request)

    async def deliver(path: str) -> None:
        d.app.db.audit(actor, "assistant.video_ready", Path(path).name)

    try:
        outcome = await commander.answer(str(data.get("token", "")), data.get("yes") is True,
                                         d.settings.language, actor, deliver, {})  # fmt: skip
    except AssistantError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    return web.json_response(_shown(outcome))


async def assistant_jobs(request: web.Request) -> web.Response:
    jobs = dashboard(request).app.addons.jobs
    return web.json_response(jobs.list() if jobs else [])


async def assistant_job_delete(request: web.Request) -> web.Response:
    jobs = dashboard(request).app.addons.jobs
    if jobs is None or not jobs.delete(int(request.match_info["id"])):
        raise web.HTTPNotFound()
    return web.json_response({"ok": True})


def _knowledge(request: web.Request) -> Knowledge:
    knowledge = _running(request, "assistant").knowledge
    if knowledge is None:
        raise web.HTTPConflict(text="web.addon_not_running")
    return knowledge  # type: ignore[no-any-return]


async def knowledge_list(request: web.Request) -> web.Response:
    return web.json_response(_knowledge(request).files())


async def knowledge_add(request: web.Request) -> web.Response:
    """Files are checked one by one; a bad file does not stop the others."""
    knowledge = _knowledge(request)
    reader = await request.multipart()
    results = []
    async for part in reader:
        if not getattr(part, "filename", None):
            continue
        name = str(part.filename)  # type: ignore[union-attr]
        data = await part.read(decode=False)  # type: ignore[union-attr]
        try:
            info = await asyncio.to_thread(knowledge.add, name, bytes(data))
            results.append({**info, "ok": True})
        except KnowledgeError as exc:
            results.append({"name": name, "ok": False, "error": exc.key})
    dashboard(request).app.db.audit(_actor(request), "assistant.knowledge", str(len(results)))
    return web.json_response(results)


async def knowledge_delete(request: web.Request) -> web.Response:
    if not _knowledge(request).delete(int(request.match_info["id"])):
        raise web.HTTPNotFound()
    return web.json_response({"ok": True})


async def answers_forget(request: web.Request) -> web.Response:
    return web.json_response({"removed": _knowledge(request).forget_answers()})


# --- languages ------------------------------------------------------------------------------

_translating: dict[str, Any] = {}


async def languages_status(request: web.Request) -> web.Response:
    d = dashboard(request)
    return web.json_response({
        "available": language_names, "installed": installed(d.settings.data_dir / "i18n"),
        "running": _translating, "key": bool(d.settings.assistant_deepl_key),
    })  # fmt: skip


async def language_add(request: web.Request) -> web.Response:
    d = dashboard(request)
    code = request.match_info["code"]
    key = d.settings.assistant_deepl_key
    if code not in language_names:
        raise web.HTTPNotFound()
    if key is None or not key.get_secret_value():
        raise web.HTTPConflict(text="web.lg_no_key")
    if _translating:
        raise web.HTTPConflict(text="web.lg_busy")

    def progress(done: int, total: int) -> None:
        _translating.update(code=code, done=done, total=total)

    async def work() -> None:
        progress(0, 1)
        try:
            await translate_all(
                code, key.get_secret_value(), d.settings.data_dir / "i18n", progress
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.warning("translation failed: %s", type(exc).__name__)
        finally:
            _translating.clear()

    d.background(work())
    return web.json_response({"ok": True})


async def language_delete(request: web.Request) -> web.Response:
    d = dashboard(request)
    code = request.match_info["code"]
    path = d.settings.data_dir / "i18n" / f"{code}.json"
    if code not in language_names or not path.exists():
        raise web.HTTPNotFound()
    path.unlink()
    remove_extra(code)
    return web.json_response({"ok": True})


# --- connection -----------------------------------------------------------------------------

_home_ip: tuple[float, str | None] = (0.0, None)
_tor_exits: tuple[float, frozenset[str]] = (0.0, frozenset())


async def home_ip() -> str | None:
    """This home's public address (cached)."""
    global _home_ip
    if time.monotonic() - _home_ip[0] < HOME_IP_SECONDS and _home_ip[1]:
        return _home_ip[1]
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get("https://api.ipify.org")
        ip = str(ipaddress.ip_address(response.text.strip()))
    except (httpx.HTTPError, ValueError):
        return _home_ip[1]
    _home_ip = (time.monotonic(), ip)
    return ip


async def tor_exits() -> frozenset[str]:
    global _tor_exits
    if time.monotonic() - _tor_exits[0] < TOR_LIST_SECONDS and _tor_exits[1]:
        return _tor_exits[1]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(TOR_LIST_URL)
        response.raise_for_status()
    except httpx.HTTPError:
        return _tor_exits[1]
    _tor_exits = (time.monotonic(), frozenset(response.text.split()))
    return _tor_exits[1]


async def _organisation(ip: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"https://ipwho.is/{ip}", params={"fields": "connection"})
        return str((response.json().get("connection") or {}).get("org") or "") or None
    except (httpx.HTTPError, ValueError):
        return None


def classify(ip: str, home: str | None, tor: frozenset[str], organisation: str | None) -> str:
    if ip in tor:
        return "tor"
    if home and ip == home:
        return "home"
    if organisation and any(hint in organisation.casefold() for hint in VPN_HINTS):
        return "vpn"
    return "other"


async def connection(request: web.Request) -> web.Response:
    """How this browser reaches the dashboard."""
    d = dashboard(request)
    ip = d.guard.client_ip(request)
    kind, peer = "unknown", None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        address = None
    if address is not None:
        if address.is_loopback:
            kind = "local"
        elif address in SUBNET:
            kind = "wireguard"
            peer = known_names(d.app).get(ip)
        elif address.is_private:
            kind = "lan"
        else:
            kind = "tor" if ip in await tor_exits() else "public"
    status = (
        read_status(d.settings.runtime_dir / "wireguard.status")
        if d.settings.wireguard_enabled
        else None
    )
    return web.json_response({
        "ip": ip, "kind": kind, "peer": peer, "home_ip": await home_ip(),
        "vpn_clients": sum(1 for p in (status or {}).get("peers", []) if p.get("active")),
    })  # fmt: skip


async def connection_check(request: web.Request) -> web.Response:
    """The public address the browser appears with, as it found out itself."""
    data = await read_json(request)
    try:
        ip = str(ipaddress.ip_address(str(data.get("ip", ""))))
    except ValueError:
        raise web.HTTPBadRequest(text="bad address") from None
    home, tor = await asyncio.gather(home_ip(), tor_exits())
    organisation = None if ip == home else await _organisation(ip)
    return web.json_response({"ip": ip, "kind": classify(ip, home, tor, organisation),
                              "organisation": organisation})  # fmt: skip

"""Dashboard API for the optional privacy upstream (Mullvad, multi-hop, DNS, kill switch)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from pi_guard.network import awg
from pi_guard.privacy.capabilities import installed_marker
from pi_guard.privacy.errors import PrivacyError
from pi_guard.privacy.service import PrivacyService
from pi_guard.web.server import confirm_password, current, dashboard, read_json

log = logging.getLogger(__name__)


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/privacy/capabilities", capabilities),
        web.get("/api/privacy/config", config_get),
        web.get("/api/privacy/diagnostics", diagnostics),
        web.put("/api/privacy/killswitch", killswitch_put),
        web.put("/api/privacy/dns", dns_put),
        web.post("/api/privacy/chain/hops", hop_add),
        web.delete("/api/privacy/chain/hops/{id}", hop_remove),
        web.put("/api/privacy/modules", modules_put),
        web.post("/api/privacy/providers/custom", custom_add),
        web.post("/api/privacy/providers/custom/import", custom_import),
        web.delete("/api/privacy/providers/custom/{id}", custom_remove),
        web.post("/api/privacy/providers/mullvad/account", mullvad_account),
        web.delete("/api/privacy/providers/mullvad/account", mullvad_remove),
        web.get("/api/privacy/providers/mullvad/relays", mullvad_relays),
        web.put("/api/privacy/providers/mullvad/server", mullvad_server),
    ]


def _svc(request: web.Request) -> PrivacyService:
    d = dashboard(request)
    if d.privacy is None:
        d.privacy = PrivacyService(
            d.settings.data_dir,
            d._secret_key(),
            home_network=d.settings.wireguard_home_network or None,
        )
    return d.privacy


def _err(exc: PrivacyError) -> web.HTTPException:
    return web.HTTPBadRequest(text=str(exc))


async def capabilities(request: web.Request) -> web.Response:
    return web.json_response(_svc(request).capabilities())


def _with_layers(request: web.Request, cfg: dict[str, Any]) -> dict[str, Any]:
    """The real state of the AmneziaWG layer: installed on this Pi, and switched on or off."""
    data_dir = dashboard(request).settings.data_dir
    installed = awg.read() is not None
    return {**cfg, "amneziawg": {"installed": installed,
                                 "on": installed and awg.enabled(data_dir)}}  # fmt: skip


async def config_get(request: web.Request) -> web.Response:
    cfg = await asyncio.to_thread(_with_layers, request, _svc(request).config())
    return web.json_response(cfg, headers={"Cache-Control": "no-store"})


async def diagnostics(request: web.Request) -> web.Response:
    from pi_guard.privacy.health import remember_exit_ip

    svc = _svc(request)
    exit_ip = None
    if request.query.get("check") == "1":
        exit_ip = await _probe_exit_ip()
        if exit_ip:
            kind = (
                "exit"
                if svc.store.data.get("enabled") and svc.store.data["chain"].get("active")
                else "public"
            )
            remember_exit_ip(exit_ip, kind=kind)
    return web.json_response(
        svc.diagnostics(exit_ip=exit_ip), headers={"Cache-Control": "no-store"}
    )


async def _probe_exit_ip() -> str | None:
    """Best-effort public IP as seen from the Pi (after exit routing when active)."""
    import aiohttp

    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get("https://api.ipify.org") as resp,
        ):
            if resp.status == 200:
                text = (await resp.text()).strip()
                return text[:45] if text else None
    except (aiohttp.ClientError, TimeoutError, OSError):
        return None
    return None


async def killswitch_put(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        result = _svc(request).set_kill_switch(str(data.get("mode") or "off"))
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.killswitch", str(data.get("mode"))
    )
    return web.json_response(result)


async def dns_put(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        result = _svc(request).set_dns(data)
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.dns", str(data.get("mode") or "")
    )
    return web.json_response(result)


async def hop_add(request: web.Request) -> web.Response:
    data = await read_json(request)
    svc = _svc(request)
    try:
        hop = svc.add_hop(data)  # validates and normalizes
    except PrivacyError as exc:
        raise _err(exc) from None
    _set_exit(svc, hop)
    await _apply_exit(svc)
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.hop_add", str(hop.get("provider") or "")
    )
    return web.json_response(hop)


async def hop_remove(request: web.Request) -> web.Response:
    hop_id = request.match_info["id"]
    svc = _svc(request)
    try:
        svc.remove_hop(hop_id)
    except PrivacyError as exc:
        raise _err(exc) from None
    await _apply_exit(svc)
    return web.json_response({"ok": True})


async def _apply_exit(svc: Any) -> None:
    """Start the exit when one is in the chain, stop it when the last one is gone."""
    hops = [h for h in (svc.store.data["chain"].get("hops") or []) if h.get("enabled") is not False]
    try:
        if hops and installed_marker(svc._data_dir):
            await asyncio.to_thread(svc.activate)
        elif svc.store.data.get("enabled"):
            await asyncio.to_thread(svc.deactivate)
    except PrivacyError as exc:
        raise _err(exc) from None


def _mullvad_hop(selected: dict[str, Any]) -> dict[str, Any]:
    """No params of its own: the exit is built from the stored selection, so switching
    multi-hop off takes effect (a copy in the hop kept the entry server)."""
    names = [selected.get("entry_hostname"), selected.get("hostname")]
    return {"id": "mullvad", "provider": "mullvad", "enabled": True, "params": {},
            "name": " → ".join(filter(None, names)) or "Mullvad",
            "server_id": selected.get("hostname")}  # fmt: skip


def _set_exit(svc: Any, hop: dict[str, Any] | None) -> None:
    """The Pi has one exit tunnel: a new one replaces the old one."""
    try:
        svc.set_chain([hop] if hop else [])
    except PrivacyError as exc:
        raise _err(exc) from None


async def modules_put(request: web.Request) -> web.Response:
    """Turn the Mullvad exit, its multi-hop entry, or AmneziaWG on or off.

    The exit starts as soon as it is in the chain: there is no separate activate step."""
    data = await read_json(request)
    svc = _svc(request)
    store = svc.store
    cfg = store.data
    changed: list[str] = []

    if "mullvad" in data:
        on = data["mullvad"] is True
        hops = list(cfg["chain"].get("hops") or [])
        if on:
            mullvad = cfg["providers"].get("mullvad") or {}
            selected = mullvad.get("selected") or {}
            if not mullvad.get("account_sealed"):
                raise web.HTTPConflict(text="web.priv_mullvad_need_account")
            if not selected.get("hostname"):
                raise web.HTTPConflict(text="web.priv_mullvad_need_server")
            _set_exit(svc, _mullvad_hop(selected))
        else:
            if any(h.get("provider") == "mullvad" for h in hops):
                _set_exit(svc, None)  # the chosen servers stay for the next time
        changed.append(f"mullvad={'on' if on else 'off'}")

    if "multi_hop" in data:
        on = data["multi_hop"] is True
        selected = (cfg["providers"].get("mullvad") or {}).get("selected")
        if not isinstance(selected, dict) or not selected.get("hostname"):
            raise web.HTTPConflict(text="web.priv_mullvad_need_server")
        if on and not selected.get("entry_hostname"):
            raise web.HTTPConflict(text="web.priv_mullvad_need_entry")
        if not on and selected.get("entry_hostname"):
            selected = {**selected, "entry_hostname": None}
            cfg["providers"]["mullvad"]["selected"] = selected
            store.save()
        if on or any(h.get("provider") == "mullvad" for h in cfg["chain"].get("hops") or []):
            _set_exit(svc, _mullvad_hop(selected))
        changed.append(f"multi_hop={'on' if on else 'off'}")

    if "amneziawg" in data:
        on = data["amneziawg"] is True
        try:
            svc.set_obfuscation({"mode": "amneziawg" if on else "off"})
        except PrivacyError as exc:
            raise _err(exc) from None
        # The switch acts: the root sync job takes awg0 down or up, and the apps stop or start
        # getting the disguised ways.
        await asyncio.to_thread(awg.set_enabled, dashboard(request).settings.data_dir, on)
        changed.append(f"amneziawg={'on' if on else 'off'}")

    if "mullvad" in data or "multi_hop" in data:
        await _apply_exit(svc)

    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.modules", ",".join(changed)
    )
    return web.json_response(await asyncio.to_thread(_with_layers, request, svc.config()))


async def custom_add(request: web.Request) -> web.Response:
    data = await read_json(request)
    confirm_password(request, str(data.get("password") or ""))
    svc = _svc(request)
    try:
        result = svc.custom.add_manual(svc.store, data)
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.custom_add", str(result.get("name") or "")
    )
    return web.json_response(result)


async def custom_import(request: web.Request) -> web.Response:
    data = await read_json(request)
    confirm_password(request, str(data.get("password") or ""))
    text = str(data.get("config") or "")
    svc = _svc(request)
    try:
        result = svc.custom.add_import(svc.store, text, data.get("name"))
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.custom_import", str(result.get("name") or "")
    )
    return web.json_response(result)


async def custom_remove(request: web.Request) -> web.Response:
    data = await read_json(request)
    confirm_password(request, str(data.get("password") or ""))
    svc = _svc(request)
    name = svc.custom.remove(svc.store, request.match_info["id"])
    if name is None:
        raise web.HTTPNotFound(text="web.not_found")
    await _apply_exit(svc)  # it may have been the exit in use
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.custom_remove", name
    )
    return web.json_response({"ok": True})


async def mullvad_account(request: web.Request) -> web.Response:
    data = await read_json(request)
    confirm_password(request, str(data.get("password") or ""))
    account = str(data.get("account") or "")
    svc = _svc(request)
    try:
        result = await svc.mullvad.register_device(account, svc.store)
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.mullvad_account", "linked"
    )
    return web.json_response(result)


async def mullvad_remove(request: web.Request) -> web.Response:
    data = await read_json(request)
    confirm_password(request, str(data.get("password") or ""))
    svc = _svc(request)
    await svc.mullvad.remove_account(svc.store)
    if any(h.get("provider") == "mullvad" for h in svc.store.data["chain"].get("hops") or []):
        _set_exit(svc, None)
    await _apply_exit(svc)
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.mullvad_remove", ""
    )
    return web.json_response({"ok": True})


async def mullvad_relays(request: web.Request) -> web.Response:
    svc = _svc(request)
    refresh = request.query.get("refresh") == "1"
    try:
        relays = await svc.mullvad.relays(svc.store.relay_cache_path(), refresh=refresh)
    except PrivacyError as exc:
        raise _err(exc) from None
    return web.json_response({"countries": relays}, headers={"Cache-Control": "no-store"})


async def mullvad_server(request: web.Request) -> web.Response:
    data = await read_json(request)
    svc = _svc(request)
    try:
        countries = await svc.mullvad.relays(svc.store.relay_cache_path())
        selected = svc.mullvad.select_server(svc.store, data, countries)
    except PrivacyError as exc:
        raise _err(exc) from None
    # Choosing a server means using it: Mullvad becomes the exit at once.
    _set_exit(svc, _mullvad_hop(selected))
    await _apply_exit(svc)
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.mullvad_server", str(selected.get("hostname"))
    )
    return web.json_response({"selected": selected})

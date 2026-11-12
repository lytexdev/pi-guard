"""Dashboard API for the optional privacy upstream (multi-hop, DNS, Tor, profiles)."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from pi_guard.privacy.errors import PrivacyError
from pi_guard.privacy.service import PrivacyService
from pi_guard.web.server import confirm_password, current, dashboard, read_json

log = logging.getLogger(__name__)


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/privacy/capabilities", capabilities),
        web.get("/api/privacy/config", config_get),
        web.get("/api/privacy/diagnostics", diagnostics),
        web.put("/api/privacy/profile", profile_put),
        web.put("/api/privacy/killswitch", killswitch_put),
        web.put("/api/privacy/dns", dns_put),
        web.put("/api/privacy/tor", tor_put),
        web.put("/api/privacy/obfuscation", obfuscation_put),
        web.get("/api/privacy/chain", chain_get),
        web.put("/api/privacy/chain", chain_put),
        web.post("/api/privacy/chain/hops", hop_add),
        web.delete("/api/privacy/chain/hops/{id}", hop_remove),
        web.post("/api/privacy/chain/reorder", chain_reorder),
        web.post("/api/privacy/activate", activate),
        web.post("/api/privacy/deactivate", deactivate),
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


async def config_get(request: web.Request) -> web.Response:
    return web.json_response(_svc(request).config(), headers={"Cache-Control": "no-store"})


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


async def profile_put(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        result = _svc(request).set_profile(str(data.get("profile") or ""))
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.profile", str(data.get("profile"))
    )
    return web.json_response(result)


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


async def tor_put(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        result = _svc(request).set_tor(data)
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.tor", str(data.get("mode"))
    )
    return web.json_response(result)


async def obfuscation_put(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        result = _svc(request).set_obfuscation(data)
    except PrivacyError as exc:
        raise _err(exc) from None
    return web.json_response(result)


async def chain_get(request: web.Request) -> web.Response:
    cfg = _svc(request).config()
    return web.json_response(cfg["chain"])


async def chain_put(request: web.Request) -> web.Response:
    data = await read_json(request)
    hops = data.get("hops")
    if not isinstance(hops, list):
        raise web.HTTPBadRequest(text="web.priv_chain_bad")
    try:
        result = _svc(request).set_chain(hops)
    except PrivacyError as exc:
        raise _err(exc) from None
    return web.json_response({"hops": result})


async def hop_add(request: web.Request) -> web.Response:
    data = await read_json(request)
    try:
        hop = _svc(request).add_hop(data)
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "privacy.hop_add", str(hop.get("provider") or "")
    )
    return web.json_response(hop)


async def hop_remove(request: web.Request) -> web.Response:
    hop_id = request.match_info["id"]
    try:
        _svc(request).remove_hop(hop_id)
    except PrivacyError as exc:
        raise _err(exc) from None
    return web.json_response({"ok": True})


async def chain_reorder(request: web.Request) -> web.Response:
    data = await read_json(request)
    order = data.get("order")
    if not isinstance(order, list):
        raise web.HTTPBadRequest(text="web.priv_chain_order")
    try:
        hops = _svc(request).reorder_hops([str(x) for x in order])
    except PrivacyError as exc:
        raise _err(exc) from None
    return web.json_response({"hops": hops})


async def activate(request: web.Request) -> web.Response:
    try:
        result = await asyncio.to_thread(_svc(request).activate)
    except PrivacyError as exc:
        raise _err(exc) from None
    dashboard(request).app.db.audit(f"web:{current(request).username}", "privacy.activate", "")
    return web.json_response(result)


async def deactivate(request: web.Request) -> web.Response:
    result = await asyncio.to_thread(_svc(request).deactivate)
    dashboard(request).app.db.audit(f"web:{current(request).username}", "privacy.deactivate", "")
    return web.json_response(result)


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
        # Keep a mullvad hop in the chain in sync with selection.
        hops = [h for h in svc.store.data["chain"]["hops"] if h.get("provider") != "mullvad"]
        names = [selected.get("entry_hostname"), selected.get("hostname")]
        hops.append(
            {
                "provider": "mullvad",
                "name": " → ".join(filter(None, names)),
                "params": selected,
            }
        )
        svc.set_chain(hops)
    except PrivacyError as exc:
        raise _err(exc) from None
    return web.json_response({"selected": selected})

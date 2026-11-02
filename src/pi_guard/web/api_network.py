"""Network page: Pi-hole, WireGuard and the devices in the LAN."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import yaml
from aiohttp import web

from pi_guard.config import OVERRIDE_YAML
from pi_guard.config.files import FileConfig, TrustedDevice
from pi_guard.network.lan import merge, neighbour_table, reverse_names
from pi_guard.network.pihole import PiholeError
from pi_guard.network.wgpeers import SERVER_ADDRESS, PeerError, PeerStore, Server
from pi_guard.network.wireguard import read_status
from pi_guard.security.files import atomic_write
from pi_guard.web.server import current, dashboard, read_json

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/network", overview),
        web.post("/api/network/pihole/blocking", blocking),
        web.post("/api/network/trust", trust_device),
        web.post("/api/network/wireguard/peers", wg_add),
        web.get("/api/network/wireguard/peers/{id:\\d+}", wg_show),
        web.get("/api/network/wireguard/peers/{id:\\d+}/file", wg_file),
        web.delete("/api/network/wireguard/peers/{id:\\d+}", wg_remove),
    ]


async def overview(request: web.Request) -> web.Response:
    app = dashboard(request).app
    s = app.settings
    pihole: dict[str, Any] | None = None
    pihole_devices = []
    if app.pihole is not None:
        try:
            summary, top, pihole_devices = await asyncio.gather(
                app.pihole.summary(), app.pihole.top_blocked(8), app.pihole.devices()
            )
            pihole = {**summary, "top_blocked": top, "url": s.pihole_url}
        except PiholeError as exc:
            pihole = {"error": str(exc), "url": s.pihole_url}
    try:
        neighbours = await neighbour_table(app.config.file.presence.interface)
    except (OSError, TimeoutError):
        neighbours = {}
    trusted = {d.mac_address: d.name for d in app.config.file.presence.devices}
    ips = [ip for _, (ip, _) in neighbours.items()]
    names = await reverse_names(ips)
    devices = merge(neighbours, pihole_devices, trusted, names)
    wireguard = read_status(s.runtime_dir / "wireguard.status") if s.wireguard_enabled else None
    store = dashboard(request).wg_peers
    if wireguard is not None and store is not None:
        stored = {p["public_key"]: p for p in store.list()}
        for peer in wireguard["peers"]:
            known = stored.get(peer["public_key"])
            if known:
                peer.update(name=known["name"], id=known["id"], full_tunnel=known["full_tunnel"])
        # Devices added a moment ago, before the root job loaded them.
        loaded = {p["public_key"] for p in wireguard["peers"]}
        wireguard["peers"] += [
            {**p, "pending": True, "active": False, "latest_handshake": None, "received": 0,
             "sent": 0} for p in stored.values() if p["public_key"] not in loaded
        ]  # fmt: skip
    return web.json_response({
        "pihole": pihole,
        "pihole_enabled": app.pihole is not None,
        "wireguard_enabled": s.wireguard_enabled,
        "wireguard": wireguard,
        "wireguard_endpoint": s.wireguard_endpoint,
        "wireguard_home_network": s.wireguard_home_network,
        "devices": [d.as_dict() for d in devices],
    })  # fmt: skip


async def blocking(request: web.Request) -> web.Response:
    app = dashboard(request).app
    if app.pihole is None:
        raise web.HTTPConflict(text="Pi-hole is not set up")
    data = await read_json(request)
    enabled = data.get("enabled") is True
    minutes = int(data.get("minutes") or 0) or None
    try:
        await app.pihole.set_blocking(enabled, minutes)
    except PiholeError as exc:
        raise web.HTTPBadGateway(text=str(exc)) from None
    app.db.audit(f"web:{current(request).username}", "pihole.blocking",
                 f"{'on' if enabled else 'off'} {minutes or ''}".strip())  # fmt: skip
    return web.json_response({"ok": True})


async def trust_device(request: web.Request) -> web.Response:
    """Add a LAN device to presence.devices (written to dashboard.yaml)."""
    d = dashboard(request)
    data = await read_json(request)
    mac = str(data.get("mac", "")).lower()
    name = str(data.get("name", "")).strip()[:40]
    if not _MAC_RE.match(mac) or not name:
        raise web.HTTPBadRequest(text="name and MAC address required")
    config = d.app.config.file.model_dump(mode="json")
    if any(dev["mac_address"] == mac for dev in config["presence"]["devices"]):
        raise web.HTTPConflict(text="already trusted")
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:28] or "device"
    taken = {dev["id"] for dev in config["presence"]["devices"]}
    device_id, n = slug, 2
    while device_id in taken:
        device_id, n = f"{slug}_{n}", n + 1
    config["presence"]["devices"].append(
        TrustedDevice(id=device_id, name=name, mac_address=mac).model_dump(mode="json")
    )
    validated = FileConfig.model_validate(config)
    text = "# Written by the pi-guard dashboard; replaces config.yaml while it exists.\n"
    text += yaml.safe_dump(validated.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    await asyncio.to_thread(atomic_write, d.settings.data_dir / OVERRIDE_YAML, text.encode())
    d.app.db.audit(f"web:{current(request).username}", "presence.trust", device_id)
    return web.json_response({"ok": True, "restart_required": True})


# --- WireGuard devices ---------------------------------------------------------------


def _store(request: web.Request) -> PeerStore:
    store = dashboard(request).wg_peers
    if store is None:
        raise web.HTTPConflict(text="web.wireguard_off")
    return store


def _server(request: web.Request) -> Server:
    s = dashboard(request).settings
    status = read_status(s.runtime_dir / "wireguard.status") or {}
    public_key = (status.get("interface") or {}).get("public_key")
    if not public_key:
        raise web.HTTPConflict(text="web.wireguard_no_status")
    if not s.wireguard_endpoint:
        raise web.HTTPConflict(text="web.wg_no_endpoint")
    port = (status.get("interface") or {}).get("listen_port")
    endpoint = s.wireguard_endpoint
    if port and port != "51820" and ":" not in endpoint:
        endpoint = f"{endpoint}:{port}"
    dns = str(SERVER_ADDRESS) if s.pihole_enabled else None
    return Server(public_key, endpoint, dns, s.wireguard_home_network)


def _peer_id(request: web.Request) -> int:
    return int(request.match_info["id"])


def _config(request: web.Request, peer_id: int) -> tuple[str, str]:
    store = _store(request)
    name = store.name(peer_id)
    if name is None:
        raise web.HTTPNotFound(text="web.not_found")
    try:
        config = store.client_config(peer_id, _server(request))
    except PeerError as exc:
        raise web.HTTPConflict(text=str(exc)) from None
    assert config is not None
    return name, config


def _qr(text: str) -> str:
    from pi_guard.web.api_account import _qr_svg

    return _qr_svg(text)


async def wg_add(request: web.Request) -> web.Response:
    store = _store(request)
    _server(request)  # refuse early if phones could not connect anyway
    data = await read_json(request)
    try:
        peer_id = await asyncio.to_thread(
            store.add, str(data.get("name", "")), data.get("full_tunnel") is not False
        )
    except PeerError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from None
    name, config = _config(request, peer_id)
    dashboard(request).app.db.audit(f"web:{current(request).username}", "wireguard.add", name)
    return web.json_response({"id": peer_id, "name": name, "qr": _qr(config)})


async def wg_show(request: web.Request) -> web.Response:
    name, config = _config(request, _peer_id(request))
    dashboard(request).app.db.audit(f"web:{current(request).username}", "wireguard.show", name)
    return web.json_response({"id": _peer_id(request), "name": name, "qr": _qr(config)})


async def wg_file(request: web.Request) -> web.Response:
    name, config = _config(request, _peer_id(request))
    dashboard(request).app.db.audit(f"web:{current(request).username}", "wireguard.file", name)
    filename = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:24] or "device"
    return web.Response(
        text=config,
        content_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.conf"',
            "Cache-Control": "no-store",
        },
    )


async def wg_remove(request: web.Request) -> web.Response:
    name = await asyncio.to_thread(_store(request).remove, _peer_id(request))
    if name is None:
        raise web.HTTPNotFound(text="web.not_found")
    dashboard(request).app.db.audit(f"web:{current(request).username}", "wireguard.remove", name)
    return web.json_response({"ok": True})

"""Network page: Pi-hole, WireGuard and the devices in the LAN."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import yaml
from aiohttp import web

from pi_guard.config import OVERRIDE_YAML
from pi_guard.config.files import FileConfig, TrustedDevice
from pi_guard.network.blocklists import MAX_FILE_BYTES, PRESETS, parse_domains, valid_list_address
from pi_guard.network.inspect import inspect_device
from pi_guard.network.lan import merge, neighbour_table, reverse_names
from pi_guard.network.pihole import PiholeError
from pi_guard.network.wgpeers import SERVER_ADDRESS, PeerError, PeerStore, Server
from pi_guard.network.wireguard import read_status
from pi_guard.security.files import atomic_write
from pi_guard.web.server import current, dashboard, read_json

log = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/network", overview),
        web.post("/api/network/pihole/blocking", blocking),
        web.post("/api/network/trust", trust_device),
        web.get("/api/network/device/{ip}", device_details),
        web.get("/api/network/blocklists", blocklists),
        web.post("/api/network/blocklists", blocklist_add),
        web.put("/api/network/blocklists", blocklist_toggle),
        web.delete("/api/network/blocklists", blocklist_delete),
        web.post("/api/network/blocklists/upload", blocklist_upload),
        web.post("/api/network/blocklists/update", blocklist_update),
        web.get("/api/topology", topology),
        web.post("/api/network/wireguard/peers", wg_add),
        web.get("/api/network/wireguard/peers/{id:\\d+}", wg_show),
        web.get("/api/network/wireguard/peers/{id:\\d+}/file", wg_file),
        web.delete("/api/network/wireguard/peers/{id:\\d+}", wg_remove),
    ]


def _internal(ips: list[str]) -> bool:
    """Containers on this Pi (Docker uses 172.16.0.0/12) are no devices of the home network."""
    docker = ipaddress.ip_network("172.16.0.0/12")
    return bool(ips) and all(ipaddress.ip_address(ip) in docker for ip in ips if ":" not in ip)


def _browser_url(request: web.Request, url: str) -> str:
    """Pi-hole's address as the browser reaches it: 127.0.0.1 means this Pi, not the phone."""
    parts = urlsplit(url)
    if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        return url
    port = f":{parts.port}" if parts.port else ""
    host = urlsplit(f"//{request.host}").hostname or "localhost"
    return f"{parts.scheme}://{host}{port}{parts.path}"


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
            pihole = {**summary, "top_blocked": top, "url": _browser_url(request, s.pihole_url)}
        except PiholeError as exc:
            pihole = {"error": str(exc), "url": _browser_url(request, s.pihole_url)}
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
        "devices": [_with_details(app, d.as_dict()) for d in devices],
    })  # fmt: skip


DETAILS_PREFIX = "lan.details."


def _with_details(app: Any, device: dict[str, Any]) -> dict[str, Any]:
    known = app.db.get(DETAILS_PREFIX + device["mac"])
    return {**device, "details": known} if known else device


async def device_details(request: web.Request) -> web.Response:
    """Look closer at one device (ping, common ports, web page). Only on request."""
    d = dashboard(request)
    ip = request.match_info["ip"]
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        raise web.HTTPBadRequest(text="analytics.bad_ip") from None
    if not address.is_private:  # the home network only, nothing on the internet
        raise web.HTTPBadRequest(text="analytics.bad_ip")
    mac = request.query.get("mac", "").lower()
    report = await inspect_device(ip, mac if _MAC_RE.match(mac) else None,
                                  request.query.get("vendor"))  # fmt: skip
    if _MAC_RE.match(mac):
        d.app.db.set(DETAILS_PREFIX + mac, report)
    d.app.db.audit(f"web:{current(request).username}", "network.inspect", ip)
    return web.json_response(report)


# --- block lists --------------------------------------------------------------------------


def _pihole(request: web.Request) -> Any:
    pihole = dashboard(request).app.pihole
    if pihole is None:
        raise web.HTTPConflict(text="web.pihole_off")
    return pihole


async def blocklists(request: web.Request) -> web.Response:
    try:
        lists = await _pihole(request).block_lists()
    except PiholeError as exc:
        raise web.HTTPBadGateway(text=str(exc)) from None
    gravity = dashboard(request).gravity_state
    return web.json_response({"lists": lists, "presets": PRESETS, "updating": gravity})


async def _list_call(request: web.Request, action: str, call: Any) -> web.Response:
    try:
        await call
    except PiholeError as exc:
        raise web.HTTPBadGateway(text=str(exc)) from None
    dashboard(request).app.db.audit(f"web:{current(request).username}", action)
    return web.json_response({"ok": True})


async def blocklist_add(request: web.Request) -> web.Response:
    data = await read_json(request)
    address, comment = str(data.get("address", "")).strip(), str(data.get("comment", ""))[:100]
    if not valid_list_address(address):
        raise web.HTTPBadRequest(text="web.bl_bad_address")
    return await _list_call(request, "pihole.list_add",
                            _pihole(request).add_block_list(address, comment))  # fmt: skip


async def blocklist_toggle(request: web.Request) -> web.Response:
    data = await read_json(request)
    address, comment = str(data.get("address", "")), str(data.get("comment", ""))[:100]
    enabled = data.get("enabled") is True
    return await _list_call(request, "pihole.list_toggle",
                            _pihole(request).set_block_list(address, enabled, comment))  # fmt: skip


async def blocklist_delete(request: web.Request) -> web.Response:
    address = str((await read_json(request)).get("address", ""))
    return await _list_call(request, "pihole.list_delete",
                            _pihole(request).delete_block_list(address))  # fmt: skip


async def blocklist_upload(request: web.Request) -> web.Response:
    """A .txt file: its domains are blocked one by one, there is no list to download."""
    pihole = _pihole(request)
    reader = await request.multipart()
    text = ""
    name = "upload"
    async for part in reader:
        if getattr(part, "filename", None):
            name = str(part.filename)[:60]  # type: ignore[union-attr]
            data = await part.read(decode=False)  # type: ignore[union-attr]
            if len(data) > MAX_FILE_BYTES:
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE_BYTES, actual_size=len(data))
            text = data.decode("utf-8", errors="replace")
    domains, bad = parse_domains(text)
    if not domains:
        raise web.HTTPBadRequest(text="web.bl_no_domains")
    try:
        added = await pihole.deny_domains(domains, f"pi-guard: {name}")
    except PiholeError as exc:
        raise web.HTTPBadGateway(text=str(exc)) from None
    dashboard(request).app.db.audit(
        f"web:{current(request).username}", "pihole.domains", str(added)
    )
    return web.json_response({"added": added, "skipped": bad})


async def blocklist_update(request: web.Request) -> web.Response:
    d = dashboard(request)
    pihole = _pihole(request)
    if d.gravity_state:
        return web.json_response({"ok": True, "running": True})

    async def run() -> None:
        d.gravity_state = True
        try:
            await pihole.update_gravity()
        except PiholeError as exc:
            log.warning("block list update failed: %s", exc)
        finally:
            d.gravity_state = False

    d.background(run())
    return web.json_response({"ok": True, "running": True})


async def topology(request: web.Request) -> web.Response:
    """What the interactive map shows: Pi, services, VPN devices, cameras and the LAN."""
    d = dashboard(request)
    app, s = d.app, d.settings
    try:
        neighbours = await neighbour_table(app.config.file.presence.interface)
    except (OSError, TimeoutError):
        neighbours = {}
    pihole_devices = []
    if app.pihole is not None:
        with contextlib.suppress(PiholeError):
            pihole_devices = await app.pihole.devices()
    trusted = {x.mac_address: x.name for x in app.config.file.presence.devices}
    lan = [d for d in merge(neighbours, pihole_devices, trusted, {}) if not _internal(d.ips)]
    status = read_status(s.runtime_dir / "wireguard.status") if s.wireguard_enabled else None
    peers = list((status or {}).get("peers", []))
    store = d.wg_peers
    if store is not None:
        names = {p["public_key"]: p["name"] for p in store.list()}
        peers = [{**p, "name": names.get(p["public_key"], p.get("name"))} for p in peers]
    cameras = []
    for camera in app.config.file.cameras:
        pipeline = app.cameras.get(camera.id)
        if camera.enabled:
            state = pipeline.status.state.value if pipeline else "offline"
            cameras.append({"id": camera.id, "name": camera.name, "room": camera.room,
                            "source": camera.source, "state": state})  # fmt: skip
    active = ("REACHABLE", "DELAY", "PROBE")
    devices = [{
        "name": x.trusted or x.name or (x.ips[0] if x.ips else x.mac),
        "ip": x.ips[0] if x.ips else None, "mac": x.mac, "vendor": x.vendor,
        "active": x.state in active, "trusted": bool(x.trusted),
        "kind": (app.db.get(DETAILS_PREFIX + x.mac) or {}).get("kind"),
    } for x in lan[:40]]  # fmt: skip
    return web.json_response({
        "services": {"pihole": app.pihole is not None, "wireguard": s.wireguard_enabled,
                     "mqtt": bool(s.mqtt_host)},
        "cameras": cameras,
        "vpn": [{"name": p.get("name") or p["public_key"][:8], "active": p.get("active", False),
                 "address": p.get("allowed_ips", "").split("/")[0]} for p in peers],
        "lan": devices,
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

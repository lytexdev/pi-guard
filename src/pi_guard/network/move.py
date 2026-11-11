"""Moving home, a new router, a new internet provider: what changed, and what to do about it.

The Pi remembers the network it calls home: the router's address and hardware address, its
own addresses, and the Wi-Fi it uses. Comparing that with the network it sees now tells
whether it was moved or the router was replaced. The checks then say, one by one, what
needs a hand: cameras on the network, the FRITZ!Box, the router handing out Pi-hole as DNS,
the address phones use for the VPN and its port forwarding, blocks that name IP addresses.

Nothing here changes the Pi's own network settings: that needs root and can cut the Pi
off, so the dashboard gives the command and the owner runs it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

HOME_KEY = "move.home"
TIMEOUT = 5


@dataclass
class Network:
    gateway: str | None = None
    gateway_mac: str | None = None
    interface: str | None = None
    addresses: list[str] = field(default_factory=list)  # IPv4 with prefix, e.g. 192.168.1.5/24
    mac: str | None = None
    ssid: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def contains(self, ip: str) -> bool:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in ipaddress.ip_interface(a).network for a in self.addresses)


async def _run(*args: str) -> str:
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL)  # fmt: skip
        out, _ = await asyncio.wait_for(process.communicate(), TIMEOUT)
    except (OSError, TimeoutError):
        return ""
    return out.decode(errors="replace") if process.returncode == 0 else ""


def _json(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


async def current() -> Network:
    """The network as the Pi sees it now."""
    route = next(iter(_json(await _run("ip", "-j", "route", "show", "default"))), {})
    net = Network(gateway=route.get("gateway"), interface=route.get("dev"))
    for link in _json(await _run("ip", "-j", "addr", "show")):
        if net.interface and link.get("ifname") != net.interface:
            continue
        net.mac = net.mac or link.get("address")
        net.addresses += [f"{a['local']}/{a['prefixlen']}" for a in link.get("addr_info", [])
                          if a.get("family") == "inet" and a.get("scope") == "global"]  # fmt: skip
    if net.gateway:
        for neighbour in _json(await _run("ip", "-j", "neigh", "show", net.gateway)):
            net.gateway_mac = neighbour.get("lladdr") or net.gateway_mac
    if net.interface and net.interface.startswith(("wl", "wlan")):
        net.ssid = (await _run("iwgetid", "-r")).strip() or None
    return net


def changes(home: dict[str, Any] | None, now: Network) -> list[str]:
    """What is different from home: router (another device), network (other addresses),
    wifi (another Wi-Fi name). Empty if nothing, or if there is no home yet."""
    if not home:
        return []
    found = []
    if home.get("gateway_mac") and now.gateway_mac and home["gateway_mac"] != now.gateway_mac:
        found.append("router")
    old_nets = {str(ipaddress.ip_interface(a).network) for a in home.get("addresses", [])}
    new_nets = {str(ipaddress.ip_interface(a).network) for a in now.addresses}
    other_net = bool(old_nets and new_nets and not old_nets & new_nets)
    if other_net or (now.gateway and home.get("gateway") != now.gateway):
        found.append("network")
    if home.get("ssid") and now.ssid and home["ssid"] != now.ssid:
        found.append("wifi")
    return found


def _host(url: str) -> str | None:
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _private(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_private
    except ValueError:
        return False


def _is_ip(text: str | None) -> bool:
    try:
        ipaddress.ip_address((text or "").rsplit(":", 1)[0] if text and text.count(":") == 1
                             else (text or ""))  # fmt: skip
        return True
    except ValueError:
        return False


def check(name: str, state: str, **values: Any) -> dict[str, Any]:
    """state: ok, todo (needs a hand), info (to know), off (not used here)."""
    return {"id": name, "state": state, **values}


def cameras(app: Any, net: Network) -> dict[str, Any]:
    found = []
    for camera in app.config.file.cameras:
        if camera.source not in ("rtsp", "http"):
            continue
        host = _host(camera.device)
        pipeline = app.cameras.get(camera.id)
        online = bool(getattr(pipeline, "online", True))
        moved = _private(host) and host is not None and not net.contains(host)
        if moved or not online:
            found.append({"name": camera.name, "host": host or "", "outside": moved})
    if not any(c.source in ("rtsp", "http") for c in app.config.file.cameras):
        return check("cameras", "off")
    return check("cameras", "todo" if found else "ok", cameras=found)


async def router(app: Any, net: Network) -> dict[str, Any]:
    box = app.config.file.presence.fritzbox
    if app.fritz is None:
        return check("router", "off")
    try:
        await asyncio.wait_for(app.fritz.hosts(), TIMEOUT * 2)
    except Exception:  # any answer but the list means the box is not the one set up
        return check("router", "todo", host=box.host, gateway=net.gateway or "")
    return check("router", "ok", host=box.host)


async def dns(app: Any, net: Network) -> dict[str, Any]:
    """Pi-hole helps only if the router hands it out: then other devices ask it."""
    if app.pihole is None:
        return check("dns", "off")
    own = [a.split("/")[0] for a in net.addresses]
    try:
        clients = [d for d in await app.pihole.devices() if d.queries and not set(d.ips) & set(own)]
    except Exception:
        return check("dns", "todo", pi=own[0] if own else "", reachable=False)
    return check("dns", "ok" if clients else "todo", pi=own[0] if own else "",
                 clients=len(clients), reachable=True)  # fmt: skip


def vpn(app: Any, public: str | None, net: Network) -> dict[str, Any]:
    s = app.settings
    if not s.wireguard_enabled:
        return check("vpn", "off")
    endpoint = s.wireguard_endpoint or ""
    from pi_guard.network.wireguard import read_status, status_file

    status = read_status(status_file(s, "wireguard.status")) or {}
    port = (status.get("interface") or {}).get("listen_port") or 51820
    own = net.addresses[0].split("/")[0] if net.addresses else ""
    literal = _is_ip(endpoint)
    host = endpoint.rsplit(":", 1)[0] if endpoint.count(":") == 1 else endpoint
    if not endpoint:
        return check(
            "vpn", "todo", endpoint="", public=public or "", port=port, pi=own, kind="none"
        )
    if literal and public and host != public:
        return check("vpn", "todo", endpoint=endpoint, public=public, port=port, pi=own, kind="ip")
    return check("vpn", "info", endpoint=endpoint, public=public or "", port=port, pi=own,
                 kind="ip" if literal else "name")  # fmt: skip


def blocks(app: Any) -> dict[str, Any]:
    """Blocks for one device name it by its IP address, which a new router may give away."""
    try:
        count = app.db.execute(
            "SELECT COUNT(*) FROM net_blocks WHERE device IS NOT NULL"
        ).fetchone()
    except Exception:
        return check("blocks", "off")
    return check("blocks", "info" if count and count[0] else "off", count=count[0] if count else 0)


def backup(app: Any) -> dict[str, Any]:
    last = app.db.get("backup.last") or {}
    target = app.settings.backup_target if app.settings.backup_enabled else ""
    return check("backup", "ok" if last.get("ok") else "todo", last=last.get("time"), target=target)


def wifi(net: Network) -> dict[str, Any]:
    if not net.ssid:
        return check("wifi", "info" if net.interface else "off", cable=bool(net.interface))
    return check("wifi", "info", ssid=net.ssid)


async def report(app: Any, public: str | None) -> dict[str, Any]:
    net = await current()
    home = app.db.get(HOME_KEY)
    if home is None and net.gateway:  # the first look: this is home
        app.db.set(HOME_KEY, net.as_dict())
        home = net.as_dict()
    checks = [
        backup(app), wifi(net), cameras(app, net), await router(app, net), await dns(app, net),
        vpn(app, public, net), blocks(app),
    ]  # fmt: skip
    return {"network": net.as_dict(), "home": home, "changes": changes(home, net),
            "checks": [c for c in checks if c["state"] != "off"]}  # fmt: skip

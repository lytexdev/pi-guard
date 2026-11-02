"""Devices in the local network, merged from the neighbour table and Pi-hole.

Only data the device already has is shown: the kernel's ARP/NDP table and,
if configured, Pi-hole's client list. Nothing is scanned.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from pi_guard.network.pihole import PiholeDevice
from pi_guard.presence.sources import parse_neighbours


@dataclass
class LanDevice:
    mac: str
    ips: list[str] = field(default_factory=list)
    name: str | None = None
    vendor: str | None = None
    state: str | None = None
    last_query: str | None = None
    queries: int | None = None
    trusted: str | None = None  # name of the matching trusted device
    randomized: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def neighbour_table(interface: str | None = None) -> dict[str, tuple[str, set[str]]]:
    args = ["ip", "-j", "neigh", "show"] + (["dev", interface] if interface else [])
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await asyncio.wait_for(process.communicate(), 10)
    return parse_neighbours(out.decode())


def _reverse(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return None


async def reverse_names(ips: list[str], limit: float = 2.0) -> dict[str, str]:
    async def lookup(ip: str) -> tuple[str, str | None]:
        try:
            return ip, await asyncio.wait_for(asyncio.to_thread(_reverse, ip), limit)
        except TimeoutError:
            return ip, None

    results = await asyncio.gather(*(lookup(ip) for ip in ips[:64]))
    return {ip: name for ip, name in results if name}


def merge(
    neighbours: dict[str, tuple[str, set[str]]],
    pihole: list[PiholeDevice],
    trusted: dict[str, str],
    names: dict[str, str],
) -> list[LanDevice]:
    devices: dict[str, LanDevice] = {}
    for mac, (ip, states) in neighbours.items():
        devices[mac] = LanDevice(mac, [ip], state=sorted(states)[0] if states else None)
    for p in pihole:
        device = devices.setdefault(p.mac, LanDevice(p.mac))
        device.ips = sorted(set(device.ips) | set(p.ips))
        device.name, device.vendor, device.queries = p.name, p.vendor, p.queries
        device.last_query = p.last_query.isoformat() if p.last_query else None
    for device in devices.values():
        device.name = device.name or next((names[ip] for ip in device.ips if ip in names), None)
        device.trusted = trusted.get(device.mac)
        device.randomized = bool(int(device.mac[:2], 16) & 0x02) if len(device.mac) >= 2 else False
    order = {"REACHABLE": 0, "DELAY": 1, "PROBE": 1, "STALE": 2}

    def key(d: LanDevice) -> tuple[bool, int, float]:
        last = datetime.fromisoformat(d.last_query).timestamp() if d.last_query else 0.0
        return (d.trusted is None, order.get(d.state or "", 3), -last)

    return sorted(devices.values(), key=key)

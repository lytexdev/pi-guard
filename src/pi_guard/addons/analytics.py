"""Network analysis from Pi-hole's data: who asks for which domains, and how often.

Everything comes from DNS, so this shows domain names and devices, not pages or
content. Devices that do not use Pi-hole as DNS server, or that use encrypted DNS
of their own, do not appear. It stays on this device and is for admins only.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections import Counter
from typing import Any

from pi_guard.network.pihole import Pihole

BLOCKED_STATUS = ("GRAVITY", "DENYLIST", "REGEX", "BLOCKED", "BLACKLIST")
HIDDEN_SUFFIXES = (".in-addr.arpa", ".ip6.arpa", ".local", ".lan", ".fritz.box")
DEVICE_QUERIES = 1000


def _visible(domain: str) -> bool:
    return bool(domain) and not domain.endswith(HIDDEN_SUFFIXES)


def base_domain(domain: str) -> str:
    """mail.proton.me -> proton.me; groups a service's many host names (good enough)."""
    parts = domain.split(".")
    if len(parts) > 2 and len(parts[-2]) <= 3 and len(parts[-1]) == 2:  # example.co.uk
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def group_domains(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    totals: Counter[str] = Counter()
    for row in rows:
        if _visible(row["domain"]):
            totals[base_domain(row["domain"])] += int(row["count"])
    return [{"domain": d, "count": n} for d, n in totals.most_common(count)]


class NetworkAnalytics:
    def __init__(self, pihole: Pihole) -> None:
        self._pihole = pihole

    async def _names(self, extra: dict[str, str]) -> dict[str, dict[str, str | None]]:
        names: dict[str, dict[str, str | None]] = {}
        for device in await self._pihole.devices():
            for ip in device.ips:
                names[ip] = {"name": device.name, "vendor": device.vendor}
        for ip, name in extra.items():
            names.setdefault(ip, {"name": None, "vendor": None})["name"] = name
        return names

    async def overview(self, known_names: dict[str, str] | None = None) -> dict[str, Any]:
        request = self._pihole.request
        summary, top, blocked, clients, types, upstreams, history, names = await asyncio.gather(
            self._pihole.summary(),
            request("GET", "/api/stats/top_domains", params={"count": 200}),
            request("GET", "/api/stats/top_domains", params={"count": 200, "blocked": "true"}),
            request("GET", "/api/stats/top_clients", params={"count": 30}),
            request("GET", "/api/stats/query_types"),
            request("GET", "/api/stats/upstreams"),
            request("GET", "/api/history"),
            self._names(known_names or {}),
        )
        devices = []
        for client in clients.get("clients", []):
            ip = client.get("ip", "")
            known = names.get(ip, {})
            devices.append({
                "ip": ip, "name": known.get("name") or client.get("name") or None,
                "vendor": known.get("vendor"), "count": client.get("count", 0),
            })  # fmt: skip
        return {
            "summary": summary,
            "top_domains": group_domains(top.get("domains", []), 15),
            "top_blocked": group_domains(blocked.get("domains", []), 15),
            "devices": devices,
            "query_types": {k: v for k, v in types.get("types", {}).items() if v},
            "upstreams": [
                {"name": u.get("name") or u.get("ip"), "count": u.get("count", 0)}
                for u in upstreams.get("upstreams", [])
                if u.get("port", -1) > 0
            ],
            "history": [
                {"time": h["timestamp"], "total": h["total"], "blocked": h["blocked"]}
                for h in history.get("history", [])
            ],
        }

    async def device(self, ip: str) -> dict[str, Any]:
        ipaddress.ip_address(ip)  # ValueError for anything else
        data = await self._pihole.request(
            "GET", "/api/queries", params={"client_ip": ip, "length": DEVICE_QUERIES}
        )
        queries = data.get("queries", [])
        allowed: Counter[str] = Counter()
        blocked: Counter[str] = Counter()
        for query in queries:
            domain = query.get("domain") or ""
            if not _visible(domain):
                continue
            status = str(query.get("status") or "")
            target = blocked if any(s in status for s in BLOCKED_STATUS) else allowed
            target[base_domain(domain)] += 1
        return {
            "ip": ip,
            "queries": len(queries),
            "last": max((q.get("time", 0) for q in queries), default=None),
            "top_domains": [{"domain": d, "count": n} for d, n in allowed.most_common(20)],
            "top_blocked": [{"domain": d, "count": n} for d, n in blocked.most_common(10)],
        }

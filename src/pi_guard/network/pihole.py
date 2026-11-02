"""Client for the Pi-hole v6 REST API.

pi-guard logs in with an app password (Pi-hole settings → Web interface/API →
"Configure app password"), keeps the session and logs in again when it expires.
Pi-hole v5's old api.php is not supported.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)


class PiholeError(Exception):
    pass


@dataclass(frozen=True)
class PiholeDevice:
    mac: str
    ips: tuple[str, ...]
    name: str | None
    vendor: str | None
    last_query: datetime | None
    queries: int


class Pihole:
    def __init__(self, url: str, password: str | None, verify: bool = False) -> None:
        # Pi-hole usually runs on the same box with a self-signed certificate.
        self._client = httpx.AsyncClient(base_url=url.rstrip("/"), timeout=10, verify=verify)
        self._password = password or ""
        self._sid: str | None = None

    async def _login(self) -> None:
        try:
            response = await self._client.post("/api/auth", json={"password": self._password})
        except httpx.HTTPError as exc:
            raise PiholeError(f"Pi-hole not reachable: {type(exc).__name__}") from exc
        if response.status_code == 401:
            raise PiholeError("Pi-hole rejected the password")
        if response.status_code >= 400:
            raise PiholeError(f"Pi-hole login answered {response.status_code}")
        session = response.json().get("session", {})
        if not session.get("valid"):
            raise PiholeError("Pi-hole login failed")
        self._sid = session.get("sid")  # None when Pi-hole has no password

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(2):
            if self._sid is None and attempt == 0:
                await self._login()
            headers = {"X-FTL-SID": self._sid} if self._sid else {}
            try:
                response = await self._client.request(method, path, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise PiholeError(f"Pi-hole not reachable: {type(exc).__name__}") from exc
            if response.status_code == 401 and attempt == 0:
                self._sid = None
                await self._login()
                continue
            if response.status_code >= 400:
                raise PiholeError(f"Pi-hole answered {response.status_code}")
            return response.json() if response.content else {}
        raise PiholeError("Pi-hole login expired")

    async def summary(self) -> dict[str, Any]:
        data = await self.request("GET", "/api/stats/summary")
        queries, gravity = data.get("queries", {}), data.get("gravity", {})
        blocking = await self.request("GET", "/api/dns/blocking")
        return {
            "queries": queries.get("total", 0),
            "blocked": queries.get("blocked", 0),
            "percent_blocked": round(queries.get("percent_blocked", 0.0), 1),
            "domains_blocked": gravity.get("domains_being_blocked", 0),
            "clients": data.get("clients", {}).get("active", 0),
            "blocking": blocking.get("blocking") == "enabled",
            "blocking_timer": blocking.get("timer"),
        }

    async def top_blocked(self, count: int = 10) -> list[dict[str, Any]]:
        data = await self.request("GET", "/api/stats/top_domains",
                                  params={"blocked": "true", "count": count})  # fmt: skip
        return [{"domain": d["domain"], "count": d["count"]} for d in data.get("domains", [])]

    async def set_blocking(self, enabled: bool, minutes: int | None = None) -> None:
        body: dict[str, Any] = {"blocking": enabled}
        if not enabled and minutes:
            body["timer"] = minutes * 60  # blocking switches itself back on
        await self.request("POST", "/api/dns/blocking", json=body)

    async def devices(self) -> list[PiholeDevice]:
        data = await self.request("GET", "/api/network/devices",
                                  params={"max_devices": 200, "max_addresses": 5})  # fmt: skip
        devices = []
        for d in data.get("devices", []):
            mac = str(d.get("hwaddr", "")).lower()
            if not mac or mac.startswith("ip-"):  # Pi-hole's placeholder for MAC-less clients
                continue
            ips = tuple(str(a.get("ip")) for a in d.get("ips", []) if a.get("ip"))
            names = [a.get("name") for a in d.get("ips", []) if a.get("name")]
            last = d.get("lastQuery")
            devices.append(PiholeDevice(
                mac, ips, names[0] if names else None, d.get("macVendor") or None,
                datetime.fromtimestamp(last, UTC) if last else None, int(d.get("numQueries", 0)),
            ))  # fmt: skip
        return devices

    async def close(self) -> None:
        if self._sid:
            with contextlib.suppress(httpx.HTTPError):
                await self._client.delete("/api/auth", headers={"X-FTL-SID": self._sid})
        await self._client.aclose()

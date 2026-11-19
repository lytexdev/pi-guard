"""Dynamic DNS through Cloudflare: a name that follows the home's changing public address.

Every few minutes this looks at the public IPv4 address (and, if wanted, the Pi's own global
IPv6 address) and, when it changed, sets the A (AAAA) record of the chosen name through the
Cloudflare API with an API token. The VPN and linked households can then use the name.

What leaves the Pi: the addresses, to Cloudflare, and a request to api.ipify.org for the IPv4
address. The token needs only "Zone: DNS: Edit" for that zone; it is never logged or shown.
The record is never proxied: WireGuard is UDP, Cloudflare's proxy would not pass it.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from pi_guard.core.timeutil import utcnow

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

API = "https://api.cloudflare.com/client/v4"
STATUS_KEY = "ddns.status"
EVERY_SECONDS = 300
REFRESH_SECONDS = 24 * 3600  # set the record again once a day, also when nothing changed
TTL = 1  # Cloudflare's "automatic"


class DDNSError(Exception):
    """`key` is a translation key; the message never holds the token."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key = key
        self.detail = detail


@dataclass(frozen=True)
class Wanted:
    hostname: str
    token: str
    ipv6: bool


def zone_candidates(hostname: str) -> list[str]:
    """The names the zone could have, longest first: a.home.example.org -> home.example.org,
    example.org. A zone has at least two labels."""
    labels = hostname.strip(".").split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


class CloudflareDNS:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._token = token
        self._client = client

    async def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        client = self._client or httpx.AsyncClient(timeout=15)
        try:
            response = await client.request(method, f"{API}{path}", headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise DDNSError("web.ddns_unreachable", type(exc).__name__) from None
        finally:
            if self._client is None:
                await client.aclose()
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code in (401, 403):
            raise DDNSError("web.ddns_token_refused")
        if response.status_code >= 400 or not data.get("success", False):
            errors = data.get("errors") or [{}]
            raise DDNSError(
                "web.ddns_refused", str(errors[0].get("message", response.status_code))[:120]
            )
        return data.get("result")

    async def zone_id(self, hostname: str) -> str:
        for name in zone_candidates(hostname):
            found = await self._call("GET", "/zones", params={"name": name})
            if found:
                return str(found[0]["id"])
        raise DDNSError("web.ddns_no_zone")

    async def set_record(self, zone: str, kind: str, hostname: str, address: str) -> str:
        """Point `hostname`'s A or AAAA record at `address`: "updated", "created" or "same"."""
        found = await self._call("GET", f"/zones/{zone}/dns_records",
                                 params={"type": kind, "name": hostname})  # fmt: skip
        body = {"type": kind, "name": hostname, "content": address, "ttl": TTL, "proxied": False}
        if found:
            record = found[0]
            if record.get("content") == address and not record.get("proxied"):
                return "same"
            await self._call("PATCH", f"/zones/{zone}/dns_records/{record['id']}", json=body)
            return "updated"
        await self._call("POST", f"/zones/{zone}/dns_records", json=body)
        return "created"


def wanted(settings: Any) -> Wanted | None:
    if settings.ddns_provider != "cloudflare" or not settings.ddns_hostname:
        return None
    token = settings.ddns_cloudflare_token
    secret = token.get_secret_value() if token else ""
    return Wanted(settings.ddns_hostname, secret, bool(settings.ddns_ipv6)) if secret else None


def own_ipv6() -> str | None:
    """The Pi's own global IPv6 address: with IPv6 it is reached directly, not the router."""
    from pi_guard.network.reach import own_ipv6 as addresses

    for address in addresses():
        ip = ipaddress.ip_address(address)
        if ip.version == 6 and ip.is_global:
            return str(ip)
    return None


class DDNS:
    def __init__(self, app: App) -> None:
        self._app = app
        self._last_push = float("-inf")  # monotonic; "never" must not mean "just now"
        self._lock = asyncio.Lock()

    def status(self) -> dict[str, Any]:
        return dict(self._app.db.get(STATUS_KEY) or {})

    async def run(self) -> None:
        await asyncio.sleep(30)  # after the network is up
        while True:
            if wanted(self._app.settings) is not None:
                try:
                    await self.update()
                except DDNSError as exc:
                    log.warning("dynamic DNS not updated: %s", exc.key)
            await asyncio.sleep(EVERY_SECONDS)

    async def update(self, force: bool = False) -> dict[str, Any]:
        """Set the records if an address changed (or `force`); the status either way."""
        from pi_guard.web.api_addons import home_ip

        want = wanted(self._app.settings)
        if want is None:
            raise DDNSError("web.ddns_off")
        async with self._lock:
            before = self.status()
            ipv4 = await home_ip()
            ipv6 = await asyncio.to_thread(own_ipv6) if want.ipv6 else None
            same = (before.get("ok") and before.get("hostname") == want.hostname
                    and before.get("ipv4") == ipv4 and before.get("ipv6") == ipv6)  # fmt: skip
            if same and not force and time.monotonic() - self._last_push < REFRESH_SECONDS:
                return before
            status: dict[str, Any] = {"hostname": want.hostname, "ipv4": ipv4, "ipv6": ipv6,
                                      "time": utcnow().isoformat()}  # fmt: skip
            try:
                if ipv4 is None:
                    raise DDNSError("web.ddns_no_address")
                dns = CloudflareDNS(want.token)
                zone = await dns.zone_id(want.hostname)
                status["a"] = await dns.set_record(zone, "A", want.hostname, ipv4)
                if ipv6:
                    status["aaaa"] = await dns.set_record(zone, "AAAA", want.hostname, ipv6)
                status["ok"] = True
                self._last_push = time.monotonic()
            except DDNSError as exc:
                status.update(ok=False, error=exc.key, detail=exc.detail)
                self._app.db.set(STATUS_KEY, status)
                raise
            self._app.db.set(STATUS_KEY, status)
            if status.get("a") in ("updated", "created"):
                log.info("dynamic DNS: %s now points at %s", want.hostname, ipv4)
            return status

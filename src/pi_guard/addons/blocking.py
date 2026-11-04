"""Block websites for everyone or one device, for good or for a while, through Pi-hole.

A block covers a domain with all its subdomains. Temporary blocks are removed by a loop
here. For one device, the device gets its own Pi-hole group and the rule applies to that
group only. If the block page is set up, blocked domains also point to it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from pi_guard.core.timeutil import utcnow
from pi_guard.network.pihole import Pihole, PiholeError
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

SWEEP_SECONDS = 30
MAX_MINUTES = 7 * 24 * 60
_DOMAIN_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}$")
_TABLE = """
CREATE TABLE IF NOT EXISTS net_blocks (
    id INTEGER PRIMARY KEY, domain TEXT NOT NULL, device TEXT, label TEXT NOT NULL DEFAULT '',
    until TEXT, created TEXT NOT NULL, UNIQUE (domain, device));
"""


class BlockError(Exception):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def clean_domain(text: str) -> str:
    """Accepts what people paste: https://www.example.org/page -> example.org."""
    domain = re.sub(r"^[a-z]+://", "", text.strip().casefold()).split("/")[0].split(":")[0]
    domain = domain.removeprefix("www.")
    if not _DOMAIN_RE.match(domain):
        raise BlockError("web.nm_bad_domain")
    return domain


def rule(domain: str) -> str:
    return r"(\.|^)" + domain.replace(".", r"\.") + "$"


class Blocks:
    def __init__(self, db: Database, pihole: Pihole, page_ip: str | None = None) -> None:
        self._db = db
        self._pihole = pihole
        self._page_ip = page_ip
        db.executescript(_TABLE)

    def list(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT * FROM net_blocks ORDER BY created DESC").fetchall()
        return [dict(r) for r in rows]

    async def _group(self, device: str) -> int:
        """A Pi-hole group for one device, keeping it in the default group as well."""
        name = f"pi-guard-{device.replace('.', '-').replace(':', '-')}"
        groups = (await self._pihole.request("GET", "/api/groups")).get("groups", [])
        found = next((g for g in groups if g.get("name") == name), None)
        if found is None:
            await self._pihole.request("POST", "/api/groups", json={
                "name": name, "comment": "pi-guard", "enabled": True})  # fmt: skip
            groups = (await self._pihole.request("GET", "/api/groups")).get("groups", [])
            found = next(g for g in groups if g.get("name") == name)
        group = int(found["id"])
        clients = (await self._pihole.request("GET", "/api/clients")).get("clients", [])
        client = next((c for c in clients if c.get("client") == device), None)
        wanted = sorted({0, group, *(client.get("groups", []) if client else [])})
        if client is None:
            await self._pihole.request("POST", "/api/clients", json={
                "client": device, "comment": "pi-guard", "groups": wanted})  # fmt: skip
        elif set(wanted) != set(client.get("groups", [])):
            await self._pihole.request("PUT", f"/api/clients/{quote(device, safe='')}", json={
                "comment": client.get("comment") or "pi-guard", "groups": wanted})  # fmt: skip
        return group

    async def add(
        self, text: str, minutes: int | None = None, device: str | None = None, label: str = ""
    ) -> dict[str, Any]:
        domain = clean_domain(text)
        if minutes is not None and not 1 <= minutes <= MAX_MINUTES:
            raise BlockError("web.nm_bad_minutes")
        groups = [await self._group(device)] if device else [0]
        await self._pihole.request("POST", "/api/domains/deny/regex", json={
            "domain": rule(domain), "comment": f"pi-guard: {domain}", "groups": groups,
            "enabled": True})  # fmt: skip
        if self._page_ip and not device:
            await self._page(domain, add=True)
        until = (utcnow() + timedelta(minutes=minutes)).isoformat() if minutes else None
        self._db.execute(
            "INSERT OR REPLACE INTO net_blocks (domain, device, label, until, created) "
            "VALUES (?, ?, ?, ?, ?)", (domain, device, label[:60], until, utcnow().isoformat()),
        )  # fmt: skip
        return {"domain": domain, "until": until}

    async def _page(self, domain: str, add: bool) -> None:
        entry = quote(f"{self._page_ip} {domain}", safe="")
        try:
            await self._pihole.request("PUT" if add else "DELETE", f"/api/config/dns/hosts/{entry}")
        except PiholeError as exc:
            log.warning("block page entry not changed: %s", exc)

    async def remove(self, block_id: int) -> bool:
        row = self._db.execute("SELECT * FROM net_blocks WHERE id = ?", (block_id,)).fetchone()
        if row is None:
            return False
        others = self._db.execute(
            "SELECT 1 FROM net_blocks WHERE domain = ? AND id != ?", (row["domain"], block_id)
        ).fetchone()
        if others is None:
            encoded = quote(rule(row["domain"]), safe="")
            await self._pihole.request("DELETE", f"/api/domains/deny/regex/{encoded}")
            if self._page_ip and not row["device"]:
                await self._page(row["domain"], add=False)
        self._db.execute("DELETE FROM net_blocks WHERE id = ?", (block_id,))
        return True

    async def run(self) -> None:
        while True:
            now = utcnow().isoformat()
            rows = self._db.execute(
                "SELECT id, domain FROM net_blocks WHERE until IS NOT NULL AND until <= ?", (now,)
            ).fetchall()
            for row in rows:
                try:
                    await self.remove(row["id"])
                    log.info("temporary block of %s ended", row["domain"])
                except PiholeError as exc:
                    log.warning("could not end a block: %s", exc)
            await asyncio.sleep(SWEEP_SECONDS)

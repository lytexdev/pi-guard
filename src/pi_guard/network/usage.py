"""Hourly VPN traffic samples for the dashboard charts.

Reads the live WireGuard status file, stores deltas per peer, and prunes old
rows. Counters that go backwards (interface restart) start a new baseline.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.network.wireguard import read_status, status_file
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

EVERY = 60
KEEP_DAYS = 30
STATE_KEY = "wg.usage.baseline"


class VpnUsage:
    def __init__(self, db: Database, settings: Any) -> None:
        self._db = db
        self._settings = settings

    def sample(self) -> None:
        if not self._settings.wireguard_enabled:
            return
        status = read_status(status_file(self._settings, "wireguard.status"))
        if status is None:
            return
        store = {p["public_key"]: p for p in self._peers()}
        baseline: dict[str, Any] = dict(self._db.get(STATE_KEY) or {})
        now = utcnow()
        hour = now.replace(minute=0, second=0, microsecond=0).isoformat()
        next_base: dict[str, Any] = {}
        for peer in status.get("peers") or []:
            key = peer.get("public_key")
            known = store.get(key) if key else None
            if not known:
                continue
            peer_id = int(known["id"])
            rx, tx = int(peer.get("received") or 0), int(peer.get("sent") or 0)
            prev = baseline.get(key) or {}
            prev_rx, prev_tx = int(prev.get("rx") or 0), int(prev.get("tx") or 0)
            d_rx = max(0, rx - prev_rx) if prev else 0
            d_tx = max(0, tx - prev_tx) if prev else 0
            connected = EVERY if peer.get("active") else 0
            if d_rx or d_tx or connected:
                self._db.execute(
                    "INSERT INTO wg_usage (peer_id, hour, rx, tx, connected_seconds) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(peer_id, hour) DO UPDATE SET "
                    "rx = rx + excluded.rx, tx = tx + excluded.tx, "
                    "connected_seconds = connected_seconds + excluded.connected_seconds",
                    (peer_id, hour, d_rx, d_tx, connected),
                )
            next_base[key] = {"rx": rx, "tx": tx}
        self._db.set(STATE_KEY, next_base)
        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        self._db.execute("DELETE FROM wg_usage WHERE hour < ?", (cutoff,))

    def history(self, peer_id: int, days: int = 7) -> list[dict[str, Any]]:
        days = max(1, min(days, KEEP_DAYS))
        start = (
            (utcnow() - timedelta(days=days)).replace(minute=0, second=0, microsecond=0).isoformat()
        )
        rows = self._db.execute(
            "SELECT hour, rx, tx, connected_seconds FROM wg_usage "
            "WHERE peer_id = ? AND hour >= ? ORDER BY hour",
            (peer_id, start),
        ).fetchall()
        return [
            {
                "hour": r["hour"],
                "rx": r["rx"],
                "tx": r["tx"],
                "connected_seconds": r["connected_seconds"],
            }
            for r in rows
        ]

    def totals(self, days: int = 7) -> list[dict[str, Any]]:
        days = max(1, min(days, KEEP_DAYS))
        start = (
            (utcnow() - timedelta(days=days)).replace(minute=0, second=0, microsecond=0).isoformat()
        )
        rows = self._db.execute(
            "SELECT peer_id, SUM(rx) AS rx, SUM(tx) AS tx, SUM(connected_seconds) AS connected "
            "FROM wg_usage WHERE hour >= ? GROUP BY peer_id",
            (start,),
        ).fetchall()
        names = {p["id"]: p["name"] for p in self._peers()}
        return [
            {
                "peer_id": r["peer_id"],
                "name": names.get(r["peer_id"], f"#{r['peer_id']}"),
                "rx": r["rx"],
                "tx": r["tx"],
                "connected_seconds": r["connected"],
            }
            for r in rows
        ]

    def _peers(self) -> list[dict[str, Any]]:
        rows = self._db.execute("SELECT id, name, public_key FROM wg_peers").fetchall()
        return [dict(r) for r in rows]

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.sample)
            except Exception:
                log.exception("vpn usage sample failed")
            await asyncio.sleep(EVERY)

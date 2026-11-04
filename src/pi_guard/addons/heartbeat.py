"""Heartbeat watcher: tells you when a server or web service stops answering.

Checks run one task per target. A target only counts as down after several failed
checks in a row, and each outage is reported once when it starts and once when it
ends. Addresses may contain tokens, so they never go into events or logs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from pi_guard.config.files import HeartbeatTarget
from pi_guard.core.events import Event, EventBus, EventType
from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

DOWN_KEY = "heartbeat.down"


@dataclass
class TargetState:
    up: bool | None = None  # None: not checked yet
    failures: int = 0
    latency_ms: int | None = None
    reason: str | None = None
    last_check: datetime | None = None
    since: datetime | None = None


async def _check_http(target: HeartbeatTarget) -> None:
    async with httpx.AsyncClient(
        timeout=target.timeout_seconds, verify=target.verify_tls, follow_redirects=True
    ) as client:
        response = await client.get(target.address)
    # 401 and 403 still prove that the service is alive.
    if response.status_code >= 500:
        raise ConnectionError(f"HTTP {response.status_code}")
    if target.expected_text and target.expected_text not in response.text:
        raise ConnectionError("expected text missing")


async def _check_tcp(target: HeartbeatTarget) -> None:
    host, _, port = target.address.rpartition(":")
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(host.strip("[]"), int(port)), target.timeout_seconds
    )
    writer.close()


async def _check_ping(target: HeartbeatTarget) -> None:
    process = await asyncio.create_subprocess_exec(
        "ping", "-n", "-c", "1", "-W", str(target.timeout_seconds), "--", target.address,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    if await asyncio.wait_for(process.wait(), target.timeout_seconds + 5) != 0:
        raise ConnectionError("no answer")


CHECKS = {"http": _check_http, "tcp": _check_tcp, "ping": _check_ping}


def _reason(exc: BaseException) -> str:
    """Short and without the address, which may contain a token."""
    if isinstance(exc, TimeoutError | httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.ConnectError | ConnectionRefusedError):
        return "connection failed"
    if isinstance(exc, ConnectionError) and exc.args:
        return str(exc.args[0])[:60]
    return type(exc).__name__


class HeartbeatWatcher:
    def __init__(self, targets: list[HeartbeatTarget], bus: EventBus, db: Database) -> None:
        self.targets = [t for t in targets if t.enabled]
        self._bus = bus
        self._db = db
        down = set(db.get(DOWN_KEY) or [])
        # Still down after a restart: the outage was already reported.
        self.state = {t.id: TargetState(up=False if t.id in down else None) for t in self.targets}

    async def check(self, target: HeartbeatTarget) -> None:
        state = self.state[target.id]
        started = time.monotonic()
        reason = None
        try:
            await CHECKS[target.kind](target)
        except (OSError, TimeoutError, httpx.HTTPError, ValueError) as exc:
            reason = _reason(exc)
        state.last_check = utcnow()
        if reason is None:
            state.latency_ms = round((time.monotonic() - started) * 1000)
            self._passed(target, state)
        else:
            state.latency_ms = None
            state.reason = reason
            self._failed(target, state, reason)

    def _passed(self, target: HeartbeatTarget, state: TargetState) -> None:
        was_down = state.up is False
        state.failures, state.reason = 0, None
        if state.up is not True:
            state.up, state.since = True, utcnow()
        if was_down:
            self._remember()
            self._bus.publish(Event(EventType.SERVICE_UP, data={"name": target.name,
                                                                "target": target.id}))  # fmt: skip

    def _failed(self, target: HeartbeatTarget, state: TargetState, reason: str) -> None:
        state.failures += 1
        if state.up is False or state.failures < target.failures_before_alert:
            return
        state.up, state.since = False, utcnow()
        self._remember()
        log.warning("service %s is down: %s", target.id, reason)
        self._bus.publish(Event(EventType.SERVICE_DOWN, data={
            "name": target.name, "target": target.id, "reason": reason}))  # fmt: skip

    def _remember(self) -> None:
        self._db.set(DOWN_KEY, sorted(i for i, s in self.state.items() if s.up is False))

    async def _watch(self, target: HeartbeatTarget) -> None:
        while True:
            await self.check(target)
            await asyncio.sleep(target.interval_seconds)

    async def run(self) -> None:
        if not self.targets:
            await asyncio.Event().wait()
        await asyncio.gather(*(self._watch(t) for t in self.targets))

    def overview(self) -> list[dict[str, Any]]:
        rows = []
        for target in self.targets:
            state = self.state[target.id]
            rows.append({
                "id": target.id, "name": target.name, "kind": target.kind, "up": state.up,
                "latency_ms": state.latency_ms, "reason": state.reason,
                "last_check": state.last_check.isoformat() if state.last_check else None,
                "since": state.since.isoformat() if state.since else None,
            })  # fmt: skip
        return rows

"""In-process event bus.

Publishing never blocks: every subscriber owns a bounded queue and the oldest
event is dropped when a consumer falls behind. Recording and detection can
therefore never be stalled by a slow notifier or a dead MQTT broker.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow

log = logging.getLogger(__name__)


class EventType(StrEnum):
    MOTION_DETECTED = "motion_detected"
    MOTION_ENDED = "motion_ended"
    PERSON_DETECTED = "person_detected"
    KNOWN_PERSON = "known_person"
    UNKNOWN_PERSON = "unknown_person"
    CAMERA_DISCONNECTED = "camera_disconnected"
    CAMERA_RECONNECTED = "camera_reconnected"
    RECORDING_FAILED = "recording_failed"
    STORAGE_LOW = "storage_low"
    PRESENCE_CHANGED = "presence_changed"
    ARRIVAL_DETECTED = "arrival_detected"
    EMERGENCY_ESCALATED = "emergency_escalated"  # trusted device back, but the mode stays AWAY
    MODE_CHANGED = "mode_changed"
    SYSTEM_STARTED = "system_started"


@dataclass(frozen=True)
class Event:
    type: EventType
    camera: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    snapshot: Path | None = None
    ts: datetime = field(default_factory=utcnow)


Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._queues: list[tuple[str, asyncio.Queue[Event]]] = []

    def subscribe(self, name: str, maxsize: int = 100) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize)
        self._queues.append((name, queue))
        return queue

    def publish(self, event: Event) -> None:
        for name, queue in self._queues:
            if queue.full():
                dropped = queue.get_nowait()
                log.warning("subscriber %s is behind; dropped %s event", name, dropped.type)
            queue.put_nowait(event)

    @staticmethod
    async def consume(queue: asyncio.Queue[Event], name: str, handler: Handler) -> None:
        """Feed events to `handler` forever; a failing handler only loses that one event.

        Subscribe when the consumer is constructed, not when it starts running, so
        events published during startup are not missed.
        """
        while True:
            event = await queue.get()
            try:
                await handler(event)
            except Exception:
                log.exception("%s failed to handle %s", name, event.type)

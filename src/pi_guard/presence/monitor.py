from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from pi_guard.config.files import PresenceSettings
from pi_guard.config.settings import Settings
from pi_guard.core.events import Event, EventBus, EventType
from pi_guard.core.modes import ModeController, Presence
from pi_guard.core.timeutil import utcnow
from pi_guard.presence.resolver import Observation, PresenceResolver, combine
from pi_guard.presence.sources import FritzBoxSource, NeighbourSource, PresenceSource

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceStatus:
    id: str
    name: str
    last_seen: datetime | None
    observation: Observation | None


def build_sources(settings: Settings, presence: PresenceSettings) -> list[PresenceSource]:
    sources: list[PresenceSource] = []
    for name in presence.sources:
        if name == "neighbours":
            sources.append(NeighbourSource(presence.interface))
        else:
            password = settings.fritzbox_password
            sources.append(
                FritzBoxSource(presence.fritzbox, password.get_secret_value() if password else None)
            )
    return sources


class PresenceMonitor:
    def __init__(
        self,
        settings: PresenceSettings,
        sources: list[PresenceSource],
        modes: ModeController,
        bus: EventBus,
    ) -> None:
        self._settings = settings
        self._devices = [d for d in settings.devices if d.enabled]
        self._sources = sources
        self._modes = modes
        self._bus = bus
        self._resolver: PresenceResolver | None = None  # starts with the first check
        self._last: dict[str, Observation] = {}
        for device in self._devices:
            if device.randomized_mac:
                log.info(
                    "presence device %s uses a private (randomized) MAC; keep the phone's "
                    "per-network address fixed or detection will stop working",
                    device.id,
                )

    def devices(self) -> list[DeviceStatus]:
        seen = self._resolver.last_seen if self._resolver else {}
        return [
            DeviceStatus(d.id, d.name, seen.get(d.id), self._last.get(d.id)) for d in self._devices
        ]

    async def check(self, now: datetime | None = None) -> Presence:
        results = await asyncio.gather(
            *(source.observe(self._devices) for source in self._sources), return_exceptions=True
        )
        per_device: dict[str, list[Observation]] = {d.id: [] for d in self._devices}
        for source, result in zip(self._sources, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("presence source %s failed: %s", source.name, result)
                continue
            for device_id, observation in result.items():
                per_device[device_id].append(observation)
        self._last = {d: combine(obs) for d, obs in per_device.items()}
        now = now or utcnow()
        if self._resolver is None:
            self._resolver = PresenceResolver(self._settings, started=now)
        previous = self._resolver.state
        state = self._resolver.update(now, self._last)
        if state is not previous:
            log.info("presence %s -> %s", previous, state)
            self._bus.publish(
                Event(
                    EventType.PRESENCE_CHANGED,
                    data={"presence": state.value, "previous": previous.value},
                )
            )
        self._modes.update_presence(state)
        return state

    async def run(self) -> None:
        if not self._devices:
            log.warning("presence detection is enabled but no trusted devices are configured")
        while True:
            await self.check()
            await asyncio.sleep(self._settings.check_interval_seconds)

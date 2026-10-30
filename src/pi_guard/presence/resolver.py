"""Turns per-device observations into HOME / AWAY / UNKNOWN.

A device seen in the network is evidence that someone is home; a device not
seen is weak evidence of absence (phones sleep, drop Wi-Fi, randomize MACs).
Hence the asymmetry:
  * HOME needs a device to be seen continuously for `home_confirmation_seconds`
  * AWAY needs *no* device seen for `away_grace_period_seconds`, with every
    check in the current round succeeding
  * if checks keep failing, the answer is UNKNOWN, never AWAY
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from pi_guard.config.files import PresenceSettings
from pi_guard.core.modes import Presence


class Observation(StrEnum):
    SEEN = "seen"
    NOT_SEEN = "not_seen"
    ERROR = "error"


def combine(observations: list[Observation]) -> Observation:
    """Merge the answers of several sources for one device."""
    if Observation.SEEN in observations:
        return Observation.SEEN
    if Observation.NOT_SEEN in observations:
        return Observation.NOT_SEEN
    return Observation.ERROR


@dataclass
class PresenceResolver:
    settings: PresenceSettings
    started: datetime
    state: Presence = Presence.UNKNOWN
    last_seen: dict[str, datetime] = field(default_factory=dict)
    _streak: dict[str, datetime] = field(default_factory=dict)
    _last_success: datetime | None = None

    def update(self, now: datetime, observations: dict[str, Observation]) -> Presence:
        grace = timedelta(seconds=self.settings.away_grace_period_seconds)
        confirm = timedelta(seconds=self.settings.home_confirmation_seconds)
        if not observations or all(o is Observation.ERROR for o in observations.values()):
            reference = self._last_success or self.started
            if now - reference >= grace:
                self.state = Presence.UNKNOWN
            return self.state

        self._last_success = now
        for device, observation in observations.items():
            if observation is Observation.SEEN:
                self.last_seen[device] = now
                self._streak.setdefault(device, now)
            elif observation is Observation.NOT_SEEN:
                self._streak.pop(device, None)

        confirmed = any(now - start >= confirm for start in self._streak.values())
        recently_seen = any(now - seen < grace for seen in self.last_seen.values())
        complete = Observation.ERROR not in observations.values()

        if confirmed:
            self.state = Presence.HOME
        elif self.state is Presence.HOME and recently_seen:
            pass  # hysteresis: stay HOME through short gaps
        elif not recently_seen and complete and now - self.started >= grace:
            self.state = Presence.AWAY
        return self.state

"""Built-in alert rules, overridable per event type in config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from pi_guard.config.files import NotificationRule
from pi_guard.core.events import EventType
from pi_guard.core.modes import Mode

Priority = Literal["low", "normal", "high", "critical"]
PRIORITY_ORDER = {"low": 0, "normal": 1, "high": 2, "critical": 3}
BOTH = (Mode.HOME, Mode.AWAY)


@dataclass(frozen=True)
class Rule:
    enabled: bool
    priority: Priority
    modes: tuple[Mode, ...]
    recipients: Literal["all", "admins"]
    cooldown_seconds: int
    attach: Literal["none", "snapshot", "clip"] = "none"
    providers: tuple[str, ...] = ("telegram", "signal", "whatsapp", "discord", "email")

    @property
    def bypasses_quiet_hours(self) -> bool:
        return PRIORITY_ORDER[self.priority] >= PRIORITY_ORDER["high"]


DEFAULTS: dict[EventType, Rule] = {
    EventType.MOTION_DETECTED: Rule(True, "normal", (Mode.AWAY,), "all", 120, "snapshot"),
    EventType.PERSON_DETECTED: Rule(True, "high", (Mode.AWAY,), "all", 120, "snapshot"),
    EventType.UNKNOWN_PERSON: Rule(True, "high", (Mode.AWAY,), "all", 120, "snapshot"),
    EventType.KNOWN_PERSON: Rule(False, "low", (Mode.AWAY,), "admins", 600, "snapshot"),
    EventType.CAMERA_DISCONNECTED: Rule(True, "high", BOTH, "admins", 1800),
    EventType.CAMERA_RECONNECTED: Rule(True, "normal", BOTH, "admins", 0),
    EventType.RECORDING_FAILED: Rule(True, "critical", BOTH, "admins", 3600),
    EventType.STORAGE_LOW: Rule(True, "critical", BOTH, "admins", 12 * 3600),
    EventType.PRESENCE_CHANGED: Rule(False, "low", BOTH, "admins", 0),
    EventType.ARRIVAL_DETECTED: Rule(True, "high", (Mode.AWAY,), "all", 600),
    # Owners are told directly by the escalation; this rule is for extra channels.
    EventType.EMERGENCY_ESCALATED: Rule(False, "critical", BOTH, "admins", 0),
    EventType.MODE_CHANGED: Rule(True, "low", BOTH, "all", 0),
    EventType.SYSTEM_STARTED: Rule(True, "low", BOTH, "admins", 0),
    EventType.MOTION_ENDED: Rule(False, "low", BOTH, "admins", 0),
    EventType.SERVICE_DOWN: Rule(True, "high", BOTH, "admins", 0),
    EventType.SERVICE_UP: Rule(True, "normal", BOTH, "admins", 0),
}


def resolve(event: EventType, overrides: dict[str, NotificationRule]) -> Rule:
    rule = DEFAULTS[event]
    override = overrides.get(event.value)
    if override is None:
        return rule
    changes = override.model_dump(exclude_none=True)
    if "modes" in changes:
        changes["modes"] = tuple(Mode(m) for m in changes["modes"])
    if "providers" in changes:
        changes["providers"] = tuple(changes["providers"])
    return replace(rule, **changes)

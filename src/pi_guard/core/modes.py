"""HOME/AWAY operating mode.

The *effective* mode (HOME or AWAY) is what alert rules look at. How it was
chosen is tracked separately: AUTO follows presence detection, MANUAL_OVERRIDE
follows an explicit command and always wins until it expires or `/mode auto`
hands control back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pi_guard.core.events import Event, EventBus, EventType
from pi_guard.core.timeutil import utcnow

if TYPE_CHECKING:
    from pi_guard.config import Config
    from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

_KEY = "mode"


class Mode(StrEnum):
    HOME = "HOME"
    AWAY = "AWAY"


class Presence(StrEnum):
    HOME = "HOME"
    AWAY = "AWAY"
    UNKNOWN = "UNKNOWN"


class Selection(StrEnum):
    AUTO = "AUTO"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class AutoUnavailableError(Exception):
    """Raised for `/mode auto` when presence detection is not enabled."""


@dataclass(frozen=True)
class ModeState:
    effective: Mode
    selection: Selection
    presence: Presence
    manual_until: datetime | None = None
    revert_to: Mode | None = None
    changed_at: datetime | None = None


class ModeController:
    def __init__(
        self,
        db: Database,
        bus: EventBus | None,
        default_mode: Mode,
        presence_enabled: bool,
        unknown_mode: Literal["AWAY", "HOME", "last"] = "AWAY",
        *,
        auto_home: bool = True,
        auto_away: bool = True,
    ) -> None:
        self._db = db
        self._auto_home = auto_home
        self._auto_away = auto_away
        self._bus = bus
        self._presence_enabled = presence_enabled
        self._unknown_mode = unknown_mode
        self._loaded_at: str | None = None
        self._state = self._load(default_mode)

    @property
    def state(self) -> ModeState:
        return self._state

    @property
    def effective(self) -> Mode:
        return self._state.effective

    @property
    def presence_enabled(self) -> bool:
        return self._presence_enabled

    def set_manual(
        self, mode: Mode, duration: timedelta | None = None, actor: str = "system"
    ) -> ModeState:
        now = utcnow()
        until = now + duration if duration else None
        revert = self._state.effective if duration else None
        if duration and self._state.selection is Selection.MANUAL_OVERRIDE:
            revert = self._state.revert_to or self._state.effective
        self._apply(
            replace(
                self._state,
                selection=Selection.MANUAL_OVERRIDE,
                manual_until=until,
                revert_to=revert,
            ),
            mode,
            actor,
        )
        return self._state

    def set_auto(self, actor: str = "system") -> ModeState:
        if not self._presence_enabled:
            raise AutoUnavailableError
        state = replace(self._state, selection=Selection.AUTO, manual_until=None, revert_to=None)
        self._apply(state, self._target(state), actor)
        return self._state

    def update_presence(self, presence: Presence) -> None:
        if presence is self._state.presence:
            return
        state = replace(self._state, presence=presence)
        target = self._target(state) if state.selection is Selection.AUTO else state.effective
        self._apply(state, target, "presence")

    def tick(self, now: datetime | None = None) -> None:
        """Pick up changes written by the CLI and expire time-limited overrides."""
        _, updated_at = self._db.get_with_time(_KEY)
        if updated_at and updated_at != self._loaded_at:
            previous = self._state.effective
            self._state = self._load(previous, keep_presence=self._state.presence)
            if self._state.effective is not previous:
                self._announce(previous, "cli")
        until = self._state.manual_until
        if until and (now or utcnow()) >= until:
            if self._presence_enabled:
                state = replace(
                    self._state, selection=Selection.AUTO, manual_until=None, revert_to=None
                )
                self._apply(state, self._target(state), "override-expired")
            else:
                target = self._state.revert_to or self._state.effective
                self._apply(
                    replace(self._state, manual_until=None, revert_to=None),
                    target,
                    "override-expired",
                )

    def _target(self, state: ModeState) -> Mode:
        """Presence-derived mode, limited to the directions automation may switch.

        With auto_home off, presence can arm (AWAY) but never disarm: a phone in
        the Wi-Fi is not proof that its owner is the one at the door.
        """
        target = self._derive(state)
        if target is Mode.HOME and state.effective is Mode.AWAY and not self._auto_home:
            return Mode.AWAY
        if target is Mode.AWAY and state.effective is Mode.HOME and not self._auto_away:
            return Mode.HOME
        return target

    def _derive(self, state: ModeState) -> Mode:
        if state.presence is Presence.HOME:
            return Mode.HOME
        if state.presence is Presence.AWAY:
            return Mode.AWAY
        if self._unknown_mode == "last":
            return state.effective
        return Mode(self._unknown_mode)

    def _apply(self, state: ModeState, target: Mode, actor: str) -> None:
        previous = self._state.effective
        changed = target is not previous
        self._state = replace(
            state, effective=target, changed_at=utcnow() if changed else state.changed_at
        )
        self._save()
        if changed:
            log.info("mode %s -> %s (%s, by %s)", previous, target, state.selection, actor)
            self._db.audit(actor, "mode", f"{previous}->{target} {state.selection}")
            self._announce(previous, actor)

    def _announce(self, previous: Mode, actor: str) -> None:
        if self._bus:
            self._bus.publish(
                Event(
                    EventType.MODE_CHANGED,
                    data={
                        "mode": self._state.effective.value,
                        "previous": previous.value,
                        "selection": self._state.selection.value,
                        "actor": actor,
                    },
                )
            )

    def _save(self) -> None:
        s = self._state
        self._db.set(
            _KEY,
            {
                "effective": s.effective.value,
                "selection": s.selection.value,
                "manual_until": s.manual_until.isoformat() if s.manual_until else None,
                "revert_to": s.revert_to.value if s.revert_to else None,
                "changed_at": s.changed_at.isoformat() if s.changed_at else None,
            },
        )
        self._loaded_at = self._db.get_with_time(_KEY)[1]

    def _load(self, default: Mode, keep_presence: Presence = Presence.UNKNOWN) -> ModeState:
        raw, self._loaded_at = self._db.get_with_time(_KEY)
        if not raw:
            selection = Selection.AUTO if self._presence_enabled else Selection.MANUAL_OVERRIDE
            return ModeState(default, selection, keep_presence)
        selection = Selection(raw["selection"])
        if selection is Selection.AUTO and not self._presence_enabled:
            selection = Selection.MANUAL_OVERRIDE
        until = raw.get("manual_until")
        changed = raw.get("changed_at")
        return ModeState(
            effective=Mode(raw["effective"]),
            selection=selection,
            presence=keep_presence,
            manual_until=datetime.fromisoformat(until) if until else None,
            revert_to=Mode(raw["revert_to"]) if raw.get("revert_to") else None,
            changed_at=datetime.fromisoformat(changed) if changed else None,
        )


def mode_controller(db: Database, bus: EventBus | None, config: Config) -> ModeController:
    s = config.settings
    return ModeController(
        db,
        bus,
        s.default_mode,
        s.presence_enabled,
        config.file.presence.unknown_mode,
        auto_home=s.presence_auto_home,
        auto_away=s.presence_auto_away,
    )

"""Weekly plan: when nobody is usually at home.

A plan is a list of blocks in the home's time zone. pi-guard only acts when a block starts or
ends, so whatever someone sets by hand in between stays until the next change.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from pi_guard.core.modes import Mode, ModeController, Presence
from pi_guard.core.timeutil import utcnow

if TYPE_CHECKING:
    from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

PLAN_KEY = "schedule.plan"
STATE_KEY = "schedule.state"
CALENDAR_KEY = "schedule.calendar"
FEEDS_KEY = "schedule.feeds"
MAX_EVENTS = 500
ACTOR = "schedule"
DAY_MINUTES = 24 * 60
MAX_BLOCKS = 100
MAX_LABEL = 40


def clean_plan(raw: Any) -> dict[str, Any]:
    """The plan as stored; raises ValueError for anything that is not a valid plan."""
    if not isinstance(raw, dict) or not isinstance(raw.get("blocks"), list):
        raise ValueError("plan")
    if len(raw["blocks"]) > MAX_BLOCKS:
        raise ValueError("too many blocks")
    blocks = []
    for item in raw["blocks"]:
        day, start, end = item.get("day"), item.get("start"), item.get("end")
        numbers = all(type(n) is int for n in (day, start, end))
        if not numbers or not 0 <= day <= 6 or not 0 <= start < end <= DAY_MINUTES:
            raise ValueError("block")
        blocks.append({"day": day, "start": start, "end": end,
                       "label": str(item.get("label", "")).strip()[:MAX_LABEL]})  # fmt: skip
    blocks.sort(key=lambda b: (b["day"], b["start"]))
    return {"enabled": raw.get("enabled") is True, "blocks": blocks,
            "respect_presence": raw.get("respect_presence") is not False}  # fmt: skip


def clean_calendar(raw: Any) -> list[dict[str, str]]:
    """Busy times from a phone's calendar, as UTC times; nothing else is kept."""
    if not isinstance(raw, list) or len(raw) > MAX_EVENTS:
        raise ValueError("calendar")
    events = []
    for item in raw:
        start = datetime.fromisoformat(str(item["start"])).astimezone(UTC)
        end = datetime.fromisoformat(str(item["end"])).astimezone(UTC)
        if end <= start:
            raise ValueError("calendar")
        events.append({"start": start.isoformat(), "end": end.isoformat()})
    return events


def all_busy(db: Any) -> list[dict[str, str]]:
    """From the phones and from calendars on the web."""
    feeds = [e for feed in db.get(FEEDS_KEY) or [] for e in feed["events"]]
    return [*(db.get(CALENDAR_KEY) or []), *feeds]


def busy_at(events: list[dict[str, str]], now: datetime) -> bool:
    return any(datetime.fromisoformat(e["start"]) <= now < datetime.fromisoformat(e["end"])
               for e in events)  # fmt: skip


def block_at(plan: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    minute = now.hour * 60 + now.minute
    for block in plan.get("blocks", []):
        if block["day"] == now.weekday() and block["start"] <= minute < block["end"]:
            return dict(block)
    return None


class ScheduleRunner:
    def __init__(self, db: Database, modes: ModeController, tz: ZoneInfo) -> None:
        self._db = db
        self._modes = modes
        self._tz = tz

    def plan(self) -> dict[str, Any]:
        return self._db.get(PLAN_KEY) or {"enabled": False, "blocks": [], "respect_presence": True}

    def tick(self, now: datetime | None = None) -> None:
        plan = self.plan()
        state = self._db.get(STATE_KEY) or {"active": False, "applied": None}
        moment = now or utcnow()
        busy = busy_at(all_busy(self._db), moment)
        planned = bool(plan["enabled"] and block_at(plan, moment.astimezone(self._tz)))
        active = planned or busy
        applied = state["applied"]
        if active and (not state["active"] or (busy and not state.get("busy"))):
            applied = self._start(plan, force=busy) or applied
        elif state["active"] and not active:
            self._end(state)
            applied = None
        elif active == state["active"] and busy == state.get("busy", False):
            return
        self._db.set(STATE_KEY, {"active": active, "busy": busy, "applied": applied})

    def _start(self, plan: dict[str, Any], force: bool = False) -> str | None:
        """`force`: a calendar entry means away, whatever the phones in the Wi-Fi say."""
        mode = self._modes.state
        someone_home = self._modes.presence_enabled and mode.presence is Presence.HOME
        if mode.effective is Mode.AWAY or (someone_home and plan["respect_presence"] and not force):
            return None
        changed = self._modes.set_manual(Mode.AWAY, actor=ACTOR).changed_at
        return changed.isoformat() if changed else None

    def _end(self, state: dict[str, Any]) -> None:
        """Back to normal, unless the plan did not switch or someone changed the mode since."""
        changed = self._modes.state.changed_at
        if state["applied"] is None or not changed or changed.isoformat() != state["applied"]:
            return
        if self._modes.presence_enabled:
            self._modes.set_auto(ACTOR)
        else:
            self._modes.set_manual(Mode.HOME, actor=ACTOR)

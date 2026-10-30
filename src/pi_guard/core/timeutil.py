from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

_DURATION_RE = re.compile(r"^(\d{1,4})\s*(s|sek|sec|m|min|h|std|d|t)?$")
_UNITS = {
    None: 60,  # a bare number means minutes in chat ("/away 30")
    "s": 1,
    "sec": 1,
    "sek": 1,
    "m": 60,
    "min": 60,
    "h": 3600,
    "std": 3600,
    "d": 86400,
    "t": 86400,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_duration(text: str) -> timedelta:
    """Parse short chat durations such as `30m`, `2h`, `1d` or `2std`."""
    match = _DURATION_RE.match(text.strip().lower())
    if not match:
        raise ValueError(text)
    value = int(match.group(1)) * _UNITS[match.group(2)]
    if value <= 0:
        raise ValueError(text)
    return timedelta(seconds=value)


@dataclass(frozen=True)
class TimeWindow:
    """A daily local-time window that may wrap past midnight, e.g. 22:00-07:00."""

    start: time
    end: time

    @classmethod
    def parse(cls, text: str) -> TimeWindow:
        try:
            start, end = (time.fromisoformat(part.strip()) for part in text.split("-"))
        except ValueError as exc:
            raise ValueError(f"{text!r} is not a time window like 22:00-07:00") from exc
        if start == end:
            raise ValueError(f"{text!r} has the same start and end")
        return cls(start, end)

    def contains(self, moment: datetime) -> bool:
        now = moment.timetz().replace(tzinfo=None)
        if self.start < self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end


def in_any_window(windows: list[TimeWindow], moment: datetime) -> bool:
    return any(window.contains(moment) for window in windows)

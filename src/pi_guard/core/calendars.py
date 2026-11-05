"""Busy times from calendars on the web: Google, Proton, Nextcloud, anything with an iCal link.

Such a link is a password, so it is never shown again, logged or exported. Like with the phone's
calendar only start and end stay here, no titles, places or people.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from pi_guard.core.schedule import FEEDS_KEY, MAX_EVENTS, clean_calendar
from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

DAYS = 14  # like the app
MAX_BYTES = 8_000_000
MAX_FEEDS = 10
MAX_NAME = 40
REFRESH_SECONDS = 3600
TIMEOUT = 20


class CalendarError(Exception):
    """Message is a translation key."""


def normal_link(link: str) -> str:
    link = link.strip()
    if link.startswith("webcal://"):
        link = "https://" + link[len("webcal://") :]
    parts = urlsplit(link)
    if parts.scheme != "https" or not parts.hostname:
        raise CalendarError("web.cal_bad_link")
    return link


def busy_times(text: bytes, now: datetime) -> list[dict[str, str]]:
    """The next two weeks with repeats worked out, leaving out what does not keep anyone away."""
    import icalendar
    import recurring_ical_events  # type: ignore[import-untyped]

    try:
        calendar = icalendar.Calendar.from_ical(text)
        events = recurring_ical_events.of(calendar).between(now, now + timedelta(days=DAYS))
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise CalendarError("web.cal_not_a_calendar") from exc
    busy = []
    for event in events:
        start, end = event.get("DTSTART").dt, event.get("DTEND")
        end = end.dt if end is not None else None
        whole_day = isinstance(start, date) and not isinstance(start, datetime)
        free = str(event.get("TRANSP", "")).upper() == "TRANSPARENT"
        cancelled = str(event.get("STATUS", "")).upper() == "CANCELLED"
        if whole_day or free or cancelled or end is None:
            continue
        busy.append({"start": _utc(start), "end": _utc(end)})
    busy.sort(key=lambda e: e["start"])
    return clean_calendar(busy[:MAX_EVENTS])


def _utc(moment: datetime) -> str:
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


async def fetch(link: str) -> bytes:
    data = bytearray()
    try:
        async with (
            httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client,
            client.stream("GET", link) as answer,
        ):
            answer.raise_for_status()
            async for block in answer.aiter_bytes():
                data += block
                if len(data) > MAX_BYTES:
                    raise CalendarError("web.cal_too_big")
    except httpx.HTTPError as exc:  # its text would contain the link
        log.info("calendar not reachable: %s", type(exc).__name__)
        raise CalendarError("web.cal_unreachable") from None
    return bytes(data)


class Feeds:
    def __init__(self, db: Database) -> None:
        self._db = db

    def _all(self) -> list[dict[str, Any]]:
        return list(self._db.get(FEEDS_KEY) or [])

    def public(self) -> list[dict[str, Any]]:
        """Everything but the link itself."""
        return [{"id": f["id"], "name": f["name"], "host": urlsplit(f["link"]).hostname,
                 "events": len(f["events"]), "checked": f.get("checked"),
                 "error": f.get("error")} for f in self._all()]  # fmt: skip

    async def add(self, name: str, link: str) -> None:
        name = " ".join(name.split())[:MAX_NAME]
        link = normal_link(link)
        feeds = self._all()
        if not name or len(feeds) >= MAX_FEEDS:
            raise CalendarError("web.cal_bad_name")
        events = await self._read(link)  # a wrong link shows now, not as silence later
        feeds.append({"id": secrets.token_hex(4), "name": name, "link": link, "events": events,
                      "checked": utcnow().isoformat(), "error": None})  # fmt: skip
        self._db.set(FEEDS_KEY, feeds)

    def remove(self, feed_id: str) -> bool:
        feeds = self._all()
        kept = [f for f in feeds if f["id"] != feed_id]
        self._db.set(FEEDS_KEY, kept)
        return len(kept) != len(feeds)

    async def _read(self, link: str) -> list[dict[str, str]]:
        text = await fetch(link)
        return await asyncio.to_thread(busy_times, text, utcnow())

    async def refresh(self) -> None:
        """A calendar that fails keeps its last known times."""
        feeds = self._all()
        for feed in feeds:
            try:
                feed["events"] = await self._read(feed["link"])
                feed.update(checked=utcnow().isoformat(), error=None)
            except CalendarError as exc:
                feed["error"] = str(exc)
        current = {f["id"] for f in self._all()}  # one may have been removed meanwhile
        self._db.set(FEEDS_KEY, [f for f in feeds if f["id"] in current])

    async def run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(REFRESH_SECONDS)

"""Calendars on the web: only busy times stay, and the link stays secret."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pi_guard.core import calendars
from pi_guard.core.calendars import CalendarError, Feeds, busy_times, normal_link
from pi_guard.core.schedule import CALENDAR_KEY, FEEDS_KEY, all_busy, busy_at

from .test_web import _client, _login

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)  # a Monday
ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:weekly
SUMMARY:Geheimes Treffen bei Dr. Meier
LOCATION:Hauptstrasse 1
DTSTART;TZID=Europe/Berlin:20261005T090000
DTEND;TZID=Europe/Berlin:20261005T170000
RRULE:FREQ=WEEKLY;BYDAY=MO
END:VEVENT
BEGIN:VEVENT
UID:holiday
SUMMARY:Feiertag
DTSTART;VALUE=DATE:20261103
DTEND;VALUE=DATE:20261104
END:VEVENT
BEGIN:VEVENT
UID:free
SUMMARY:Nur zur Info
TRANSP:TRANSPARENT
DTSTART:20261104T100000Z
DTEND:20261104T110000Z
END:VEVENT
BEGIN:VEVENT
UID:off
SUMMARY:Abgesagt
STATUS:CANCELLED
DTSTART:20261105T100000Z
DTEND:20261105T110000Z
END:VEVENT
END:VCALENDAR
"""


def test_repeats_are_worked_out_in_their_time_zone() -> None:
    busy = busy_times(ICS, NOW)
    assert [e["start"][:16] for e in busy] == [
        "2026-11-02T08:00", "2026-11-09T08:00",
    ]  # fmt: skip
    assert busy[0]["end"].startswith("2026-11-02T16:00")


def test_only_start_and_end_stay() -> None:
    stored = str(busy_times(ICS, NOW))
    assert "Meier" not in stored and "Hauptstrasse" not in stored
    assert all(set(e) == {"start", "end"} for e in busy_times(ICS, NOW))


def test_something_else_is_refused() -> None:
    with pytest.raises(CalendarError):
        busy_times(b"<html>login</html>", NOW)


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("webcal://cal.example/a.ics", "https://cal.example/a.ics"),
        ("  https://cal.example/a.ics ", "https://cal.example/a.ics"),
    ],
)
def test_links_are_normalised(link: str, expected: str) -> None:
    assert normal_link(link) == expected


@pytest.mark.parametrize("link", ["http://cal.example/a.ics", "file:///etc/passwd", "", "https://"])
def test_only_encrypted_links(link: str) -> None:
    with pytest.raises(CalendarError):
        normal_link(link)


async def _fetch(_: str) -> bytes:
    return ICS


async def test_a_calendar_counts_as_busy_and_keeps_its_times_when_offline(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calendars, "fetch", _fetch)
    monkeypatch.setattr(calendars, "utcnow", lambda: NOW)
    feeds = Feeds(db)
    await feeds.add("Arbeit", "https://cal.example/geheim-123.ics")
    db.set(
        CALENDAR_KEY, [{"start": "2026-11-03T08:00:00+00:00", "end": "2026-11-03T09:00:00+00:00"}]
    )
    assert busy_at(all_busy(db), datetime(2026, 11, 2, 10, 0, tzinfo=UTC))
    assert busy_at(all_busy(db), datetime(2026, 11, 3, 8, 30, tzinfo=UTC))
    assert not busy_at(all_busy(db), datetime(2026, 11, 2, 18, 0, tzinfo=UTC))

    async def broken(_: str) -> bytes:
        raise CalendarError("web.cal_unreachable")

    monkeypatch.setattr(calendars, "fetch", broken)
    await feeds.refresh()
    row = feeds.public()[0]
    assert row["events"] == 2 and row["error"] == "web.cal_unreachable"


async def test_a_wrong_link_shows_when_adding_it(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def page(_: str) -> bytes:
        return b"<html>"

    monkeypatch.setattr(calendars, "fetch", page)
    with pytest.raises(CalendarError):
        await Feeds(db).add("X", "https://cal.example/x")
    assert not db.get(FEEDS_KEY)


async def test_the_dashboard_never_shows_the_link_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(calendars, "fetch", _fetch)
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        link = "https://calendar.google.com/calendar/ical/x/private-secret123/basic.ics"
        body = {"name": "Google", "link": link}
        assert (await client.post("/api/calendars", json=body, headers=headers)).status == 200
        answer = await (await client.get("/api/calendars")).text()
        assert "secret123" not in answer and "calendar.google.com" in answer
        assert "secret123" not in str(
            app.db.recent_audit(5) if hasattr(app.db, "recent_audit") else ""
        )
        feed = (await (await client.get("/api/calendars")).json())["feeds"][0]
        gone = await client.delete(f"/api/calendars/{feed['id']}", headers=headers)
        assert gone.status == 200 and not app.db.get(FEEDS_KEY)
        bad = await client.post("/api/calendars", json={"name": "X", "link": "ftp://a"},
                                headers=headers)  # fmt: skip
        assert bad.status == 400 and await bad.text() == "web.cal_bad_link"
    finally:
        await client.close()
        app.db.close()

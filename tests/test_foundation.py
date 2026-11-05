from __future__ import annotations

import asyncio
import json
import logging
import re
import string
import subprocess
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path

import pytest

from pi_guard.core.events import Event, EventBus, EventType
from pi_guard.core.lifecycle import Backoff, ComponentState, supervise
from pi_guard.core.timeutil import TimeWindow, parse_duration
from pi_guard.i18n import LANGUAGES, catalog, format_duration, t
from pi_guard.logs import RedactingFormatter
from pi_guard.security.ratelimit import RateLimiter
from pi_guard.security.redact import mask_sender, redact
from pi_guard.storage.db import Database

from .conftest import TOKEN


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_catalogs_have_identical_keys_and_placeholders() -> None:
    en, de = catalog("en"), catalog("de")
    assert set(en) == set(de), set(en) ^ set(de)
    for key in en:
        assert _placeholders(en[key]) == _placeholders(de[key]), key
        assert de[key].strip(), key


def test_catalogs_are_valid_json_without_duplicates() -> None:
    for lang in LANGUAGES:
        text = resources.files("pi_guard.i18n").joinpath(f"{lang}.json").read_text("utf-8")

        def reject_duplicates(pairs: list[tuple[str, str]]) -> dict[str, str]:
            keys = [k for k, _ in pairs]
            assert len(keys) == len(set(keys)), [k for k in keys if keys.count(k) > 1]
            return dict(pairs)

        json.loads(text, object_pairs_hook=reject_duplicates)


def test_translation_and_fallback() -> None:
    assert t("unit.minutes", "de", n=5) == "5 Minuten"
    assert t("unit.minutes", "fr", n=5) == "5 minutes"
    assert format_duration(30, "en") == "30 seconds"
    assert format_duration(7200, "de") == "2 Stunden"


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30", 1800), ("30m", 1800), ("2h", 7200), ("2std", 7200), ("1d", 86400), ("45s", 45)],
)
def test_parse_duration(text: str, seconds: int) -> None:
    assert parse_duration(text) == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["", "abc", "0", "-5m", "5y"])
def test_parse_duration_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


def test_time_window_wraps_midnight() -> None:
    window = TimeWindow.parse("22:00-07:00")
    at = lambda h, m=0: datetime(2026, 1, 1, h, m)  # noqa: E731
    assert window.contains(at(23))
    assert window.contains(at(6, 59))
    assert not window.contains(at(7))
    assert not window.contains(at(12))
    day = TimeWindow.parse("08:00-18:00")
    assert day.contains(at(8)) and not day.contains(at(18))
    with pytest.raises(ValueError):
        TimeWindow.parse("8-18")


def test_redaction_covers_messages_and_tracebacks() -> None:
    assert TOKEN not in redact(f"POST https://api.telegram.org/bot{TOKEN}/getMe")
    formatter = RedactingFormatter(logging.Formatter("%(message)s"), ["hunter2"])
    record = logging.LogRecord("x", logging.INFO, "", 0, "pw=%s", ("hunter2",), None)
    assert "hunter2" not in formatter.format(record)
    try:
        raise RuntimeError(f"https://api.telegram.org/bot{TOKEN}/getUpdates failed")
    except RuntimeError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, "", 0, "boom", None, sys.exc_info())
    text = formatter.format(record)
    assert "getUpdates failed" in text and TOKEN not in text


def test_phone_numbers_are_masked_in_logs() -> None:
    assert mask_sender("signal:+4915112345678") == "signal:+491…78"
    assert mask_sender("telegram:1001") == "telegram:1001"


def test_rate_limiter() -> None:
    limiter = RateLimiter(per_minute=60, burst=2)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=0)
    assert not limiter.allow("a", now=0)
    assert limiter.allow("b", now=0)
    assert limiter.allow("a", now=1.0)


async def test_event_bus_drops_oldest_when_full() -> None:
    bus = EventBus()
    queue = bus.subscribe("slow", maxsize=2)
    for camera in ("a", "b", "c"):
        bus.publish(Event(EventType.MOTION_DETECTED, camera=camera))
    assert [queue.get_nowait().camera for _ in range(2)] == ["b", "c"]


async def test_event_bus_handler_errors_are_isolated() -> None:
    bus = EventBus()
    seen: list[str | None] = []

    async def handler(event: Event) -> None:
        if event.camera == "bad":
            raise RuntimeError("boom")
        seen.append(event.camera)

    task = asyncio.create_task(bus.consume(bus.subscribe("test"), "test", handler))
    await asyncio.sleep(0)
    bus.publish(Event(EventType.MOTION_DETECTED, camera="bad"))
    bus.publish(Event(EventType.MOTION_DETECTED, camera="good"))
    await asyncio.sleep(0.01)
    task.cancel()
    assert seen == ["good"]


async def test_supervisor_restarts_crashed_component() -> None:
    runs = 0

    async def flaky() -> None:
        nonlocal runs
        runs += 1
        if runs < 3:
            raise RuntimeError("fail")
        await asyncio.sleep(10)

    state = ComponentState("flaky")
    task = asyncio.create_task(supervise(state, flaky, Backoff(initial=0.001, maximum=0.001)))
    await asyncio.sleep(0.05)
    task.cancel()
    assert runs == 3
    assert state.restarts == 2
    assert "fail" in (state.last_error or "")


def test_database_kv_and_events(db: Database) -> None:
    db.set("x", {"a": 1})
    assert db.get("x") == {"a": 1}
    now = datetime.now(UTC)
    db.add_event(now - timedelta(days=10), "motion_detected", "cam", {})
    db.add_event(now, "motion_detected", "cam", {"area": 3})
    assert [e.data for e in db.recent_events()] == [{"area": 3}, {}]
    assert db.prune(now - timedelta(days=1)) == 1
    assert db.check() == "ok"


def test_database_file_is_private(db: Database) -> None:
    assert db.path.stat().st_mode & 0o077 == 0


def test_build_output_never_enters_the_repository() -> None:
    """It happened twice: a new build folder that .gitignore did not know yet."""
    root = Path(__file__).parents[1]
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    )
    folders = {"target", "dist", "dist-pack", "node_modules", "__pycache__"}
    wrong = [f for f in tracked.stdout.splitlines() if folders & set(Path(f).parts)]
    assert not wrong, wrong[:5]
    secrets = [
        f for f in tracked.stdout.splitlines() if f.endswith((".pem", ".sk", ".jks", ".keystore"))
    ]
    assert not secrets, secrets[:5]


def test_root_jobs_never_write_where_pi_guard_can() -> None:
    """A link left there would make root overwrite any file."""
    root = Path(__file__).parents[1]
    jobs = [root / "deploy/wireguard/pi-guard-wg-status", root / "scripts/update.sh"]
    for job in jobs:
        for line in job.read_text().splitlines():
            if ">" in line and not line.lstrip().startswith("#"):
                assert not re.search(r'>\s*"?\$(STATE|DATA)\b', line), line
                assert "/run/pi-guard/" not in line and "/var/lib/pi-guard/" not in line, line
    assert "DIR=/run/pi-guard-status" in jobs[0].read_text()
    server = (root / "vpn/src/server.rs").read_text().split("#[cfg(test)]")[0]
    for line in server.splitlines():
        if "fs::write" in line or "fs::copy" in line or "File::create" in line:
            assert "DATA" not in line, line

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pi_guard.config.files import FileConfig, NotificationRule, NotificationSettings
from pi_guard.core.events import Event, EventType
from pi_guard.core.modes import Mode
from pi_guard.notifications import engine as engine_module
from pi_guard.notifications.rules import resolve

from .helpers import ADMIN, VIEWER, FakeProvider, camera, fake_snapshot, make_app


def motion(ts: datetime | None = None) -> Event:
    return Event(EventType.MOTION_DETECTED, camera="living_room", ts=ts or datetime.now(UTC))


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = make_app(tmp_path)
    fake_snapshot(application)
    yield application
    application.db.close()


def sent_to(app) -> list[str]:  # type: ignore[no-untyped-def]
    return [chat for chat, _ in app.providers["telegram"].sent]


async def flush(app) -> None:  # type: ignore[no-untyped-def]
    while not app.notifier._queue.empty():
        await app.notifier._deliver(app.notifier._queue.get_nowait())


async def test_motion_is_silent_at_home(app) -> None:  # type: ignore[no-untyped-def]
    await app.notifier.handle(motion())
    await flush(app)
    assert sent_to(app) == []


async def test_motion_alerts_everyone_when_away(app) -> None:  # type: ignore[no-untyped-def]
    app.modes.set_manual(Mode.AWAY)
    await app.notifier.handle(motion())
    await flush(app)
    assert sorted(sent_to(app)) == [ADMIN, VIEWER]
    reply = app.providers["telegram"].last()
    assert reply.photo is not None and reply.photo.exists()
    commands = [b.command for row in reply.buttons for b in row]
    assert "video living_room 30" in commands and "home" in commands


async def test_cooldown_per_camera(app) -> None:  # type: ignore[no-untyped-def]
    app.modes.set_manual(Mode.AWAY)
    rule = app.notifier.rule(EventType.MOTION_DETECTED)
    assert app.notifier.should_send(motion(), rule, now=1000)
    assert not app.notifier.should_send(motion(), rule, now=1060)
    assert app.notifier.should_send(motion(), rule, now=1121)


async def test_quiet_hours_suppress_normal_but_not_critical(tmp_path: Path) -> None:
    file = FileConfig(
        cameras=[camera()], notifications=NotificationSettings(quiet_hours="22:00-07:00")
    )
    app = make_app(tmp_path, file=file, timezone="UTC")
    app.modes.set_manual(Mode.AWAY)
    night = datetime(2026, 10, 30, 23, 0, tzinfo=UTC)
    notifier = app.notifier
    assert not notifier.should_send(motion(night), notifier.rule(EventType.MOTION_DETECTED))
    storage = Event(EventType.STORAGE_LOW, ts=night)
    assert notifier.should_send(storage, notifier.rule(EventType.STORAGE_LOW))
    app.db.close()


async def test_hourly_cap_spares_critical(tmp_path: Path) -> None:
    file = FileConfig(
        cameras=[camera()],
        notifications=NotificationSettings(
            max_per_hour=2, rules={"motion_detected": NotificationRule(cooldown_seconds=0)}
        ),
    )
    app = make_app(tmp_path, file=file)
    app.modes.set_manual(Mode.AWAY)
    n = app.notifier
    rule = n.rule(EventType.MOTION_DETECTED)
    assert [n.should_send(motion(), rule, now=float(i)) for i in range(3)] == [True, True, False]
    assert n.should_send(
        Event(EventType.RECORDING_FAILED), n.rule(EventType.RECORDING_FAILED), now=3.0
    )
    app.db.close()


async def test_technical_alerts_go_to_admins_in_any_mode(app) -> None:  # type: ignore[no-untyped-def]
    await app.notifier.handle(Event(EventType.CAMERA_DISCONNECTED, camera="living_room"))
    await flush(app)
    assert sent_to(app) == [ADMIN]
    assert "not been reachable" in app.providers["telegram"].last().text


async def test_users_who_opted_out_are_skipped(app) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    viewer = app.users.get("telegram", VIEWER)
    app.users.save(replace(viewer, notifications=False))
    app.modes.set_manual(Mode.AWAY)
    await app.notifier.handle(motion())
    await flush(app)
    assert sent_to(app) == [ADMIN]


async def test_mode_change_not_echoed_to_actor(app) -> None:  # type: ignore[no-untyped-def]
    event = Event(
        EventType.MODE_CHANGED,
        data={
            "mode": "AWAY",
            "previous": "HOME",
            "selection": "MANUAL_OVERRIDE",
            "actor": f"telegram:{ADMIN}",
        },
    )
    await app.notifier.handle(event)
    await flush(app)
    assert sent_to(app) == [VIEWER]
    assert '"Away"' in app.providers["telegram"].last().text


async def test_german_recipient_gets_german_text(tmp_path: Path) -> None:
    app = make_app(tmp_path, language="de")
    await app.notifier.handle(Event(EventType.SYSTEM_STARTED, data={"version": "1.0"}))
    await flush(app)
    assert app.providers["telegram"].last().text == "Pi-Guard gestartet"
    app.db.close()


async def test_unavailable_provider_is_retried(app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(engine_module, "RETRY_DELAYS", (0.01, 0.01))
    provider: FakeProvider = app.providers["telegram"]
    provider.fail = True
    await app.notifier.handle(Event(EventType.SYSTEM_STARTED, data={"version": "1"}))
    await flush(app)
    assert provider.sent == []
    provider.fail = False
    await asyncio.sleep(0.05)
    await flush(app)
    assert sent_to(app) == [ADMIN]


async def test_notifier_never_blocks_publisher(app) -> None:  # type: ignore[no-untyped-def]
    app.providers["telegram"].fail = True
    for _ in range(engine_module.QUEUE_SIZE + 50):
        app.notifier._enqueue(
            engine_module.Delivery(
                engine_module.Recipient("telegram", ADMIN, "en"),
                app.notifier.message(
                    Event(EventType.SYSTEM_STARTED, data={"version": "1"}),
                    "en",
                    None,
                    app.notifier.rule(EventType.SYSTEM_STARTED),
                ),
            )
        )
    assert app.notifier._queue.qsize() == engine_module.QUEUE_SIZE


def test_rule_overrides() -> None:
    rule = resolve(
        EventType.MOTION_DETECTED,
        {"motion_detected": NotificationRule(modes=["HOME", "AWAY"], attach="clip")},
    )
    assert rule.modes == (Mode.HOME, Mode.AWAY)
    assert rule.attach == "clip"
    assert rule.cooldown_seconds == 120


def test_every_event_type_has_a_rule_and_text() -> None:
    from pi_guard.i18n import catalog
    from pi_guard.notifications.rules import DEFAULTS

    for event_type in EventType:
        assert event_type in DEFAULTS
        assert f"notify.{event_type.value}" in catalog("en")
        assert f"event.{event_type.value}" in catalog("de")


async def test_startup_event_reaches_consumers(tmp_path: Path) -> None:
    app = make_app(tmp_path, cameras=[])
    task = asyncio.create_task(app.run())
    for _ in range(50):
        await asyncio.sleep(0.02)
        if app.providers["telegram"].sent:
            break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert "Pi-Guard started" in app.providers["telegram"].texts()[0]

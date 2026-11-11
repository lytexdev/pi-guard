from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from pi_guard.core.events import Event, EventType
from pi_guard.core.modes import Mode
from pi_guard.core.timeutil import utcnow

from .helpers import ADMIN, VIEWER, fake_snapshot, make_app, msg

CONTACT = "5005"


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = make_app(
        tmp_path,
        emergency_telegram_user_ids=[int(CONTACT)],
        emergency_after_alerts=2,
        emergency_after_minutes=10,
    )
    fake_snapshot(application)
    application.modes.set_manual(Mode.AWAY)
    yield application
    application.db.close()


async def alert(app, minutes_ago: float = 0) -> None:  # type: ignore[no-untyped-def]
    event = Event(
        EventType.MOTION_DETECTED,
        camera="living_room",
        ts=utcnow() - timedelta(minutes=minutes_ago),
    )
    app.notifier._last_sent.clear()  # no cooldown between test alerts
    await app.notifier.handle(event)


def sent_to(app, chat: str) -> list:  # type: ignore[no-untyped-def]
    return [reply for c, reply in app.providers["telegram"].sent if c == chat]


async def test_sent_alerts_are_open_and_have_ack_button(app) -> None:  # type: ignore[no-untyped-def]
    await alert(app)
    while not app.notifier._queue.empty():
        await app.notifier._deliver(app.notifier._queue.get_nowait())
    assert len(app.escalation.open_alerts()) == 1
    commands = [b.command for row in sent_to(app, ADMIN)[0].buttons for b in row]
    assert "ok" in commands


async def test_alerts_at_home_are_not_tracked(app) -> None:  # type: ignore[no-untyped-def]
    app.modes.set_manual(Mode.HOME)
    await alert(app)
    assert app.escalation.open_alerts() == []


async def test_escalation_thresholds(app) -> None:  # type: ignore[no-untyped-def]
    await alert(app, minutes_ago=5)
    assert app.escalation.due() == []  # one alert only
    await alert(app, minutes_ago=4)
    assert app.escalation.due() == []  # not old enough
    assert len(app.escalation.due(utcnow() + timedelta(minutes=6))) == 2


async def test_acknowledging_prevents_escalation(app) -> None:  # type: ignore[no-untyped-def]
    await alert(app, minutes_ago=30)
    await alert(app, minutes_ago=20)
    await app.messenger.handle(msg("ok", VIEWER, from_button=True))
    assert "2 alert" in app.providers["telegram"].last().text
    assert app.escalation.due() == []


async def test_i_am_home_acknowledges(app) -> None:  # type: ignore[no-untyped-def]
    await alert(app, minutes_ago=30)
    await app.messenger.handle(msg("Ich bin wieder da", VIEWER))
    assert app.escalation.open_alerts() == []


async def test_escalation_messages_contacts_once(app) -> None:  # type: ignore[no-untyped-def]
    await alert(app, minutes_ago=30)
    await alert(app, minutes_ago=20)
    await app.escalation.escalate(app.escalation.due())
    to_contact = sent_to(app, CONTACT)
    assert "2 alert" in to_contact[0].text and "112" in to_contact[0].text
    assert any(r.photo is not None for r in to_contact[1:])
    assert any("emergency contact" in r.text for r in sent_to(app, ADMIN))
    assert app.escalation.due() == []  # already escalated, nothing new
    await alert(app, minutes_ago=15)
    assert len(app.escalation.due()) == 3  # a new alert extends the escalation


async def test_emergency_contact_gets_explanation(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/start", CONTACT))
    assert "emergency contact" in app.providers["telegram"].last().text


async def test_each_contact_gets_what_was_chosen_for_it(app) -> None:  # type: ignore[no-untyped-def]
    contacts = app.escalation.contacts
    contacts.save(None, {"name": "Words", "share": {"escalation": []},
                         "channels": [{"provider": "telegram", "address": "6006"}]})  # fmt: skip
    contacts.save(None, {"name": "Not here", "share": {"sos": ["location"]},
                         "channels": [{"provider": "telegram", "address": "7007"}]})  # fmt: skip
    assert app.escalation.is_contact("telegram", "6006")
    assert {c for _, c, _ in contacts.recipients("escalation")} == {CONTACT, "6006"}
    for minutes in (20, 15):
        await alert(app, minutes_ago=minutes)
    await app.escalation.escalate(app.escalation.due())
    old, words = sent_to(app, CONTACT), sent_to(app, "6006")
    assert any(r.photo for r in old) and "•" in old[0].text  # the old contact: all as before
    assert len(words) == 1 and words[0].photo is None and "•" not in words[0].text
    assert sent_to(app, "7007") == []


def test_contacts_are_checked() -> None:
    from pi_guard.notifications.contacts import DEFAULT_SHARE, ContactError, clean

    with pytest.raises(ContactError, match="name"):
        clean({"channels": [{"provider": "signal", "address": "+491700000"}]})
    with pytest.raises(ContactError, match="channel"):
        clean({"name": "A"})
    for provider, address in (("signal", "0170"), ("telegram", "abc"), ("email", "x@"),
                              ("sms", "+491700000")):  # fmt: skip
        with pytest.raises(ContactError, match="address"):
            clean({"name": "A", "channels": [{"provider": provider, "address": address}]})
    twice = [{"provider": "email", "address": "a@b.de"}] * 2
    share = {"sos": ["location", "medical", "nonsense"]}
    made = clean({"name": " A  B ", "channels": twice, "share": share, "medical_mode": "x"})
    assert made["name"] == "A B" and made["channels"] == [["email", "a@b.de"]]
    assert made["share"] == {"sos": ["location", "medical"]} and made["medical_mode"] == "link"
    fresh = clean({"name": "A", "channels": [{"provider": "email", "address": "a@b.de"}]})
    assert fresh["share"] == DEFAULT_SHARE and "medical" not in fresh["share"]["sos"]

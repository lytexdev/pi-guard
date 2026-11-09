from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from pi_guard.core.modes import Mode, Selection
from pi_guard.core.permissions import Role
from pi_guard.messaging.commands import build_registry
from pi_guard.messaging.commands.registry import Command, Registry

from .helpers import (
    ADMIN,
    STRANGER,
    VIEWER,
    FakeProvider,
    camera,
    fake_snapshot,
    make_app,
    msg,
)


async def _noop(ctx, args):  # type: ignore[no-untyped-def]
    raise AssertionError


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = make_app(tmp_path)
    yield application
    application.db.close()


def provider(app) -> FakeProvider:  # type: ignore[no-untyped-def]
    return app.providers["telegram"]  # type: ignore[no-any-return]


# --- registry and parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "/video 30",
        "/v 30",
        "video 30",
        "V 30",
        "  /VIDEO   30 ",
        "/video@TestBot 30",
        "/clip 30",
        "film 30",
    ],
)
def test_video_aliases_resolve_to_one_command(text: str) -> None:
    parsed = build_registry().parse(text, "TestBot")
    assert parsed is not None
    assert parsed.command.name == "video"
    assert parsed.tokens == ("30",)


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("/foto", "photo"),
        ("p", "photo"),
        ("/abwesend", "away"),
        ("zuhause", "home"),
        ("/s", "status"),
        ("Menü", "menu"),
        ("hilfe", "help"),
        ("ｐｈｏｔｏ", "photo"),
    ],
)
def test_translated_and_unicode_aliases(text: str, name: str) -> None:
    parsed = build_registry().parse(text)
    assert parsed is not None and parsed.command.name == name


def test_commands_for_other_bots_and_chatter_are_ignored() -> None:
    registry = build_registry()
    assert registry.parse("/photo@OtherBot", "TestBot") is None
    assert registry.parse("hello there") is None
    assert registry.parse("") is None


def test_alias_collision_is_rejected() -> None:
    registry = Registry()
    registry.add(Command("photo", _noop, ("p",)))
    with pytest.raises(ValueError, match="collides"):
        registry.add(Command("play", _noop, ("P",)))


def test_invalid_telegram_command_name_rejected() -> None:
    with pytest.raises(ValueError, match="Telegram"):
        Registry().add(Command("Foto-Ansicht", _noop))


def test_full_registry_builds_without_collisions() -> None:
    names = [c.name for c in build_registry()]
    assert len(names) == len(set(names))
    assert {"photo", "video", "status", "home", "away", "mode", "menu"} <= set(names)


# --- authorization ---------------------------------------------------------------------


async def test_stranger_is_rejected_once(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/photo", STRANGER))
    await app.messenger.handle(msg("/photo", STRANGER))
    sent = provider(app).sent
    assert len(sent) == 1
    assert "not authorised" in sent[0][1].text
    assert sent[0][1].photo is None


async def test_viewer_cannot_run_admin_commands(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/invite", VIEWER))
    assert "administrator" in provider(app).last().text


async def test_group_messages_need_allowed_chat(tmp_path: Path) -> None:
    app = make_app(tmp_path, telegram_allowed_chat_ids=[-100])
    fake_snapshot(app)
    await app.messenger.handle(msg("/status", ADMIN, chat="-999", is_group=True))
    assert provider(app).sent == []
    await app.messenger.handle(msg("status", ADMIN, chat="-100", is_group=True))
    assert provider(app).sent == []  # plain text is off in groups by default
    await app.messenger.handle(msg("/status", ADMIN, chat="-100", is_group=True))
    assert provider(app).sent[0][0] == "-100"
    app.db.close()


async def test_invite_flow(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/invite", ADMIN))
    text = provider(app).last().text
    assert "https://t.me/TestBot?start=" in text
    code = text.rsplit(" ", 1)[-1]
    await app.messenger.handle(msg(f"/start {code}", STRANGER, sender_name="Oma"))
    user = app.users.get("telegram", STRANGER)
    assert user is not None and user.role is Role.VIEWER and user.name == "Oma"
    assert any("Welcome" in t for t in provider(app).texts())
    # single use
    await app.messenger.handle(msg(code, "4004"))
    assert app.users.get("telegram", "4004") is None
    assert "not valid" in provider(app).last().text


async def test_configured_admin_cannot_be_removed(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg(f"/removeuser telegram:{VIEWER}", ADMIN))
    assert "cannot be removed" in provider(app).last().text


async def test_rate_limit(app) -> None:  # type: ignore[no-untyped-def]
    for _ in range(30):
        await app.messenger.handle(msg("/help", VIEWER))
    assert len(provider(app).sent) < 15


# --- everyday commands -----------------------------------------------------------------


async def test_simple_menu_in_german(tmp_path: Path) -> None:
    app = make_app(tmp_path, language="de")
    await app.messenger.handle(msg("/start", VIEWER))
    reply = provider(app).last()
    assert "Ihre Kamera funktioniert." in reply.text
    assert "Die Aufzeichnung ist eingeschaltet." in reply.text
    labels = [b.label for row in reply.buttons for b in row]
    assert labels == [
        "Foto ansehen",
        "Video ansehen",
        "Kamerastatus",
        "Benachrichtigungen",
        "Hilfe",
    ]
    app.db.close()


async def test_admin_menu_has_more(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("menu", ADMIN))
    commands = [b.command for row in provider(app).last().buttons for b in row]
    assert "more" in commands


async def test_photo_single_camera_needs_no_choice(app) -> None:  # type: ignore[no-untyped-def]
    fake_snapshot(app)
    await app.messenger.handle(msg("foto", VIEWER))
    reply = provider(app).last()
    assert reply.photo is not None and reply.photo.exists()
    assert "Living Room" in reply.text


async def test_photo_unavailable_is_explained(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/photo", VIEWER))
    assert "No picture" in provider(app).last().text


async def test_multiple_cameras_offer_a_choice(tmp_path: Path) -> None:
    app = make_app(tmp_path, cameras=[camera(), camera("entrance", "Entrance")])
    await app.messenger.handle(msg("/photo", VIEWER))
    commands = [b.command for row in provider(app).last().buttons for b in row]
    assert commands[:2] == ["photo living_room", "photo entrance"]
    fake_snapshot(app, "entrance")
    await app.messenger.handle(msg("/photo ent", VIEWER))
    assert provider(app).last().photo is not None
    app.db.close()


async def test_argument_errors_are_friendly(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/video 500", VIEWER))
    assert "between 3 and 120" in provider(app).last().text
    await app.messenger.handle(msg("/video kitchen", VIEWER))
    assert "did not understand" in provider(app).last().text


async def test_unknown_text_points_to_menu(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("what is going on", VIEWER))
    reply = provider(app).last()
    assert reply.buttons[0][0].command == "menu"


async def test_notifications_toggle_persists(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("benachrichtigungen aus", VIEWER))
    assert app.users.get("telegram", VIEWER).notifications is False
    await app.messenger.handle(msg("/notifications on", VIEWER))
    assert app.users.get("telegram", VIEWER).notifications is True


async def test_language_switch(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/language de", VIEWER))
    await app.messenger.handle(msg("/status", VIEWER))
    assert "Aufzeichnung" in provider(app).last().text


async def test_handler_crash_is_contained(app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def boom(*a):  # type: ignore[no-untyped-def]
        raise RuntimeError("x")

    monkeypatch.setattr(app, "take_photo", boom)
    await app.messenger.handle(msg("/photo", VIEWER))
    assert "went wrong" in provider(app).last().text


# --- modes ------------------------------------------------------------------------------


async def test_manual_mode_commands(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("/abwesend", VIEWER))
    assert app.modes.effective is Mode.AWAY
    await app.messenger.handle(msg("home 2h", VIEWER))
    state = app.modes.state
    assert state.effective is Mode.HOME and state.selection is Selection.MANUAL_OVERRIDE
    assert state.manual_until is not None
    assert timedelta(minutes=119) < state.manual_until - app.clock() <= timedelta(hours=2)
    await app.messenger.handle(msg("/mode auto", VIEWER))
    assert "not set up" in provider(app).last().text


# --- providers without buttons ----------------------------------------------------------


async def test_numbered_menu_for_signal(tmp_path: Path) -> None:
    signal = FakeProvider("signal", buttons=False)
    app = make_app(
        tmp_path,
        providers={"signal": signal},
        telegram_enabled=False,
        signal_enabled=True,
        signal_phone_number="+491510000000",
        signal_admin_numbers=["+491511111111"],
    )
    fake_snapshot(app)
    await app.messenger.handle(msg("menu", "+491511111111", provider="signal"))
    await app.messenger.handle(msg("1", "+491511111111", provider="signal"))
    assert signal.last().photo is not None
    app.db.close()


async def test_help_shows_credit(app) -> None:  # type: ignore[no-untyped-def]
    await app.messenger.handle(msg("hilfe", VIEWER))
    assert "made by lytex" in provider(app).last().text
    await app.messenger.handle(msg("/layout advanced", VIEWER))
    await app.messenger.handle(msg("/help", VIEWER))
    assert "made by lytex" in provider(app).last().text


async def test_strangers_get_an_answer_right_after_a_restart(app, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The clock behind the limit starts at boot, so a fresh Pi has not "just answered"."""
    monkeypatch.setattr("pi_guard.messaging.hub.time.monotonic", lambda: 60.0)
    await app.messenger.handle(msg("/photo", STRANGER))
    assert len(provider(app).sent) == 1


async def test_strangers_hear_back_on_telegram_but_not_on_signal(tmp_path: Path) -> None:
    signal = FakeProvider("signal", buttons=False)
    signal.answers_strangers = False  # type: ignore[attr-defined]
    app = make_app(tmp_path, providers={"telegram": FakeProvider(), "signal": signal})
    await app.messenger.handle(msg("hallo", STRANGER))
    assert "ID" in app.providers["telegram"].last().text
    await app.messenger.handle(msg("hallo", "+491701234567", provider="signal"))
    assert signal.sent == []  # a friend writing to the owner's own number gets no bot answer
    assert app.db.execute("SELECT COUNT(*) FROM audit WHERE action = 'rejected'").fetchone()[0] == 2
    app.db.close()


def test_the_signal_provider_stays_quiet_to_strangers() -> None:
    from pi_guard.messaging.signal.provider import SignalProvider

    assert SignalProvider.answers_strangers is False

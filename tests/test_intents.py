from __future__ import annotations

from pathlib import Path

import pytest

from pi_guard.core.modes import Mode
from pi_guard.messaging.intents import understand

from .helpers import VIEWER, camera, fake_snapshot, make_app, msg

CAMERAS = {"living_room": "Wohnzimmer", "entrance": "Eingang"}


@pytest.mark.parametrize(
    ("sentence", "command"),
    [
        ("Kannst du mir bitte ein Foto schicken?", "photo"),
        ("Zeig mal", "photo"),
        ("Bild vom Eingang bitte", "photo entrance"),
        ("Schick mir ein Video von 30 Sekunden", "video 30"),
        ("zeig mir ein video, eine minute", "video 60"),
        ("Das Bild ist so unscharf", "sharper"),
        ("Kannst du das vergrößern?", "zoom"),
        ("Das ist viel zu dunkel", "brighter"),
        ("Ich bin wieder da", "home"),
        ("Ich bin jetzt zuhause", "home"),
        ("Ich gehe jetzt weg", "away"),
        ("Wir fahren in den Urlaub", "away"),
        ("Funktioniert alles?", "status"),
        ("Was ist passiert?", "events"),
        ("Hilfe", "help"),
        ("Can you send me a picture?", "photo"),
        ("I'm leaving", "away"),
    ],
)
def test_sentences(sentence: str, command: str) -> None:
    assert understand(sentence, CAMERAS).command == command


def test_unclear_sentences_ask_back() -> None:
    assert understand("Foto oder Video?", CAMERAS).options == ("video", "photo")
    negated = understand("Ich gehe heute nicht weg", CAMERAS)
    assert negated.command is None and negated.options == ("home", "away")


def test_courtesy() -> None:
    assert understand("Danke schön!", CAMERAS).command == "thanks"
    assert understand("Guten Morgen!", CAMERAS).command == "menu"
    assert understand("Hallo, danke!", CAMERAS).command == "thanks"


@pytest.mark.parametrize("sentence", ["Wie spät ist es", "/foto", "", "alarm", "Schlaf gut"])
def test_nothing_is_guessed(sentence: str) -> None:
    assert understand(sentence, CAMERAS).command is None


async def test_sentence_runs_the_command(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    fake_snapshot(app)
    await app.messenger.handle(msg("Kannst du mir ein Foto schicken?", VIEWER))
    assert app.providers["telegram"].last().photo is not None
    await app.messenger.handle(msg("Ich gehe jetzt weg", VIEWER))
    assert app.modes.effective is Mode.AWAY
    app.db.close()


async def test_ambiguous_sentence_offers_buttons(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    await app.messenger.handle(msg("Ich gehe nicht weg", VIEWER))
    reply = app.providers["telegram"].last()
    assert [b.command for row in reply.buttons for b in row] == ["home", "away", "menu"]
    assert app.modes.effective is Mode.HOME
    app.db.close()


async def test_groups_need_real_commands(tmp_path: Path) -> None:
    app = make_app(
        tmp_path, cameras=[camera()], telegram_allowed_chat_ids=[-5], telegram_group_plain_text=True
    )
    await app.messenger.handle(msg("Ich gehe jetzt weg", VIEWER, chat="-5", is_group=True))
    assert app.modes.effective is Mode.HOME
    app.db.close()

"""The tutorial: the same chapters for the dashboard, the chats and the assistant."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pi_guard import tutorial
from pi_guard.i18n import catalog

from .helpers import ADMIN, make_app, msg
from .test_addons import make_assistant

STATIC = Path(__file__).parents[1] / "src/pi_guard/web/static/js/views/help.js"


def test_dashboard_and_chats_have_the_same_chapters() -> None:
    shown = re.findall(r'\["(\w+)", "(#/[\w/]+)", "[\w-]+"\]', STATIC.read_text())
    assert shown == [(c, route) for c, route, _ in tutorial.CHAPTERS]
    for lang in ("en", "de"):
        for chapter, _, _ in tutorial.CHAPTERS:
            assert f"web.tut_{chapter}_text" in catalog(lang)


def test_questions_about_pi_guard_get_their_chapter() -> None:
    assert "Kid Protect" in (tutorial.answer("Wie funktioniert Kid Protect?", "de") or "")
    assert "Umzug" in (tutorial.answer("Wie geht ein Umzug mit neuem Router?", "de") or "")
    assert (tutorial.answer("Gibt es ein Tutorial?", "de") or "").count("•") == len(
        tutorial.CHAPTERS
    )
    assert tutorial.answer(
        "How do I use the VPN?",
        "en",
    ).startswith("VPN")  # type: ignore[union-attr]
    assert tutorial.answer("Wie wird das Wetter morgen?", "de") is None
    assert tutorial.answer("Kinder", "de") is None  # a word alone is no question about it
    assert tutorial.answer("Wie funktioniert Happy Hour?", "de") is None  # "app" only as a word


async def test_the_assistant_answers_from_the_tutorial_without_ai(db: Any, tmp_path: Path) -> None:
    assistant = make_assistant(db, tmp_path, key=None)
    answer = await assistant.ask("Wie funktioniert der Umzug?", "de", "c1")
    assert answer.source == "tutorial" and answer.tokens == 0 and "Umzug" in answer.text


async def test_the_chat_command_sends_the_tutorial(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    try:
        await app.messenger.handle(msg("/tutorial", ADMIN))
        text = app.providers["telegram"].sent[-1][1].text
        assert "Kid Protect" in text and ("Notfallkontakte" in text or "Emergency contacts" in text)
    finally:
        app.db.close()

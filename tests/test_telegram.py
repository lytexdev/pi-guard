"""TelegramProvider against a fake bot; no network access."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from telegram import Bot, CallbackQuery, Update
from telegram.error import NetworkError

from pi_guard.messaging.base import Button, Incoming, ProviderUnavailableError, Reply
from pi_guard.messaging.telegram.provider import CAPTION_LIMIT, TelegramProvider

TOKEN = "123456789:AAH" + "x" * 32


class FakeBot:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        async def method(*args: Any, **kwargs: Any) -> None:
            if self.fail:
                raise self.fail
            self.calls.append((name, {"args": args, **kwargs}))

        return method


class FakeApplication:
    def __init__(self, bot: FakeBot) -> None:
        self.bot = bot


def provider(bot: FakeBot | None = None) -> tuple[TelegramProvider, FakeBot]:
    p = TelegramProvider(TOKEN, Path("/tmp"), 1000, lambda uid: uid == "1", {})
    fake = bot or FakeBot()
    p._application = FakeApplication(fake)  # type: ignore[assignment]
    return p, fake


async def test_text_with_buttons() -> None:
    p, bot = provider()
    await p.send("42", Reply("Hi", buttons=((Button("Photo", "photo"), Button("Video", "video")),)))
    name, call = bot.calls[0]
    assert name == "send_message" and call["args"] == (42, "Hi")
    keyboard = call["reply_markup"].inline_keyboard
    assert [[b.callback_data for b in row] for row in keyboard] == [["photo", "video"]]


async def test_photo_with_caption(tmp_path: Path) -> None:
    p, bot = provider()
    photo = tmp_path / "x.jpg"
    photo.write_bytes(b"jpg")
    await p.send("42", Reply("Living Room, 14:32", photo=photo))
    assert [n for n, _ in bot.calls] == ["send_photo"]
    assert bot.calls[0][1]["caption"] == "Living Room, 14:32"


async def test_long_text_is_sent_before_media(tmp_path: Path) -> None:
    p, bot = provider()
    video = tmp_path / "x.mp4"
    video.write_bytes(b"mp4")
    await p.send("42", Reply("x" * (CAPTION_LIMIT + 1), video=video))
    assert [n for n, _ in bot.calls] == ["send_message", "send_video"]
    assert bot.calls[1][1]["caption"] is None


async def test_oversized_callback_data_is_dropped() -> None:
    p, bot = provider()
    await p.send("42", Reply("x", buttons=((Button("ok", "menu"), Button("long", "x" * 80)),)))
    keyboard = bot.calls[0][1]["reply_markup"].inline_keyboard
    assert [[b.text for b in row] for row in keyboard] == [["ok"]]


async def test_network_errors_become_retryable() -> None:
    p, _ = provider(FakeBot(fail=NetworkError("down")))
    with pytest.raises(ProviderUnavailableError):
        await p.send("42", Reply("x"))


async def test_not_connected() -> None:
    p = TelegramProvider(TOKEN, Path("/tmp"), 1000, lambda uid: True, {})
    with pytest.raises(ProviderUnavailableError):
        await p.send("42", Reply("x"))


def _update(data: dict[str, Any]) -> Update:
    return Update.de_json(data, Bot(TOKEN))


USER = {"id": 1, "is_bot": False, "first_name": "Anna", "language_code": "de"}


async def test_incoming_text_and_button(monkeypatch: pytest.MonkeyPatch) -> None:
    p, _ = provider()
    received: list[Incoming] = []

    async def on_message(incoming: Incoming) -> None:
        received.append(incoming)

    p._on_message = on_message
    text = _update(
        {
            "update_id": 1,
            "message": {
                "message_id": 5,
                "date": 0,
                "chat": {"id": -100, "type": "group"},
                "from": USER,
                "text": "/photo@TestBot",
            },
        }
    )
    await p._on_text(text, None)  # type: ignore[arg-type]
    assert received[0] == Incoming(
        "telegram",
        "1",
        "-100",
        "/photo@TestBot",
        is_group=True,
        sender_name="Anna",
        language_hint="de",
    )

    button = _update(
        {
            "update_id": 2,
            "callback_query": {
                "id": "9",
                "chat_instance": "c",
                "from": USER,
                "data": "video living_room 30",
                "message": {
                    "message_id": 6,
                    "date": 0,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 123456789, "is_bot": True, "first_name": "Bot"},
                    "text": "menu",
                },
            },
        }
    )
    monkeypatch.setattr(CallbackQuery, "answer", _noop)
    await p._on_callback(button, None)  # type: ignore[arg-type]
    assert received[1].from_button and received[1].text == "video living_room 30"
    assert not received[1].is_group


async def test_bots_are_ignored() -> None:
    p, _ = provider()
    received: list[Incoming] = []

    async def on_message(incoming: Incoming) -> None:
        received.append(incoming)

    p._on_message = on_message
    update = _update(
        {
            "update_id": 1,
            "message": {
                "message_id": 5,
                "date": 0,
                "chat": {"id": 7, "type": "private"},
                "from": {"id": 7, "is_bot": True, "first_name": "Other"},
                "text": "/photo",
            },
        }
    )
    await p._on_text(update, None)  # type: ignore[arg-type]
    assert received == []


async def _noop(*args: Any, **kwargs: Any) -> None:
    return None

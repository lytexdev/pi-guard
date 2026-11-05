"""Slack, Rocket.Chat, Matrix, Mastodon: what goes out, and what must not."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from pi_guard.messaging import chatapps
from pi_guard.messaging.base import ProviderUnavailableError, Reply
from pi_guard.messaging.chatapps import COLOURS, Mastodon, Matrix, Webhook, attachment

from .test_web import _client, _login


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(chatapps.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))  # fmt: skip
    return seen


def test_a_card_has_title_colour_and_text() -> None:
    card = attachment(Reply("Bewegung\nKamera: Flur", tone="alert"))
    assert card["title"] == "Bewegung" and card["text"] == "Kamera: Flur"
    assert card["color"] == COLOURS["alert"]


async def test_webhooks_get_cards_and_never_a_picture(sent: Any, tmp_path: Path) -> None:
    photo = tmp_path / "flur.jpg"
    photo.write_bytes(b"jpeg")
    await Webhook("slack", "https://hooks.example/x").send("default", Reply("Alarm", photo=photo))
    assert b"attachments" in sent[0].content and b"flur" not in sent[0].content


async def test_matrix_sends_to_the_room(sent: Any) -> None:
    await Matrix("https://m.example/", "!a:m.example", "token").send("default", Reply("Alarm"))
    assert "/rooms/%21a%3Am.example/send/m.room.message/" in str(sent[0].url)
    assert sent[0].headers["Authorization"] == "Bearer token"


async def test_mastodon_never_posts_publicly(sent: Any) -> None:
    await Mastodon("https://social.example", "token").send("default", Reply("x" * 900))
    assert b"visibility=direct" in sent[0].content and len(sent[0].content) < 700


async def test_a_failure_can_be_retried_and_keeps_the_address_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = httpx.AsyncClient

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("https://hooks.example/secret")

    monkeypatch.setattr(chatapps.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(broken), **kw))  # fmt: skip
    with pytest.raises(ProviderUnavailableError) as caught:
        await Webhook("slack", "https://hooks.example/secret").send("default", Reply("x"))
    assert "secret" not in str(caught.value)


async def test_removing_an_addon_frees_its_data(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        data = app.settings.data_dir
        (data / "knowledge.db").write_bytes(b"x" * 1000)
        catalog = await (await client.get("/api/addons")).json()
        assert next(a for a in catalog["addons"] if a["id"] == "assistant")["size"] == 1000
        gone = await client.delete("/api/addons/assistant", headers=headers)
        assert (await gone.json())["freed"] == 1000 and not (data / "knowledge.db").exists()
        assert (await client.delete("/api/addons/nope", headers=headers)).status in (404, 405)
    finally:
        await client.close()
        app.db.close()

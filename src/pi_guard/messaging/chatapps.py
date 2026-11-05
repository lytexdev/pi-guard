"""Alerts into Slack, Rocket.Chat, Matrix and Mastodon. Send-only and text-only.

Commands stay with the chat messengers. Pictures stay out on purpose: these are services of
others, often shared with a whole team, and what a camera sees in your home is not for them.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any
from urllib.parse import quote

import httpx

from pi_guard.messaging.base import MessageHandler, ProviderUnavailableError, Reply

log = logging.getLogger(__name__)

TIMEOUT = 20
COLOURS = {"info": "#5CB3FF", "warn": "#FFC857", "alert": "#FF6B6B"}
MASTODON_LIMIT = 500


def split(text: str) -> tuple[str, str]:
    title, _, rest = text.partition("\n")
    return (title, rest.strip()) if rest.strip() else ("", text)


def attachment(reply: Reply) -> dict[str, Any]:
    """The card Slack and Rocket.Chat both understand."""
    title, body = split(reply.text)
    return {"color": COLOURS.get(reply.tone, COLOURS["info"]), "title": title, "text": body,
            "fallback": reply.text, "footer": "pi-guard"}  # fmt: skip


class ChatApp:
    """One service. `deliver` turns a reply into a request."""

    supports_buttons = False
    connected = True

    def __init__(self, name: str, target: str) -> None:
        self.name = name
        self._target = target

    async def run(self, on_message: MessageHandler) -> None:
        await asyncio.Event().wait()  # nothing to receive

    async def _request(self, method: str, url: str, **kwargs: Any) -> None:
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                answer = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:  # its text would contain the secret address
            raise ProviderUnavailableError(f"{self.name}: {type(exc).__name__}") from None
        if answer.status_code >= 400:
            log.warning("%s answered %d", self.name, answer.status_code)
            raise ProviderUnavailableError(f"{self.name}: {answer.status_code}")

    async def send(self, chat_id: str, reply: Reply) -> None:
        raise NotImplementedError


class Webhook(ChatApp):
    async def send(self, chat_id: str, reply: Reply) -> None:
        await self._request("POST", self._target, json={"attachments": [attachment(reply)]})


class Matrix(ChatApp):
    def __init__(self, homeserver: str, room: str, token: str) -> None:
        super().__init__("matrix", homeserver.rstrip("/"))
        self._room, self._token = room, token

    async def send(self, chat_id: str, reply: Reply) -> None:
        url = (f"{self._target}/_matrix/client/v3/rooms/{quote(self._room, safe='')}"
               f"/send/m.room.message/{secrets.token_hex(8)}")  # fmt: skip
        message = {"msgtype": "m.text", "body": reply.text}
        await self._request("PUT", url, json=message,
                            headers={"Authorization": f"Bearer {self._token}"})  # fmt: skip


class Mastodon(ChatApp):
    """Direct messages only, so that nothing about your home ends up on a public timeline."""

    def __init__(self, instance: str, token: str) -> None:
        super().__init__("mastodon", instance.rstrip("/"))
        self._token = token

    async def send(self, chat_id: str, reply: Reply) -> None:
        await self._request(
            "POST", f"{self._target}/api/v1/statuses",
            data={"status": reply.text[:MASTODON_LIMIT], "visibility": "direct"},
            headers={"Authorization": f"Bearer {self._token}"},
        )  # fmt: skip

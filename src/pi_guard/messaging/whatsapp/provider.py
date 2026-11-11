"""WhatsApp through Meta's official Cloud API.

Sending goes out directly. Receiving needs a webhook, which Meta calls over the public
internet: `POST /webhook/whatsapp` of the dashboard, checked with the app secret. See
docs/whatsapp.md for how to reach only that path from outside.

WhatsApp allows free text only within 24 hours of the person's last message. Alerts
outside that window need an approved template (PI_GUARD_WHATSAPP_ALERT_TEMPLATE).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import mimetypes
from typing import Any

import httpx

from pi_guard.messaging.base import Incoming, MessageHandler, ProviderUnavailableError, Reply
from pi_guard.messaging.signal.provider import render

log = logging.getLogger(__name__)

API = "https://graph.facebook.com/v21.0"
TIMEOUT = 30
TEXT_LIMIT = 4000
OUTSIDE_WINDOW = 131047  # "Re-engagement message": more than 24 hours since the last message


def valid_signature(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def parse_webhook(payload: dict[str, Any]) -> list[Incoming]:
    """Text messages from a webhook call; statuses and other notifications are skipped."""
    messages = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                for c in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                sender = str(message.get("from") or "")
                text = (message.get("text") or {}).get("body") or ""
                if sender and message.get("type") == "text" and text:
                    name = names.get(sender, "")
                    messages.append(Incoming("whatsapp", sender, sender, text, sender_name=name))
    return messages


class WhatsAppProvider:
    name = "whatsapp"
    supports_buttons = False  # numbered menu, like Signal

    def __init__(self, phone_number_id: str, token: str, template: str | None = None) -> None:
        self._url = f"{API}/{phone_number_id}"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._template = template
        self._on_message: MessageHandler | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def connected(self) -> bool:
        return self._on_message is not None

    async def run(self, on_message: MessageHandler) -> None:
        self._on_message = on_message
        try:
            await asyncio.Event().wait()  # messages arrive through the webhook
        finally:
            self._on_message = None

    def received(self, payload: dict[str, Any]) -> None:
        """Called by the dashboard's webhook after the signature check."""
        if self._on_message is None:
            return
        for incoming in parse_webhook(payload):
            task = asyncio.create_task(self._on_message(incoming))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any]) -> httpx.Response:
        try:
            return await client.post(f"{self._url}/messages", headers=self._headers,
                                     json={"messaging_product": "whatsapp", **body})  # fmt: skip
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc

    async def _upload(self, client: httpx.AsyncClient, reply: Reply) -> str:
        assert reply.media is not None
        kind = mimetypes.guess_type(reply.media.name)[0] or "application/octet-stream"
        with reply.media.open("rb") as handle:
            response = await client.post(
                f"{self._url}/media", headers=self._headers,
                data={"messaging_product": "whatsapp", "type": kind},
                files={"file": (reply.media.name, handle, kind)},
            )  # fmt: skip
        response.raise_for_status()
        return str(response.json()["id"])

    async def send(self, chat_id: str, reply: Reply) -> None:
        text = render(reply)[:TEXT_LIMIT]
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if reply.media is not None:
                kind = ("image" if reply.photo else "video" if reply.video
                        else "audio" if reply.audio else "document")  # fmt: skip
                media: dict[str, Any] = {"id": await self._upload(client, reply)}
                if kind != "audio":
                    media["caption"] = text[:1000]
                if kind == "document":
                    media["filename"] = reply.media.name
                response = await self._post(client, {"to": chat_id, "type": kind, kind: media})
                if kind != "audio" or not text:
                    self._check(response)
                    return
                self._check(response)
            response = await self._post(client, {"to": chat_id, "type": "text",
                                                 "text": {"body": text}})  # fmt: skip
            if self._outside_window(response) and self._template:
                response = await self._post(client, {
                    "to": chat_id, "type": "template",
                    "template": {"name": self._template, "language": {"code": "de"}},
                })  # fmt: skip
            self._check(response)

    @staticmethod
    def _outside_window(response: httpx.Response) -> bool:
        if response.status_code < 400:
            return False
        try:
            return bool(response.json().get("error", {}).get("code") == OUTSIDE_WINDOW)
        except ValueError:
            return False

    @staticmethod
    def _check(response: httpx.Response) -> None:
        if response.status_code >= 500:
            raise ProviderUnavailableError(f"WhatsApp answered {response.status_code}")
        if response.status_code >= 400:
            raise RuntimeError(f"WhatsApp refused the message ({response.status_code})")

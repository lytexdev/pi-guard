"""Alerts and data exports by email. Send-only: commands stay with the chat messengers.

Mail is only sent encrypted to the mail server. On the way to the recipient it may not be,
so nothing is mailed unless someone switched this on.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.messaging.base import MessageHandler, ProviderUnavailableError, Reply

log = logging.getLogger(__name__)

TIMEOUT = 30
ATTACHMENT_LIMIT = 15_000_000
SUBJECT_LIMIT = 120
CHECK_SECONDS = 3600
EXPORT_KEY = "email.last_export"
EXPORT_DAYS = {"weekly": 7, "monthly": 30}


@dataclass(frozen=True)
class Mailbox:
    host: str
    port: int
    security: str  # starttls | tls
    username: str
    password: str
    sender: str


def compose(sender: str, recipient: str, reply: Reply) -> EmailMessage:
    message = EmailMessage()
    message["From"], message["To"] = sender, recipient
    first = reply.text.strip().splitlines()[0] if reply.text.strip() else "pi-guard"
    message["Subject"] = " ".join(first.split())[:SUBJECT_LIMIT]
    message.set_content(reply.text)
    media = reply.media
    if media is not None:
        if media.stat().st_size > ATTACHMENT_LIMIT:
            log.warning("file too large for email: %d bytes", media.stat().st_size)
        else:
            attach(message, media.name, media.read_bytes())
    return message


def attach(message: EmailMessage, name: str, content: bytes) -> None:
    kind = mimetypes.guess_type(name)[0] or "application/octet-stream"
    main, _, sub = kind.partition("/")
    message.add_attachment(content, maintype=main, subtype=sub, filename=name)


def deliver(box: Mailbox, message: EmailMessage) -> None:
    context = ssl.create_default_context()
    client: smtplib.SMTP
    if box.security == "tls":
        client = smtplib.SMTP_SSL(box.host, box.port, timeout=TIMEOUT, context=context)
    else:
        client = smtplib.SMTP(box.host, box.port, timeout=TIMEOUT)
    with client:
        if box.security != "tls":
            client.starttls(context=context)  # raises if the server does not offer it
        if box.username:
            client.login(box.username, box.password)
        client.send_message(message)


class EmailProvider:
    name = "email"
    supports_buttons = False

    def __init__(
        self, box: Mailbox, recipients: list[str], db: Any, interval: str,
        exporter: Callable[[], bytes | None],
    ) -> None:  # fmt: skip
        self._box = box
        self._recipients = recipients
        self._db = db
        self._interval = interval
        self._exporter = exporter
        self._running = False

    @property
    def connected(self) -> bool:
        return self._running

    async def run(self, on_message: MessageHandler) -> None:
        self._running = True
        try:
            while True:
                if self._export_due():
                    try:
                        await self.send_export()
                    except ProviderUnavailableError as exc:
                        log.warning("export by email failed: %s", exc)
                await asyncio.sleep(CHECK_SECONDS)
        finally:
            self._running = False

    def _export_due(self) -> bool:
        days = EXPORT_DAYS.get(self._interval)
        if days is None:
            return False
        last = self._db.get(EXPORT_KEY)
        now = utcnow()
        if last is None:
            self._db.set(EXPORT_KEY, now.isoformat())  # the first one comes after a full period
            return False
        return bool(now.isoformat() >= (_parse(last) + timedelta(days=days)).isoformat())

    async def _deliver(self, message: EmailMessage) -> None:
        try:
            await asyncio.to_thread(deliver, self._box, message)
        except (OSError, smtplib.SMTPException) as exc:
            raise ProviderUnavailableError(type(exc).__name__) from exc

    async def send(self, chat_id: str, reply: Reply) -> None:
        message = await asyncio.to_thread(compose, self._box.sender, chat_id, reply)
        await self._deliver(message)

    async def send_export(self) -> int:
        body = await asyncio.to_thread(self._exporter)
        if body is None:
            raise ProviderUnavailableError("export needs the dashboard")
        name = f"pi-guard-{utcnow():%Y%m%d}.json"
        for recipient in self._recipients:
            message = compose(self._box.sender, recipient, Reply("pi-guard export"))
            attach(message, name, body)
            await self._deliver(message)
        self._db.set(EXPORT_KEY, utcnow().isoformat())
        return len(self._recipients)


def _parse(text: str) -> Any:
    from datetime import datetime

    return datetime.fromisoformat(text)

"""Decides which events become messages, to whom, and delivers them.

Filtering (rule enabled, mode, quiet hours, cooldown, hourly cap) happens as
events arrive. Deliveries go through a bounded queue with retries, so a slow
or offline messenger never holds up detection or recording.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.core.events import Event, EventType
from pi_guard.core.permissions import Role
from pi_guard.core.timeutil import TimeWindow
from pi_guard.i18n import format_time, t
from pi_guard.messaging.base import Button, ProviderUnavailableError, Reply
from pi_guard.notifications.rules import Rule, resolve
from pi_guard.recording.segments import ClipError

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

# Alerts that someone at home may have caused; they get an "I am at home" button.
_OFFER_HOME = (
    EventType.MOTION_DETECTED,
    EventType.PERSON_DETECTED,
    EventType.UNKNOWN_PERSON,
    EventType.ARRIVAL_DETECTED,
)
RETRY_DELAYS = (5.0, 30.0, 120.0)
QUEUE_SIZE = 200


@dataclass(frozen=True)
class Recipient:
    provider: str
    chat_id: str
    language: str
    user_key: str | None = None


@dataclass
class Delivery:
    recipient: Recipient
    reply: Reply
    attempt: int = 0


class Notifier:
    def __init__(self, app: App) -> None:
        self._app = app
        self._settings = app.config.file.notifications
        quiet = self._settings.quiet_hours
        self._quiet = TimeWindow.parse(quiet) if quiet else None
        self._last_sent: dict[tuple[EventType, str | None], float] = {}
        self._recent: deque[float] = deque()
        self._queue: asyncio.Queue[Delivery] = asyncio.Queue(QUEUE_SIZE)
        self._events = app.bus.subscribe("notifier")

    async def run(self) -> None:
        await asyncio.gather(
            self._app.bus.consume(self._events, "notifier", self.handle),
            self._deliver_forever(),
        )

    def rule(self, event: EventType) -> Rule:
        return resolve(event, self._settings.rules)  # type: ignore[arg-type]

    def should_send(self, event: Event, rule: Rule, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if not rule.enabled:
            return False
        if self._app.modes.effective not in rule.modes:
            return False
        local = event.ts.astimezone(self._app.settings.tz)
        if self._quiet and not rule.bypasses_quiet_hours and self._quiet.contains(local):
            return False
        key = (event.type, event.camera)
        last = self._last_sent.get(key)
        if last is not None and now - last < rule.cooldown_seconds:
            return False
        while self._recent and now - self._recent[0] > 3600:
            self._recent.popleft()
        if rule.priority != "critical" and len(self._recent) >= self._settings.max_per_hour:
            log.warning("hourly notification limit reached; dropping %s", event.type)
            return False
        self._last_sent[key] = now
        self._recent.append(now)
        return True

    def recipients(self, rule: Rule, exclude: str | None = None) -> list[Recipient]:
        app = self._app
        result = []
        for user in app.users.all():
            if user.provider not in app.providers or user.provider not in rule.providers:
                continue
            if not user.notifications or user.key == exclude:
                continue
            if rule.recipients == "admins" and user.role is not Role.ADMIN:
                continue
            language = user.language or app.settings.language
            result.append(Recipient(user.provider, user.user_id, language, user.key))
        if (
            rule.recipients == "all"
            and "telegram" in app.providers
            and "telegram" in rule.providers
        ):
            for chat_id in app.settings.telegram_allowed_chat_ids:
                result.append(Recipient("telegram", str(chat_id), app.settings.language))
        return result

    async def handle(self, event: Event) -> None:
        rule = self.rule(event.type)
        if not self.should_send(event, rule):
            return
        recipients = self.recipients(rule, exclude=event.data.get("actor"))
        if not recipients:
            return
        attachment = await self._attachment(event, rule)
        for recipient in recipients:
            reply = self.message(event, recipient.language, attachment, rule)
            self._enqueue(Delivery(recipient, reply))

    async def _attachment(self, event: Event, rule: Rule) -> Path | None:
        if rule.attach == "none" or event.camera not in self._app.cameras:
            return None
        if rule.attach == "clip" and self._app.cameras[event.camera].segment_dir:
            try:
                return await self._app.clip(event.camera, self._settings.clip_seconds)
            except ClipError as exc:
                log.warning("no clip for %s: %s", event.type, exc)
        if event.snapshot and event.snapshot.exists():
            return event.snapshot
        try:
            return await self._app.take_photo(event.camera, prefix=event.type.value)
        except SnapshotUnavailableError:
            return None

    def message(self, event: Event, lang: str, attachment: Path | None, rule: Rule) -> Reply:
        app = self._app
        camera = app.cameras.get(event.camera or "")
        values: dict[str, object] = {
            "camera": camera.camera.name if camera else (event.camera or ""),
            "time": format_time(event.ts, app.settings.tz, lang),
            **{k: v for k, v in event.data.items() if isinstance(v, str | int | float)},
        }
        if event.type is EventType.MODE_CHANGED:
            values["mode"] = t(f"mode.{str(event.data['mode']).lower()}", lang)
        if event.type is EventType.PRESENCE_CHANGED:
            values["presence"] = t(f"presence.{str(event.data['presence']).lower()}", lang)
        key = f"notify.{event.type.value}"
        if event.data.get("recognition") == "uncertain":
            key = "notify.person_uncertain"
        text = t(key, lang, **values)
        buttons: list[tuple[Button, ...]] = []
        if camera:
            buttons.append(
                (
                    Button(t("menu.video", lang), f"video {camera.camera.id} 30"),
                    Button(t("photo.again", lang), f"photo {camera.camera.id}"),
                )
            )
        if event.type in _OFFER_HOME and app.modes.effective.value == "AWAY":
            buttons.append((Button(t("mode.button_home", lang), "home"),))
        buttons.append((Button(t("button.menu", lang), "menu"),))
        is_video = attachment is not None and attachment.suffix == ".mp4"
        return Reply(
            text,
            photo=attachment if attachment and not is_video else None,
            video=attachment if is_video else None,
            buttons=tuple(buttons),
        )

    def _enqueue(self, delivery: Delivery) -> None:
        if self._queue.full():
            dropped = self._queue.get_nowait()
            log.warning(
                "notification queue full; dropped message to %s", dropped.recipient.provider
            )
        self._queue.put_nowait(delivery)

    async def _deliver_forever(self) -> None:
        while True:
            delivery = await self._queue.get()
            await self._deliver(delivery)

    async def _deliver(self, delivery: Delivery) -> None:
        r = delivery.recipient
        try:
            await self._app.messenger.send(r.provider, r.chat_id, delivery.reply)
        except ProviderUnavailableError as exc:
            if delivery.attempt >= len(RETRY_DELAYS):
                log.error("giving up on notification via %s: %s", r.provider, exc)
                return
            delay = RETRY_DELAYS[delivery.attempt]
            delivery.attempt += 1
            log.info("%s unavailable, retrying notification in %.0fs", r.provider, delay)
            asyncio.get_running_loop().call_later(delay, self._enqueue, delivery)
        except Exception:
            log.exception("notification via %s failed", r.provider)

"""Emergency contacts for alerts nobody reacted to.

Every alert about movement or people that was actually sent is "open" until a
user acknowledges it ("Everything is fine", "I am at home", or the dashboard).
When at least `emergency_after_alerts` alerts are open and the oldest has been
open for `emergency_after_minutes`, the emergency contacts get one message with
a summary, and the latest events and photos if they are set to get them (see
notifications/contacts.py), and the owners are told that this happened.
Further open alerts are added to the same escalation instead of repeating it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pi_guard.core.events import Event, EventType
from pi_guard.core.permissions import Role
from pi_guard.core.timeutil import utcnow
from pi_guard.i18n import format_time, t
from pi_guard.messaging.base import Reply
from pi_guard.notifications.contacts import Contacts

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

ALERT_TYPES = (EventType.MOTION_DETECTED, EventType.PERSON_DETECTED, EventType.UNKNOWN_PERSON)
CHECK_SECONDS = 30
PHOTOS = 3
# The assistant's emergency watch looks at most this often: alarms come in bursts.
ASSESS_EVERY = 600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, type TEXT NOT NULL, camera TEXT,
    snapshot TEXT, acked_at TEXT, acked_by TEXT, escalated_at TEXT);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts (acked_at);
"""


@dataclass(frozen=True)
class OpenAlert:
    id: int
    ts: datetime
    type: str
    camera: str | None
    snapshot: str | None
    escalated: bool


class Escalation:
    def __init__(self, app: App) -> None:
        self._app = app
        self._db = app.db
        self._db.executescript(_SCHEMA)
        s = app.settings
        self.contacts = Contacts(app.db, s)
        self.after_alerts = s.emergency_after_alerts
        self.after = timedelta(minutes=s.emergency_after_minutes)
        self._assessed = float("-inf")  # monotonic: "never" must not mean "just now"
        self.assessment: str | None = None  # the latest view, for the escalation message
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.contacts.recipients("escalation"))

    def is_contact(self, provider: str, user_id: str) -> bool:
        return (provider, user_id) in self.contacts.channels()

    def record(self, event: Event, snapshot: Path | None) -> None:
        if event.type not in ALERT_TYPES:
            return
        self._db.execute(
            "INSERT INTO alerts (ts, type, camera, snapshot) VALUES (?, ?, ?, ?)",
            (event.ts.isoformat(), event.type.value, event.camera,
             snapshot.name if snapshot else None),
        )  # fmt: skip
        assistant = self._app.addons.assistant
        watching = assistant is not None and assistant.settings.emergency_watch and assistant.ready
        if watching and snapshot is not None and snapshot.suffix == ".jpg":
            now = asyncio.get_running_loop().time()
            if now - self._assessed >= ASSESS_EVERY:
                self._assessed = now
                task = asyncio.ensure_future(self._assess(event, snapshot))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    def facts(self, event: Event) -> str:
        """What the Pi itself knows about an alarm, for the assistant: no pictures of anyone."""
        app = self._app
        camera = app.cameras.get(event.camera or "")
        data = event.data or {}
        lines = [f"Time: {format_time(event.ts, app.settings.tz, 'en', with_date=True)}",
                 f"Event: {event.type.value}",
                 f"Camera: {camera.camera.name if camera else event.camera or '?'}",
                 f"Home mode: {app.modes.effective.value}"]  # fmt: skip
        if event.type is EventType.UNKNOWN_PERSON:
            known = ", ".join(data.get("known") or []) or "none"
            lines.append(f"Face recognition: {data.get('faces', 0)} face(s), not a trusted person; "
                         f"trusted people next to them: {known}")  # fmt: skip
        elif data.get("recognition") == "uncertain":
            lines.append("Face recognition: a face, too unclear to say who it is")
        elif event.type is EventType.PERSON_DETECTED:
            lines.append("Face recognition: a person, but no face that could be compared")
        else:
            lines.append("Face recognition: no person seen, only movement")
        return "\n".join(lines)

    async def _assess(self, event: Event, snapshot: Path) -> None:
        from pi_guard.addons.assistant import AssistantError

        app = self._app
        assistant = app.addons.assistant
        if assistant is None:
            return
        lang = app.settings.language
        try:
            view = await assistant.assess([snapshot], self.facts(event), lang)
        except (AssistantError, OSError) as exc:
            reason = getattr(exc, "key", type(exc).__name__)
            log.warning("emergency watch could not look: %s", reason)
            return
        except Exception as exc:  # a provider problem must not touch the alarm itself
            log.warning("emergency watch failed: %s", type(exc).__name__)
            return
        if not view:
            return
        self.assessment = view
        app.db.audit("assistant", "emergency.assessed", event.type.value)
        await app.messenger.notify_admins(t("emergency.assessment", lang, text=view), tone="alert")

    def open_alerts(self) -> list[OpenAlert]:
        rows = self._db.execute(
            "SELECT * FROM alerts WHERE acked_at IS NULL ORDER BY ts"
        ).fetchall()
        return [OpenAlert(r["id"], datetime.fromisoformat(r["ts"]), r["type"], r["camera"],
                          r["snapshot"], r["escalated_at"] is not None) for r in rows]  # fmt: skip

    def acknowledge(self, actor: str) -> int:
        count = self._db.execute(
            "UPDATE alerts SET acked_at = ?, acked_by = ? WHERE acked_at IS NULL",
            (utcnow().isoformat(), actor),
        ).rowcount
        if count:
            self._db.audit(actor, "alerts.ack", str(count))
            log.info("%d alert(s) acknowledged", count)
        return count

    def due(self, now: datetime | None = None) -> list[OpenAlert]:
        """Open alerts to escalate now, or an empty list."""
        if not self.enabled:
            return []
        alerts = self.open_alerts()
        fresh = [a for a in alerts if not a.escalated]
        if not fresh or len(alerts) < self.after_alerts:
            return []
        if (now or utcnow()) - alerts[0].ts < self.after:
            return []
        return alerts

    async def run(self) -> None:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            alerts = self.due()
            if alerts:
                await self.escalate(alerts)

    async def escalate(self, alerts: list[OpenAlert]) -> None:
        app = self._app
        lang = app.settings.language
        names = {cid: p.camera.name for cid, p in app.cameras.items()}
        head = [
            t("emergency.title", lang),
            "",
            t(
                "emergency.summary",
                lang,
                count=len(alerts),
                since=format_time(alerts[0].ts, app.settings.tz, lang, with_date=True),
            ),
        ]
        events = []
        for alert in alerts[-10:]:
            where = names.get(alert.camera or "", alert.camera or "")
            events.append(f"• {format_time(alert.ts, app.settings.tz, lang)} "
                          f"{t(f'event.{alert.type}', lang)} – {where}")  # fmt: skip
        tail = ["", t("emergency.what_to_do", lang)]
        if self.assessment:
            tail = ["", t("emergency.assessment", lang, text=self.assessment), *tail]
        photos = [app.media_dir / "snapshots" / a.snapshot for a in alerts if a.snapshot]
        photos = [p for p in photos if p.is_file()][-PHOTOS:]
        sent = 0
        reached: set[int] = set()
        for provider, chat, contact in self.contacts.recipients("escalation"):
            if provider not in app.providers:
                continue
            lines = head + (events if contact.gets("escalation", "events") else []) + tail
            try:
                await app.messenger.send(provider, chat, Reply("\n".join(lines)))
                if contact.gets("escalation", "pi_photo"):
                    for photo in photos:
                        caption = t("emergency.photo", lang)
                        await app.messenger.send(provider, chat, Reply(caption, photo=photo))
                if contact.id not in reached:
                    reached.add(contact.id)
                    sent += 1
            except Exception as exc:
                log.warning("emergency contact via %s unreachable: %s", provider, exc)
        ids = ",".join(str(a.id) for a in alerts)
        self._db.execute(f"UPDATE alerts SET escalated_at = ? WHERE id IN ({ids})",  # noqa: S608
                         (utcnow().isoformat(),))  # fmt: skip
        self._db.audit("system", "emergency.escalated", f"alerts={len(alerts)} contacts={sent}")
        log.warning("escalated %d unacknowledged alert(s) to %d emergency contact(s)",
                    len(alerts), sent)  # fmt: skip
        for user in app.users.all():
            if user.role is Role.ADMIN and user.provider in app.providers:
                user_lang = user.language or lang
                note = Reply(t("emergency.owner_note", user_lang, contacts=sent),
                             buttons=())  # fmt: skip
                try:
                    await app.messenger.send(user.provider, user.user_id, note)
                except Exception as exc:
                    log.warning("could not tell %s about the escalation: %s", user.provider, exc)
        app.bus.publish(
            Event(EventType.EMERGENCY_ESCALATED, data={"alerts": len(alerts), "contacts": sent})
        )

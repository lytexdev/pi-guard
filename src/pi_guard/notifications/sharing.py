"""What an emergency contact gets: the words always, the rest only if it was chosen for it.

The SOS of a phone and the dashboard's "send to a contact" both go through here, so the
emergency medical data leave the Pi in one place only: as a rescue link (valid an hour,
with a PIN in a message of its own) or, if the owner chose it for this contact, as the PDF
sheet. Every sending is in the audit log with the record number, never the content.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_guard.core.timeutil import utcnow
from pi_guard.i18n import format_time, t
from pi_guard.messaging.base import ProviderUnavailableError, Reply
from pi_guard.notifications.contacts import Contact

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

LINK_MINUTES = 60
EVENTS = 5
_FAILED = (ProviderUnavailableError, RuntimeError, OSError)


def recent_events(app: App, limit: int = EVENTS) -> list[str]:
    lang = app.settings.language
    names = {cid: p.camera.name for cid, p in app.cameras.items()}
    lines = []
    for event in app.db.recent_events(limit):
        where = names.get(event.camera or "", event.camera or "")
        try:
            label = t(f"event.{event.type}", lang)
        except KeyError:
            label = event.type
        lines.append(f"• {format_time(event.ts, app.settings.tz, lang)} {label}"
                     + (f" – {where}" if where else ""))  # fmt: skip
    return lines


async def send_medical(
    app: App, provider: str, chat: str, contact: Contact, record_id: int, sheet: dict[str, Any],
    base_url: str, actor: str,
) -> bool:  # fmt: skip
    """The emergency data of one person to one contact, as the contact is set to get them."""
    from pi_guard.addons import sheet as sheets

    records = app.addons.medical
    if records is None:
        return False
    lang = app.settings.language
    name = str(sheet.get("name", ""))
    try:
        if contact.medical_mode == "sheet":
            stamp = format_time(utcnow(), app.settings.tz, lang, with_date=True)
            body = sheets.pdf(sheet, lang, t("web.md_pdf_footer", lang, time=stamp))
            with tempfile.TemporaryDirectory(prefix="pg-md-") as folder:
                path = Path(folder) / f"{t('web.md_title', lang)} {name}.pdf".replace("/", "-")
                path.write_bytes(body)
                text = t("emergency.medical_sheet", lang, name=name)
                await app.messenger.send(provider, chat,
                                         Reply(text, document=path, tone="alert"))  # fmt: skip
        else:
            token, pin, _ = records.make_link(record_id, LINK_MINUTES, True, actor)
            url = f"{base_url.rstrip('/')}/emergency/{token}"
            await app.messenger.send(provider, chat, Reply(
                t("emergency.medical_link", lang, name=name, url=url, minutes=LINK_MINUTES),
                tone="alert"))  # fmt: skip
            await app.messenger.send(provider, chat, Reply(
                t("emergency.medical_pin", lang, pin=pin), tone="alert"))  # fmt: skip
    except _FAILED:
        log.warning("emergency data via %s failed", provider)
        return False
    app.db.audit(actor, f"medical.sent_{contact.medical_mode}", f"{record_id}:{contact.id}")
    return True


async def _say(app: App, provider: str, chat: str, text: str, photos: Sequence[Path]) -> bool:
    """The words matter most: if a picture is what fails, they still go out."""
    send = app.messenger.send
    try:
        await send(provider, chat, Reply(text, photo=photos[0] if photos else None, tone="alert"))
        for photo in photos[1:]:
            await send(provider, chat, Reply(photo.stem, photo=photo, tone="alert"))
        return True
    except _FAILED:
        log.warning("emergency message via %s failed", provider)
    if not photos:
        return False
    try:
        await send(provider, chat, Reply(text, tone="alert"))
        return True
    except _FAILED:
        return False


def base_url(app: App, fallback: str) -> str:
    """Where a rescue link points: the dashboard's public address if there is one, else the
    address the request came to (which may work in the home network or the VPN only)."""
    return app.settings.dashboard_public_url or fallback


async def sos(
    app: App, username: str, head: list[str], place: list[str], photos: Sequence[Path],
    actor: str, address: str,
) -> int:  # fmt: skip
    """An SOS to every contact that takes part, each with what it is set to get."""
    medical = None
    records = app.addons.medical
    wanted = app.escalation.contacts.recipients("sos")
    if records is not None and any(c.gets("sos", "medical") for _, _, c in wanted):
        medical = records.for_account(username)
    events = recent_events(app) if any(c.gets("sos", "events") for _, _, c in wanted) else []
    reached: set[int] = set()
    for provider, chat, contact in wanted:
        if provider not in app.providers:
            continue
        lines = list(head)
        if contact.gets("sos", "location"):
            lines += place
        if events and contact.gets("sos", "events"):
            lines += ["", t("emergency.recent", app.settings.language), *events]
        chosen = photos if contact.gets("sos", "pi_photo") else []
        if not await _say(app, provider, chat, "\n".join(lines), chosen):
            continue
        reached.add(contact.id)
        if medical is not None and contact.gets("sos", "medical"):
            url = base_url(app, address)
            await send_medical(app, provider, chat, contact, *medical, url, actor)
    return len(reached)


async def to_contacts(
    app: App, text: str, need: str | None = None, photo: Path | None = None,
    audio: Path | None = None,
) -> int:  # fmt: skip
    """Words, and a file to those contacts that get this kind (`need`), for the SOS."""
    reached: set[int] = set()
    for provider, chat, contact in app.escalation.contacts.recipients("sos"):
        if provider not in app.providers or (need and not contact.gets("sos", need)):
            continue
        try:
            reply = Reply(text, photo=photo, audio=audio, tone="alert")
            await app.messenger.send(provider, chat, reply)
            reached.add(contact.id)
        except _FAILED:
            log.warning("emergency message via %s failed", provider)
    return len(reached)

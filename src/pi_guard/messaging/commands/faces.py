"""Enrollment of trusted people (admin only, opt-in, with explicit consent step).

Enrollment: an admin sends a photo of the person with the caption
`/enroll Name`. The face embedding is computed, the photo is deleted, and the
embedding is only stored after the admin confirms the person has consented.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pi_guard.core.permissions import Role
from pi_guard.detection.faces import Embedding, EnrollmentError, FaceRecognizer
from pi_guard.i18n import format_time
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.core import menu_row
from pi_guard.messaging.commands.registry import ArgError, Command, Registry, TextArg

PENDING_SECONDS = 600


@dataclass(frozen=True)
class PendingEnrollment:
    name: str
    embedding: Embedding
    actor: str
    expires: float


_pending: dict[str, PendingEnrollment] = {}


def _recognizer(ctx: CommandContext) -> FaceRecognizer:
    if ctx.app.faces is None:
        raise ArgError("faces.disabled")
    return ctx.app.faces


async def faces(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    recognizer = _recognizer(ctx)
    text = args.get("action", "")
    verb, _, name = text.partition(" ")
    if verb.casefold() in ("delete", "löschen", "loeschen") and name:
        deleted = recognizer.delete(name)
        ctx.app.db.audit(ctx.user.key, "faces.delete", "ok" if deleted else "missing")
        return Reply(
            ctx.t("faces.deleted" if deleted else "faces.not_found", name=name),
            buttons=(menu_row(ctx),),
        )
    people = recognizer.store.people()
    if not people:
        return Reply(ctx.t("faces.none"), buttons=(menu_row(ctx),))
    lines = [ctx.t("faces.title")]
    rows = []
    for person, samples, enrolled in people:
        when = format_time(
            datetime.fromisoformat(enrolled), ctx.app.settings.tz, ctx.lang, with_date=True
        )
        lines.append(ctx.t("faces.entry", name=person, samples=samples, date=when))
        rows.append((Button(ctx.t("faces.delete_button", name=person), f"faces delete {person}"),))
    lines += ["", ctx.t("faces.how_to")]
    return Reply("\n".join(lines), buttons=(*rows, menu_row(ctx)))


async def enroll(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    recognizer = _recognizer(ctx)
    text = args.get("text", "")
    verb, _, token = text.partition(" ")
    if verb in ("confirm", "cancel"):
        pending = _pending.pop(token, None)
        if pending is None or pending.expires < time.monotonic():
            return Reply(ctx.t("faces.expired"), buttons=(menu_row(ctx),))
        if verb == "cancel":
            return Reply(ctx.t("faces.cancelled"), buttons=(menu_row(ctx),))
        try:
            count = recognizer.enroll(pending.name, pending.embedding, ctx.user.key)
        except EnrollmentError as exc:
            return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
        ctx.app.db.audit(ctx.user.key, "faces.enroll", f"samples={count}")
        return Reply(
            ctx.t("faces.enrolled", name=pending.name, samples=count), buttons=(menu_row(ctx),)
        )

    if ctx.attachment is None or ctx.attachment.suffix != ".jpg":
        return Reply(ctx.t("faces.how_to"), buttons=(menu_row(ctx),))
    name = text.strip()
    if not name or len(name) > 64:
        return Reply(ctx.t("faces.bad_name"), buttons=(menu_row(ctx),))
    try:
        embedding = await asyncio.to_thread(recognizer.prepare, ctx.attachment)
    except EnrollmentError as exc:
        return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
    for key in [k for k, p in _pending.items() if p.expires < time.monotonic()]:
        del _pending[key]
    token = secrets.token_hex(4)
    _pending[token] = PendingEnrollment(
        name, embedding, ctx.user.key, time.monotonic() + PENDING_SECONDS
    )
    return Reply(
        ctx.t("faces.consent", name=name),
        buttons=(
            (ctx.button("faces.consent_yes", f"enroll confirm {token}"),),
            (ctx.button("faces.consent_no", f"enroll cancel {token}"),),
        ),
    )


def register(registry: Registry) -> None:
    registry.add(
        Command(
            "faces",
            faces,
            ("gesichter", "personen"),
            (TextArg("action"),),
            role=Role.ADMIN,
            help_key="help.faces",
        )
    )
    registry.add(
        Command(
            "enroll",
            enroll,
            ("eintragen", "anlernen"),
            (TextArg("text"),),
            role=Role.ADMIN,
            menu=False,
        )
    )

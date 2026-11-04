"""Trusted people: enrollment and management (admin only, opt-in, consent required).

Enrollment is a short session per admin:
  1. a name (`/enroll Erna`, or the bot asks for it)
  2. photos, videos or pictures taken by the camera, as many as wanted
  3. "Done", then an explicit confirmation that the person consented
Photos and video frames are deleted as soon as their face data is computed;
only the confirmed face data is stored. A person seen by the camera can also be
enrolled straight from an alert ("Add as trusted person").
"""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from pi_guard.cameras.pipeline import SnapshotUnavailableError
from pi_guard.core.permissions import Role
from pi_guard.detection.enrollment import EnrollmentSession, video_frames
from pi_guard.detection.faces import Embedding, EnrollmentError, FaceRecognizer
from pi_guard.i18n import format_time
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.core import menu_row
from pi_guard.messaging.commands.registry import ArgError, CameraArg, Command, Registry, TextArg

MAX_NAME = 64


def _recognizer(ctx: CommandContext) -> FaceRecognizer:
    if ctx.app.faces is None:
        raise ArgError("faces.disabled")
    return ctx.app.faces


def _session(ctx: CommandContext) -> EnrollmentSession | None:
    session = ctx.app.enrollments.get(ctx.user.key)
    if session is not None and session.expired:
        del ctx.app.enrollments[ctx.user.key]
        return None
    return session


def _collect_reply(ctx: CommandContext, session: EnrollmentSession, note: str = "") -> Reply:
    text = ctx.t("faces.collect", name=session.name or "?", samples=len(session.samples))
    rows: list[tuple[Button, ...]] = []
    if ctx.app.cameras:
        rows.append((ctx.button("faces.camera_button", "enroll camera"),))
    if session.samples:
        rows.append((ctx.button("faces.done_button", "enroll done"),))
    rows.append((ctx.button("faces.cancel_button", "enroll cancel"),))
    return Reply(f"{note}\n\n{text}".strip(), buttons=tuple(rows))


async def faces(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    recognizer = _recognizer(ctx)
    verb, _, name = args.get("action", "").partition(" ")
    if verb.casefold() in ("delete", "löschen", "loeschen") and name:
        deleted = recognizer.delete(name)
        ctx.app.db.audit(ctx.user.key, "faces.delete", "ok" if deleted else "missing")
        return Reply(
            ctx.t("faces.deleted" if deleted else "faces.not_found", name=name),
            buttons=(menu_row(ctx),),
        )
    people = recognizer.store.people()
    lines = [ctx.t("faces.title") if people else ctx.t("faces.none")]
    rows = []
    for person, samples, enrolled in people:
        when = format_time(
            datetime.fromisoformat(enrolled), ctx.app.settings.tz, ctx.lang, with_date=True
        )
        lines.append(ctx.t("faces.entry", name=person, samples=samples, date=when))
        lines.append(ctx.t("faces.quality_line", score=recognizer.quality(person)["score"]))
        rows.append(
            (
                Button(ctx.t("faces.more_button", name=person), f"enroll name {person}"),
                Button(ctx.t("faces.delete_button", name=person), f"faces delete {person}"),
            )
        )
    lines += ["", ctx.t("faces.how_to")]
    return Reply(
        "\n".join(lines),
        buttons=(*rows, (ctx.button("faces.add_button", "enroll"),), menu_row(ctx)),
    )


async def enroll(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    recognizer = _recognizer(ctx)
    text = args.get("text", "").strip()
    verb, _, rest = text.partition(" ")
    action = verb.casefold()
    if action == "add":
        return await _add_media(ctx, recognizer)
    if action == "camera":
        return await _add_from_camera(ctx, recognizer, rest)
    if action == "done":
        return _ask_consent(ctx)
    if action == "confirm":
        return _store(ctx, recognizer)
    if action == "cancel":
        ctx.app.enrollments.pop(ctx.user.key, None)
        return Reply(ctx.t("faces.cancelled"), buttons=(menu_row(ctx),))

    name = rest.strip() if action == "name" else text
    session = _session(ctx)
    if not name:
        return Reply(ctx.t("faces.ask_name"), buttons=(menu_row(ctx),), expect="enroll name")
    if len(name) > MAX_NAME:
        return Reply(ctx.t("faces.bad_name"), buttons=(menu_row(ctx),))
    if session is None or (session.name and session.name != name):
        session = ctx.app.enrollments[ctx.user.key] = EnrollmentSession()
    session.name = name
    if ctx.attachment is not None:
        return await _add_media(ctx, recognizer)
    return _collect_reply(ctx, session)


async def trust(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    """Start enrolling the person in an alert's snapshot."""
    recognizer = _recognizer(ctx)
    stored = ctx.app.db.get(f"trust:{args.get('token', '')}")
    path = ctx.app.media_dir / "snapshots" / str(stored) if stored else None
    if path is None or not path.is_file():
        return Reply(ctx.t("faces.expired"), buttons=(menu_row(ctx),))
    try:
        embedding = await asyncio.to_thread(recognizer.prepare, path)
    except EnrollmentError as exc:
        return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
    session = ctx.app.enrollments[ctx.user.key] = EnrollmentSession()
    session.add(embedding, recognizer.settings.max_samples_per_person)
    return Reply(ctx.t("faces.ask_name"), buttons=(menu_row(ctx),), expect="enroll name")


async def _add_media(ctx: CommandContext, recognizer: FaceRecognizer) -> Reply:
    session = _session(ctx)
    if session is None or not session.name:
        return Reply(
            ctx.t("faces.no_session"),
            buttons=((ctx.button("faces.add_button", "enroll"),), menu_row(ctx)),
        )
    if ctx.attachment is None:
        return _collect_reply(ctx, session)
    limit = recognizer.settings.max_samples_per_person
    if ctx.attachment.suffix == ".mp4":
        added = await _add_video(ctx.attachment, recognizer, session, limit)
        note = ctx.t("faces.video_added", count=added) if added else ctx.t("faces.no_face_video")
        return _collect_reply(ctx, session, note)
    try:
        embedding = await asyncio.to_thread(recognizer.prepare, ctx.attachment)
    except EnrollmentError as exc:
        return _collect_reply(ctx, session, ctx.t(exc.key))
    added = session.add(embedding, limit)
    return _collect_reply(ctx, session, ctx.t("faces.photo_added" if added else "faces.duplicate"))


async def _add_video(
    video: Path, recognizer: FaceRecognizer, session: EnrollmentSession, limit: int
) -> int:
    workdir, frames = await video_frames(video)
    try:
        added = 0
        for frame in frames:
            try:
                embedding: Embedding = await asyncio.to_thread(recognizer.prepare, frame)
            except EnrollmentError:
                continue  # frames without exactly one face are simply skipped
            added += session.add(embedding, limit)
        return added
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _add_from_camera(ctx: CommandContext, recognizer: FaceRecognizer, rest: str) -> Reply:
    session = _session(ctx)
    if session is None or not session.name:
        return Reply(ctx.t("faces.no_session"), buttons=(menu_row(ctx),))
    camera_id = CameraArg().parse(rest.casefold(), ctx) if rest else None
    pipeline = ctx.app.cameras[camera_id] if camera_id else ctx.camera({})
    if pipeline is None:
        return Reply(ctx.t("camera.choose"), buttons=ctx.camera_choice("enroll camera"))
    try:
        photo = await ctx.app.take_photo(pipeline.camera.id, prefix="enroll")
    except SnapshotUnavailableError:
        return _collect_reply(ctx, session, ctx.t("photo.unavailable", name=pipeline.camera.name))
    try:
        embedding = await asyncio.to_thread(recognizer.prepare, photo)
    except EnrollmentError as exc:
        return _collect_reply(ctx, session, ctx.t(exc.key))
    finally:
        photo.unlink(missing_ok=True)
    added = session.add(embedding, recognizer.settings.max_samples_per_person)
    return _collect_reply(ctx, session, ctx.t("faces.photo_added" if added else "faces.duplicate"))


def _ask_consent(ctx: CommandContext) -> Reply:
    session = _session(ctx)
    if session is None or not session.name:
        return Reply(ctx.t("faces.expired"), buttons=(menu_row(ctx),))
    if not session.samples:
        return _collect_reply(ctx, session, ctx.t("faces.no_samples"))
    return Reply(
        ctx.t("faces.consent", name=session.name, samples=len(session.samples)),
        buttons=(
            (ctx.button("faces.consent_yes", "enroll confirm"),),
            (ctx.button("faces.consent_no", "enroll cancel"),),
        ),
    )


def _quality_lines(ctx: CommandContext, quality: dict[str, Any]) -> list[str]:
    lines = [ctx.t("faces.quality_line", score=quality["score"])]
    for tip in quality["tips"][:1]:
        values = {k: v for k, v in tip.items() if k != "key"}
        lines.append(ctx.t("faces.quality_tip", tip=ctx.t(tip["key"], **values)))
    return lines


def _store(ctx: CommandContext, recognizer: FaceRecognizer) -> Reply:
    session = ctx.app.enrollments.pop(ctx.user.key, None)
    if session is None or session.expired or not session.name or not session.samples:
        return Reply(ctx.t("faces.expired"), buttons=(menu_row(ctx),))
    try:
        report = recognizer.enroll_many(session.name, session.samples, ctx.user.key)
    except EnrollmentError as exc:
        return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
    ctx.app.db.audit(ctx.user.key, "faces.enroll", f"samples={report.stored}")
    lines = (
        [ctx.t("faces.enrolled", name=session.name, samples=report.total)] if report.stored else []
    )
    if report.outliers:
        lines.append(ctx.t("faces.outliers", count=report.outliers))
    lines += [
        ctx.t("faces.conflict", count=n, other=other) for other, n in report.conflicts.items()
    ]
    if not report.stored:
        lines.append(ctx.t("faces.nothing_stored"))
    else:
        lines += _quality_lines(ctx, recognizer.quality(session.name))
    return Reply(
        "\n".join(lines),
        buttons=((ctx.button("faces.list_button", "faces"),), menu_row(ctx)),
    )


def register(registry: Registry) -> None:
    registry.add(
        Command(
            "faces",
            faces,
            ("gesichter", "personen", "vertraute", "people"),
            (TextArg("action"),),
            role=Role.ADMIN,
            help_key="help.faces",
        )
    )
    registry.add(
        Command(
            "enroll",
            enroll,
            ("eintragen", "anlernen", "hinzufügen"),
            (TextArg("text"),),
            role=Role.ADMIN,
            help_key="help.enroll",
        )
    )
    registry.add(
        Command("trust", trust, ("vertrauen",), (TextArg("token"),), role=Role.ADMIN, menu=False)
    )

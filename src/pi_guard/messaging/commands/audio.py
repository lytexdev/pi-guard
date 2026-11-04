"""Microphone and speaker commands. Administrators only.

Listening is always bounded in time, audited, and — when a speaker is set
up — announced on site with a chime before the microphone opens.
"""

from __future__ import annotations

import contextlib
import secrets
from pathlib import Path
from typing import Any

from pi_guard.addons.assistant import AssistantError
from pi_guard.audio.service import AudioError, AudioService
from pi_guard.audio.sounds import valid_name
from pi_guard.core.permissions import Role
from pi_guard.core.timeutil import utcnow
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.core import menu_row
from pi_guard.messaging.commands.registry import (
    ArgError,
    CameraArg,
    ChoiceArg,
    Command,
    IntArg,
    Registry,
    TextArg,
)
from pi_guard.recording.segments import ClipError

DEFAULT_LISTEN_SECONDS = 10
ALARM_SECONDS = 30
ALARM_UPLOAD_NAME = "alarm"


def _audio(ctx: CommandContext) -> AudioService:
    if ctx.app.audio is None:
        raise ArgError("audio.not_configured")
    return ctx.app.audio


def _error(ctx: CommandContext, exc: AudioError) -> Reply:
    return Reply(ctx.t(exc.key, **exc.values), buttons=(menu_row(ctx),))


async def listen(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    audio = _audio(ctx)
    if not audio.recording_enabled:
        return Reply(ctx.t("audio.recording_disabled"), buttons=(menu_row(ctx),))
    seconds = min(args.get("seconds", DEFAULT_LISTEN_SECONDS), audio.settings.max_clip_seconds)
    camera_id = args.get("camera")
    with_mic = [p for p in ctx.app.cameras.values() if p.microphone and p.segment_dir]
    pipeline = ctx.app.cameras[camera_id] if camera_id else (with_mic[0] if with_mic else None)
    ctx.app.db.audit(ctx.user.key, "audio.listen", f"{seconds}s")
    try:
        if pipeline is not None and pipeline.microphone:
            path = await ctx.app.clip(pipeline.camera.id, seconds, audio_only=True)
            return Reply(
                ctx.t("audio.clip_past", seconds=seconds), audio=path, buttons=(menu_row(ctx),)
            )
        if audio.settings.microphone is None:
            return Reply(ctx.t("audio.no_microphone"), buttons=(menu_row(ctx),))
        chime = audio.sounds.path("chime")
        if audio.playback_enabled and chime:
            # Announcing the microphone matters more than quiet hours.
            with contextlib.suppress(AudioError):
                await audio.play(chime, ignore_quiet_hours=True)
        await ctx.progress(ctx.t("audio.recording_now", seconds=seconds))
        out = ctx.app.media_dir / "audio" / f"listen_{utcnow():%Y%m%dT%H%M%S}.m4a"
        path = await audio.record(audio.settings.microphone, seconds, out)
    except AudioError as exc:
        return _error(ctx, exc)
    except ClipError:
        return Reply(ctx.t("audio.recording_failed"), buttons=(menu_row(ctx),))
    return Reply(ctx.t("audio.clip_live", seconds=seconds), audio=path, buttons=(menu_row(ctx),))


async def sound(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    audio = _audio(ctx)
    name = args.get("name")
    if not name:
        rows = tuple((Button(n, f"sound {n}"),) for n in audio.sounds.names())
        return Reply(
            ctx.t("audio.sounds", volume=audio.volume),
            buttons=(
                *rows,
                (
                    Button("−", f"volume {max(0, audio.volume - 10)}"),
                    Button("+", f"volume {min(audio.max_volume, audio.volume + 10)}"),
                ),
                menu_row(ctx),
            ),
        )
    path = audio.sounds.path(name.casefold())
    if path is None:
        return Reply(ctx.t("audio.unknown_sound", name=name), buttons=(menu_row(ctx),))
    try:
        await audio.play(path)
    except AudioError as exc:
        return _error(ctx, exc)
    ctx.app.db.audit(ctx.user.key, "audio.play", name)
    return Reply(
        ctx.t("audio.played", name=name),
        buttons=((Button(ctx.t("audio.again"), f"sound {name}"),), menu_row(ctx)),
    )


async def volume(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    audio = _audio(ctx)
    if "level" in args:
        audio.set_volume(args["level"])
    return Reply(
        ctx.t("audio.volume", volume=audio.volume, maximum=audio.max_volume),
        buttons=(
            (
                Button("−", f"volume {max(0, audio.volume - 10)}"),
                Button("+", f"volume {min(audio.max_volume, audio.volume + 10)}"),
            ),
            menu_row(ctx),
        ),
    )


def _pending(ctx: CommandContext, token: str) -> Path | None:
    if not token.isalnum():
        return None
    matches = list((ctx.app.media_dir / "audio").glob(f"upload_{token}.*"))
    return matches[0] if matches else None


async def _ask_by_voice(ctx: CommandContext, path: Path) -> Reply:
    assistant = ctx.app.addons.assistant
    if assistant is None:
        return Reply(ctx.t("audio.upload_expired"), buttons=(menu_row(ctx),))
    try:
        question = await assistant.transcribe(path)
        result = await assistant.ask(question, ctx.lang, ctx.chat_key)
        spoken = await assistant.speak(result.text)
    except AssistantError as exc:
        return Reply(ctx.t(exc.key), buttons=(menu_row(ctx),))
    finally:
        path.unlink(missing_ok=True)
    return Reply(f"„{question}“\n\n{result.text}", audio=spoken, buttons=(menu_row(ctx),))


async def upload(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    """Handles audio files sent by an admin, and the buttons offered afterwards."""
    audio = _audio(ctx)
    action, _, rest = args.get("text", "").partition(" ")
    if action in ("play", "save", "alarm", "discard", "ask"):
        token, _, name = rest.partition(" ")
        path = _pending(ctx, token)
        if path is None:
            return Reply(ctx.t("audio.upload_expired"), buttons=(menu_row(ctx),))
        try:
            if action == "ask":
                return await _ask_by_voice(ctx, path)
            if action == "play":
                await audio.play(path)
                ctx.app.db.audit(ctx.user.key, "audio.play_upload")
                return Reply(ctx.t("audio.played_upload"), buttons=_upload_buttons(ctx, token))
            if action == "save":
                name = name.casefold() or f"message_{utcnow():%m%d%H%M}"
                if not valid_name(name):
                    return Reply(ctx.t("audio.bad_name"), buttons=_upload_buttons(ctx, token))
                audio.sounds.save(name, path)
                return Reply(ctx.t("audio.saved", name=name), buttons=(menu_row(ctx),))
            if action == "alarm":
                audio.sounds.save(ALARM_UPLOAD_NAME, path)
                audio.set_alarm_sound(ALARM_UPLOAD_NAME)
                ctx.app.db.audit(ctx.user.key, "audio.alarm_sound")
                return Reply(ctx.t("audio.alarm_sound_set"), buttons=(
                    (ctx.button("alarm.test_button", "alarm"),), menu_row(ctx)))  # fmt: skip
        except AudioError as exc:
            return _error(ctx, exc)
        except ValueError:
            return Reply(ctx.t("audio.bad_name"), buttons=_upload_buttons(ctx, token))
        path.unlink(missing_ok=True)
        return Reply(ctx.t("audio.discarded"), buttons=(menu_row(ctx),))

    if ctx.attachment_meta.get("too_large"):
        return Reply(ctx.t("audio.too_large"), buttons=(menu_row(ctx),))
    if ctx.attachment is None:
        return Reply(ctx.t("audio.send_file"), buttons=(menu_row(ctx),))
    try:
        await audio.validate_upload(ctx.attachment)
    except AudioError as exc:
        return _error(ctx, exc)
    token = secrets.token_hex(4)
    dest = ctx.app.media_dir / "audio" / f"upload_{token}{ctx.attachment.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx.attachment.replace(dest)  # keep it past this message; retention removes it after a day
    return Reply(ctx.t("audio.uploaded"), buttons=_upload_buttons(ctx, token))


def _upload_buttons(ctx: CommandContext, token: str) -> tuple[tuple[Button, ...], ...]:
    assistant = ctx.app.addons.assistant
    ask = ((ctx.button("audio.ask_assistant", f"upload ask {token}"),),) if assistant else ()
    return (
        *ask,
        (ctx.button("audio.play_now", f"upload play {token}"),),
        (ctx.button("audio.save_as_sound", f"upload save {token}"),),
        (ctx.button("audio.use_as_alarm", f"upload alarm {token}"),),
        (ctx.button("audio.discard", f"upload discard {token}"),),
        menu_row(ctx),
    )


async def alarm(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    """Sound the alarm on the speaker. Without "on", it asks first, so typing never triggers it."""
    audio = _audio(ctx)
    state = args.get("state")
    if state == "off":
        stopped = await audio.stop_alarm()
        if stopped:
            ctx.app.db.audit(ctx.user.key, "alarm.stop")
        return Reply(ctx.t("alarm.stopped" if stopped else "alarm.not_running"),
                     buttons=(menu_row(ctx),))  # fmt: skip
    if state != "on":
        return Reply(ctx.t("alarm.confirm", seconds=ALARM_SECONDS), buttons=(
            (ctx.button("alarm.start_button", "alarm on"),),
            (ctx.button("faces.cancel_button", "menu"),),
        ))  # fmt: skip
    try:
        audio.start_alarm(ALARM_SECONDS)
    except AudioError as exc:
        return _error(ctx, exc)
    ctx.app.db.audit(ctx.user.key, "alarm.start")
    stop = (ctx.button("alarm.stop_button", "alarm off"),)
    return Reply(ctx.t("alarm.started", seconds=ALARM_SECONDS), buttons=(stop, menu_row(ctx)))


def register(registry: Registry) -> None:
    on_off = {"on": "on", "an": "on", "ein": "on", "los": "on",
              "off": "off", "aus": "off", "stop": "off", "stopp": "off"}  # fmt: skip
    registry.add(Command("alarm", alarm, ("sirene", "alarmton"), (ChoiceArg("state", on_off),),
                         role=Role.ADMIN, help_key="help.alarm"))  # fmt: skip
    registry.add(
        Command(
            "listen",
            listen,
            ("audio", "anhören", "anhoeren"),
            (CameraArg(), IntArg("seconds", 3, 300)),
            role=Role.ADMIN,
            help_key="help.listen",
        )
    )
    registry.add(
        Command(
            "sound",
            sound,
            ("ton", "klang", "abspielen"),
            (TextArg("name"),),
            role=Role.ADMIN,
            help_key="help.sound",
        )
    )
    registry.add(
        Command(
            "volume",
            volume,
            ("lautstärke", "lautstaerke", "vol"),
            (IntArg("level", 0, 100),),
            role=Role.ADMIN,
            help_key="help.volume",
        )
    )
    registry.add(Command("upload", upload, (), (TextArg("text"),), role=Role.ADMIN, menu=False))

"""Everyday commands: menu, photo, video, status, events and personal settings."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pi_guard.cameras.pipeline import CameraState, SnapshotUnavailableError
from pi_guard.core.modes import Selection
from pi_guard.core.permissions import Role
from pi_guard.i18n import format_duration, format_time
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.registry import (
    CameraArg,
    ChoiceArg,
    Command,
    IntArg,
    Registry,
)
from pi_guard.recording.segments import ClipError

MAX_VIDEO_SECONDS = 120
DEFAULT_VIDEO_SECONDS = 15
EVENT_TYPES_SHOWN = [
    "motion_detected",
    "person_detected",
    "known_person",
    "unknown_person",
    "camera_disconnected",
    "camera_reconnected",
    "recording_failed",
    "storage_low",
    "mode_changed",
    "system_started",
]


def menu_row(ctx: CommandContext) -> tuple[Button, ...]:
    return (ctx.button("button.menu", "menu"),)


def camera_summary(ctx: CommandContext) -> str:
    cameras = list(ctx.app.cameras.values())
    if not cameras:
        return ctx.t("status.no_cameras")
    broken = [
        p for p in cameras if p.status.state in (CameraState.RECONNECTING, CameraState.OFFLINE)
    ]
    if not broken:
        if len(cameras) == 1:
            return ctx.t("status.camera_ok")
        return ctx.t("status.cameras_ok", n=len(cameras))
    return "\n".join(ctx.t("status.camera_problem", name=p.camera.name) for p in broken)


def recording_summary(ctx: CommandContext) -> str:
    return ctx.t(
        "status.recording_on" if ctx.app.settings.recording_enabled else "status.recording_off"
    )


def mode_summary(ctx: CommandContext) -> str:
    state = ctx.app.modes.state
    text = ctx.t(f"mode.{state.effective.value.lower()}")
    if state.selection is Selection.AUTO:
        return ctx.t("status.mode_auto", mode=text)
    if state.manual_until:
        until = format_time(state.manual_until, ctx.app.settings.tz, ctx.lang)
        return ctx.t("status.mode_until", mode=text, until=until)
    return ctx.t("status.mode", mode=text)


async def menu(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    text = "\n\n".join(
        [
            "pi-guard",
            camera_summary(ctx),
            recording_summary(ctx),
            ctx.t("menu.question"),
        ]
    )
    rows: list[tuple[Button, ...]] = [
        (ctx.button("menu.photo", "photo"),),
        (ctx.button("menu.video", "video"),),
        (ctx.button("menu.status", "status"),),
        (ctx.button("menu.notifications", "notifications"),),
        (ctx.button("menu.help", "help"),),
    ]
    if ctx.user.role is Role.ADMIN or not ctx.simple:
        rows.insert(4, (ctx.button("menu.more", "more"),))
    return Reply(text, buttons=tuple(rows))


async def more(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    rows: list[tuple[Button, ...]] = [
        (ctx.button("mode.button_home", "home"), ctx.button("mode.button_away", "away")),
        (ctx.button("more.events", "events"), ctx.button("more.cameras", "cameras")),
    ]
    if ctx.app.modes.presence_enabled:
        rows[0] += (ctx.button("mode.button_auto", "mode auto"),)
    if ctx.user.role is Role.ADMIN:
        if ctx.app.audio and ctx.app.audio.playback_enabled:
            rows.append((ctx.button("more.sounds", "sound"),))
        if ctx.app.faces is not None:
            rows.append((ctx.button("more.people", "faces"),))
        rows.append((ctx.button("more.users", "users"), ctx.button("more.invite", "invite")))
    rows.append((ctx.button("more.settings", "settings"),))
    rows.append(menu_row(ctx))
    return Reply(ctx.t("more.title"), buttons=tuple(rows))


def photo_buttons(ctx: CommandContext, camera_id: str) -> tuple[tuple[Button, ...], ...]:
    """The buttons under every photo: what people want to do next with a picture."""
    return (
        (ctx.button("photo.again", f"photo {camera_id}"),
         ctx.button("menu.video", f"video {camera_id}")),
        (ctx.button("image.zoom_button", "zoom"),
         ctx.button("image.sharper_button", f"sharper {camera_id}"),
         ctx.button("image.brighter_button", "brighter")),
        menu_row(ctx),
    )  # fmt: skip


async def photo(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    if args.get("all") and len(ctx.app.cameras) > 1:
        return await _all_photos(ctx)
    pipeline = ctx.camera(args)
    if pipeline is None:
        choices = (*ctx.camera_choice("photo"), (ctx.button("photo.all", "photo all"),))
        return Reply(ctx.t("camera.choose"), buttons=(*choices, menu_row(ctx)))
    try:
        path = await ctx.app.take_photo(pipeline.camera.id)
    except SnapshotUnavailableError:
        return Reply(
            ctx.t("photo.unavailable", name=pipeline.camera.name), buttons=(menu_row(ctx),)
        )
    ctx.app.last_photos[ctx.chat_key] = path
    when = format_time(ctx.app.clock(), ctx.app.settings.tz, ctx.lang)
    return Reply(
        ctx.t("photo.caption", name=pipeline.camera.name, time=when),
        photo=path,
        buttons=photo_buttons(ctx, pipeline.camera.id),
    )


async def _all_photos(ctx: CommandContext) -> Reply:
    """One photo per camera; the last one carries the buttons."""
    when = format_time(ctx.app.clock(), ctx.app.settings.tz, ctx.lang)
    replies = []
    for camera_id, pipeline in ctx.app.cameras.items():
        try:
            path = await ctx.app.take_photo(camera_id)
        except SnapshotUnavailableError:
            replies.append(Reply(ctx.t("photo.unavailable", name=pipeline.camera.name)))
            continue
        ctx.app.last_photos[ctx.chat_key] = path
        replies.append(Reply(ctx.t("photo.caption", name=pipeline.camera.name, time=when),
                             photo=path))  # fmt: skip
    for reply in replies[:-1]:
        await ctx.send(reply)
    last = replies[-1]
    return Reply(last.text, photo=last.photo, buttons=(
        (ctx.button("photo.all_again", "photo all"),), menu_row(ctx)))  # fmt: skip


async def video(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    seconds = args.get("seconds", DEFAULT_VIDEO_SECONDS)
    pipeline = ctx.camera(args)
    if pipeline is None:
        return Reply(
            ctx.t("camera.choose"),
            buttons=(*ctx.camera_choice("video", str(seconds)), menu_row(ctx)),
        )
    if pipeline.segment_dir is None:
        return Reply(ctx.t("video.disabled"), buttons=(menu_row(ctx),))
    await ctx.progress(ctx.t("video.preparing", seconds=seconds))
    try:
        path = await ctx.app.clip(pipeline.camera.id, seconds)
    except ClipError:
        return Reply(ctx.t("video.failed", name=pipeline.camera.name), buttons=(menu_row(ctx),))
    return Reply(
        ctx.t("video.caption", name=pipeline.camera.name, seconds=seconds),
        video=path,
        buttons=(
            (
                ctx.button("video.longer", f"video {pipeline.camera.id} 60"),
                ctx.button("menu.photo", f"photo {pipeline.camera.id}"),
            ),
            menu_row(ctx),
        ),
    )


async def status(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    app = ctx.app
    lines = [camera_summary(ctx), recording_summary(ctx), mode_summary(ctx)]
    last = app.db.recent_events(1, ["motion_detected"])
    if last:
        lines.append(
            ctx.t("status.last_motion", time=format_time(last[0].ts, app.settings.tz, ctx.lang))
        )
    if not ctx.simple or ctx.user.role is Role.ADMIN:
        lines.append("")
        lines.extend(technical_status(ctx))
    return Reply(
        "\n".join(lines),
        buttons=(
            (ctx.button("menu.photo", "photo"), ctx.button("menu.video", "video")),
            menu_row(ctx),
        ),
    )


def technical_status(ctx: CommandContext) -> list[str]:
    from pi_guard.diagnostics.system import system_snapshot

    app = ctx.app
    snap = system_snapshot(app.settings.recordings_path)
    lines = [
        ctx.t(
            "status.storage",
            percent=round(snap.disk_used_percent),
            free=round(snap.disk_free_gb, 1),
        ),
        ctx.t("status.system", load=f"{snap.load1:.2f}", memory=round(snap.memory_used_percent)),
    ]
    if snap.temperature_c is not None:
        lines.append(ctx.t("status.temperature", value=round(snap.temperature_c)))
    lines.append(ctx.t("status.uptime", duration=format_duration(app.uptime_seconds, ctx.lang)))
    if app.presence is not None:
        lines.append(
            ctx.t(
                "status.presence", state=ctx.t(f"presence.{app.modes.state.presence.value.lower()}")
            )
        )
    failing = [c.name for c in app.components.values() if not c.running and c.restarts]
    if failing:
        lines.append(ctx.t("status.components_failing", names=", ".join(failing)))
    return lines


async def cameras(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    if not ctx.app.cameras:
        return Reply(ctx.t("status.no_cameras"), buttons=(menu_row(ctx),))
    lines = [ctx.t("cameras.title")]
    for pipeline in ctx.app.cameras.values():
        state = ctx.t(f"camera.state.{pipeline.status.state.value}")
        lines.append(f"• {pipeline.camera.name}: {state}")
    rows = tuple((Button(p.camera.name, f"camera {cid}"),) for cid, p in ctx.app.cameras.items())
    return Reply("\n".join(lines), buttons=(*rows, menu_row(ctx)))


async def camera(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    pipeline = ctx.camera(args)
    if pipeline is None:
        return Reply(ctx.t("camera.choose"), buttons=(*ctx.camera_choice("camera"), menu_row(ctx)))
    cam, st = pipeline.camera, pipeline.status
    lines = [
        cam.name,
        ctx.t("camera.detail.state", state=ctx.t(f"camera.state.{st.state.value}")),
        ctx.t("camera.detail.video", resolution=cam.resolution, fps=cam.fps),
        ctx.t(
            "camera.detail.recording_on" if pipeline.segment_dir else "camera.detail.recording_off"
        ),
        ctx.t(
            "camera.detail.motion_on" if cam.id in ctx.app.motion else "camera.detail.motion_off"
        ),
    ]
    if ctx.user.role is Role.ADMIN:
        lines.append(
            ctx.t(
                "camera.detail.device",
                device=cam.device,
                input=st.input_format or "-",
                encoder=st.encoder or "-",
            )
        )
        if st.last_error:
            lines.append(ctx.t("camera.detail.error", error=st.last_error[:200]))
    return Reply(
        "\n".join(lines),
        buttons=(
            (
                ctx.button("menu.photo", f"photo {cam.id}"),
                ctx.button("menu.video", f"video {cam.id}"),
            ),
            menu_row(ctx),
        ),
    )


async def events(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    stored = ctx.app.db.recent_events(args.get("count", 10), EVENT_TYPES_SHOWN)
    if not stored:
        return Reply(ctx.t("events.none"), buttons=(menu_row(ctx),))
    names = ctx.camera_names()
    lines = [ctx.t("events.title")]
    for event in stored:
        when = format_time(event.ts, ctx.app.settings.tz, ctx.lang, with_date=True)
        label = ctx.t(f"event.{event.type}")
        where = f" – {names.get(event.camera, event.camera)}" if event.camera else ""
        lines.append(f"{when}  {label}{where}")
    return Reply("\n".join(lines), buttons=(menu_row(ctx),))


async def notifications(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    choice = args.get("state")
    user = ctx.user
    if choice is not None:
        user = replace(user, notifications=choice == "on")
        ctx.app.users.save(user)
        ctx.user = user
    key = "notifications.on" if user.notifications else "notifications.off"
    toggle = (
        ("notifications.turn_off", "notifications off")
        if user.notifications
        else ("notifications.turn_on", "notifications on")
    )
    return Reply(ctx.t(key), buttons=((ctx.button(*toggle),), menu_row(ctx)))


async def settings(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    language = ctx.t(f"language.{ctx.lang}")
    layout = ctx.t(f"layout.{ctx.user.layout}")
    return Reply(
        ctx.t("settings.text", language=language, layout=layout),
        buttons=(
            (Button("Deutsch", "language de"), Button("English", "language en")),
            (
                ctx.button("layout.simple_button", "layout simple"),
                ctx.button("layout.advanced_button", "layout advanced"),
            ),
            (ctx.button("menu.notifications", "notifications"),),
            menu_row(ctx),
        ),
    )


async def language(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    if "language" in args:
        ctx.user = replace(ctx.user, language=args["language"])
        ctx.app.users.save(ctx.user)
    return await settings(ctx, {})


async def layout(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    if "layout" in args:
        ctx.user = replace(ctx.user, layout=args["layout"])
        ctx.app.users.save(ctx.user)
    return await settings(ctx, {})


async def help_(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    if ctx.simple:
        return Reply(ctx.t("help.simple"), buttons=(menu_row(ctx),))
    lines = [ctx.t("help.title")]
    for command in ctx.app.registry:
        if command.help_key and ctx.user.role.allows(command.role):
            aliases = ", ".join(command.aliases[:3])
            suffix = f" ({aliases})" if aliases else ""
            lines.append(f"/{command.name}{suffix} – {ctx.t(command.help_key)}")
    lines.append("")
    lines.append(ctx.t("help.plain_text"))
    return Reply("\n".join(lines), buttons=(menu_row(ctx),))


def register(registry: Registry) -> None:
    registry.add(
        Command("menu", menu, ("start", "m", "menü", "menue", "hauptmenü"), help_key="help.menu")
    )
    registry.add(Command("more", more, ("mehr", "weitere"), menu=False))
    registry.add(
        Command(
            "photo",
            photo,
            ("p", "foto", "bild", "snapshot", "fotos", "bilder"),
            (ChoiceArg("all", {"all": "all", "alle": "all"}), CameraArg()),
            help_key="help.photo",
        )
    )
    registry.add(
        Command(
            "video",
            video,
            ("v", "clip", "film"),
            (CameraArg(), IntArg("seconds", 3, MAX_VIDEO_SECONDS)),
            help_key="help.video",
        )
    )
    registry.add(
        Command("status", status, ("s", "zustand", "kamerastatus"), help_key="help.status")
    )
    registry.add(Command("cameras", cameras, ("kameras",), help_key="help.cameras"))
    registry.add(
        Command("camera", camera, ("kamera", "cam"), (CameraArg(),), help_key="help.camera")
    )
    registry.add(
        Command(
            "events",
            events,
            ("ereignisse", "e", "verlauf"),
            (IntArg("count", 1, 50),),
            help_key="help.events",
        )
    )
    on_off = {"on": "on", "an": "on", "ein": "on", "off": "off", "aus": "off"}
    registry.add(
        Command(
            "notifications",
            notifications,
            ("benachrichtigungen", "alerts", "meldungen"),
            (ChoiceArg("state", on_off),),
            help_key="help.notifications",
        )
    )
    registry.add(Command("settings", settings, ("einstellungen",), help_key="help.settings"))
    registry.add(
        Command(
            "language",
            language,
            ("sprache", "lang"),
            (
                ChoiceArg(
                    "language",
                    {"de": "de", "deutsch": "de", "en": "en", "english": "en", "englisch": "en"},
                ),
            ),
            menu=False,
        )
    )
    registry.add(
        Command(
            "layout",
            layout,
            ("ansicht",),
            (
                ChoiceArg(
                    "layout",
                    {
                        "simple": "simple",
                        "einfach": "simple",
                        "advanced": "advanced",
                        "erweitert": "advanced",
                    },
                ),
            ),
            menu=False,
        )
    )
    registry.add(Command("help", help_, ("h", "hilfe", "?"), help_key="help.help"))

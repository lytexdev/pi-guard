"""HOME/AWAY commands and presence overview."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pi_guard.core.modes import AutoUnavailableError, Mode, Selection
from pi_guard.core.permissions import Role
from pi_guard.i18n import format_time
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.core import menu_row, mode_summary
from pi_guard.messaging.commands.registry import ChoiceArg, Command, DurationArg, Registry


def _mode_reply(ctx: CommandContext, text: str) -> Reply:
    rows: list[tuple[Button, ...]] = [
        (ctx.button("mode.button_home", "home"), ctx.button("mode.button_away", "away"))
    ]
    if ctx.app.modes.presence_enabled and ctx.app.modes.state.selection is not Selection.AUTO:
        rows.append((ctx.button("mode.button_auto", "mode auto"),))
    rows.append(menu_row(ctx))
    return Reply(text, buttons=tuple(rows))


def _set(ctx: CommandContext, mode: Mode, duration: timedelta | None) -> Reply:
    ctx.app.modes.set_manual(mode, duration, actor=ctx.user.key)
    key = "mode.set_home" if mode is Mode.HOME else "mode.set_away"
    text = ctx.t(key)
    until = ctx.app.modes.state.manual_until
    if until:
        text += "\n" + ctx.t("mode.until", time=format_time(until, ctx.app.settings.tz, ctx.lang))
    elif ctx.app.modes.presence_enabled:
        text += "\n" + ctx.t("mode.manual_hint")
    return _mode_reply(ctx, text)


async def mode(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    target = args.get("mode")
    if target is None:
        return _mode_reply(ctx, mode_summary(ctx))
    if target == "auto":
        try:
            ctx.app.modes.set_auto(actor=ctx.user.key)
        except AutoUnavailableError:
            return _mode_reply(ctx, ctx.t("mode.auto_unavailable"))
        return _mode_reply(ctx, ctx.t("mode.set_auto") + "\n" + mode_summary(ctx))
    return _set(ctx, Mode(target), args.get("duration"))


async def home(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    # Saying "I am at home" also means the alerts so far were fine.
    ctx.app.escalation.acknowledge(ctx.user.key)
    return _set(ctx, Mode.HOME, args.get("duration"))


async def ok(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    count = ctx.app.escalation.acknowledge(ctx.user.key)
    text = ctx.t("alert.acked", count=count) if count else ctx.t("alert.none_open")
    return Reply(text, buttons=(menu_row(ctx),))


async def away(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    return _set(ctx, Mode.AWAY, args.get("duration"))


async def presence(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    monitor = ctx.app.presence
    if monitor is None:
        return Reply(ctx.t("presence.disabled"), buttons=(menu_row(ctx),))
    state = ctx.app.modes.state.presence
    lines = [ctx.t("presence.summary", state=ctx.t(f"presence.{state.value.lower()}"))]
    tz = ctx.app.settings.tz
    if not monitor.devices():
        lines.append(ctx.t("presence.no_devices"))
    for device in monitor.devices():
        seen = (
            format_time(device.last_seen, tz, ctx.lang, with_date=True)
            if device.last_seen
            else ctx.t("presence.never")
        )
        lines.append(ctx.t("presence.device", name=device.name, seen=seen))
    lines.append("")
    lines.append(ctx.t("presence.disclaimer"))
    return Reply("\n".join(lines), buttons=(menu_row(ctx),))


def register(registry: Registry) -> None:
    modes = {
        "home": "HOME",
        "zuhause": "HOME",
        "daheim": "HOME",
        "away": "AWAY",
        "abwesend": "AWAY",
        "weg": "AWAY",
        "auto": "auto",
        "automatisch": "auto",
    }
    registry.add(
        Command(
            "mode",
            mode,
            ("modus",),
            (ChoiceArg("mode", modes), DurationArg("duration")),
            help_key="help.mode",
        )
    )
    registry.add(
        Command(
            "home",
            home,
            ("zuhause", "daheim", "da"),
            (DurationArg("duration"),),
            help_key="help.home",
        )
    )
    registry.add(
        Command(
            "away",
            away,
            ("abwesend", "weg", "unterwegs"),
            (DurationArg("duration"),),
            help_key="help.away",
        )
    )
    registry.add(Command("ok", ok, ("okay", "entwarnung", "allesok", "passt"), help_key="help.ok"))
    registry.add(
        Command("presence", presence, ("anwesenheit",), role=Role.ADMIN, help_key="help.presence")
    )

"""User administration through chat: invites and removal."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pi_guard.core.permissions import Role
from pi_guard.i18n import format_time
from pi_guard.messaging.base import Button, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.core import menu_row
from pi_guard.messaging.commands.registry import ArgError, ChoiceArg, Command, Registry, TextArg

INVITE_TTL = timedelta(hours=24)


async def invite(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    role = Role(args.get("role", "viewer"))
    code, expires = ctx.app.users.create_invite(role, ctx.user.key, INVITE_TTL)
    until = format_time(expires, ctx.app.settings.tz, ctx.lang, with_date=True)
    lines = [ctx.t("invite.created", role=ctx.t(f"role.{role.value}"), until=until)]
    telegram = ctx.app.providers.get("telegram")
    username = getattr(telegram, "username", None)
    if username:
        lines.append(ctx.t("invite.telegram_link", link=f"https://t.me/{username}?start={code}"))
    lines.append(ctx.t("invite.code", code=code))
    return Reply("\n\n".join(lines), buttons=(menu_row(ctx),))


async def users(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    lines = [ctx.t("users.title")]
    rows = []
    for user in ctx.app.users.all():
        name = user.name or user.user_id
        tag = ctx.t("users.configured") if user.configured else ""
        lines.append(
            f"• {name} ({user.provider}, {ctx.t(f'role.{user.role.value}')}) {tag}".rstrip()
        )
        if not user.configured:
            rows.append(
                (
                    Button(
                        ctx.t("users.remove", name=name),
                        f"removeuser {user.provider}:{user.user_id}",
                    ),
                )
            )
    return Reply(
        "\n".join(lines), buttons=(*rows, (ctx.button("more.invite", "invite"),), menu_row(ctx))
    )


async def remove_user(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    provider, _, user_id = args.get("user", "").partition(":")
    if not user_id:
        raise ArgError("error.missing_argument")
    if not ctx.app.users.remove(provider, user_id):
        return Reply(ctx.t("users.remove_failed"), buttons=(menu_row(ctx),))
    ctx.app.db.audit(ctx.user.key, "user.remove", f"{provider}:{user_id}")
    return Reply(ctx.t("users.removed"), buttons=(menu_row(ctx),))


async def weblogin(ctx: CommandContext, args: dict[str, Any]) -> Reply:
    """Answer to a dashboard login that waits for confirmation."""
    choice, _, request_id = args.get("text", "").partition(" ")
    board = ctx.app.dashboard
    if board is None or ctx.user.provider != "telegram" or choice not in ("yes", "no"):
        return Reply(ctx.t("web.tg_login_expired"), buttons=(menu_row(ctx),))
    pending = board.logins.answer(request_id, choice == "yes")
    if pending is None:
        return Reply(ctx.t("web.tg_login_expired"), buttons=(menu_row(ctx),))
    ctx.app.db.audit(ctx.user.key, "login.telegram_answer", f"{choice} {pending.ip}")
    return Reply(ctx.t("web.tg_login_done" if choice == "yes" else "web.tg_login_refused"))


def register(registry: Registry) -> None:
    registry.add(Command("weblogin", weblogin, (), (TextArg("text"),), role=Role.ADMIN, menu=False))
    roles = {"viewer": "viewer", "betrachter": "viewer", "admin": "admin"}
    registry.add(
        Command(
            "invite",
            invite,
            ("einladen",),
            (ChoiceArg("role", roles),),
            role=Role.ADMIN,
            help_key="help.invite",
        )
    )
    registry.add(Command("users", users, ("benutzer",), role=Role.ADMIN, help_key="help.users"))
    registry.add(
        Command(
            "removeuser",
            remove_user,
            ("entfernen",),
            (TextArg("user"),),
            role=Role.ADMIN,
            menu=False,
        )
    )

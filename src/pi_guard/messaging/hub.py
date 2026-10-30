"""Routes incoming chat messages to commands and sends replies.

Authorization happens here, once, for every provider: unknown senders, chats
that are not allowed and users without the required role never reach a
command handler.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from pi_guard.core.permissions import Role, User
from pi_guard.i18n import t
from pi_guard.messaging.base import Button, Incoming, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.registry import ArgError, ParsedCommand, bind_args, normalize
from pi_guard.security.ratelimit import RateLimiter

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

_INVITE_RE = re.compile(r"^[0-9a-f]{12}$")
_UNKNOWN_REPLY_INTERVAL = 3600.0


def flatten(reply: Reply) -> list[Button]:
    return [button for row in reply.buttons for button in row]


class Messenger:
    def __init__(self, app: App) -> None:
        self._app = app
        self._limiter = RateLimiter(per_minute=20, burst=8)
        self._unknown_replied: dict[str, float] = {}
        self._menus: dict[tuple[str, str], list[str]] = {}

    async def handle(self, msg: Incoming) -> None:
        try:
            await self._handle(msg)
        finally:
            if msg.attachment is not None:
                msg.attachment.unlink(missing_ok=True)

    async def _handle(self, msg: Incoming) -> None:
        app = self._app
        sender = f"{msg.provider}:{msg.sender_id}"
        if msg.is_group and not self._group_allowed(msg):
            return
        user = app.users.get(msg.provider, msg.sender_id)

        parsed = self._parse(msg)
        if user is None:
            await self._handle_stranger(msg, parsed, sender)
            return
        if not self._limiter.allow(sender):
            log.warning("rate limit hit by %s", sender)
            return
        if parsed is None:
            if msg.attachment_kind == "audio":
                parsed = app.registry.parse("upload")
            elif msg.is_group and not msg.from_button:
                return
        if parsed is None:
            await self._reply(
                msg,
                user,
                Reply(
                    t("error.unknown_command", self._lang(user)),
                    buttons=((Button(t("button.menu", self._lang(user)), "menu"),),),
                ),
            )
            return
        if parsed.command.name == "menu" and parsed.tokens:
            await self._redeem(msg, parsed.tokens[0], sender)
            return
        if not user.role.allows(parsed.command.role):
            app.db.audit(sender, "denied", parsed.command.name)
            await self._reply(msg, user, Reply(t("auth.admin_only", self._lang(user))))
            return
        await self._execute(msg, user, parsed)

    def _parse(self, msg: Incoming) -> ParsedCommand | None:
        text = msg.text
        provider = self._app.providers.get(msg.provider)
        if provider is not None and not provider.supports_buttons:
            # Providers without buttons show numbered menus; "2" picks the second entry.
            number = text.strip()
            menu = self._menus.get((msg.provider, msg.chat_id))
            if number.isdigit() and menu and 1 <= int(number) <= len(menu):
                text = menu[int(number) - 1]
        return self._app.registry.parse(text, getattr(provider, "username", None))

    def _group_allowed(self, msg: Incoming) -> bool:
        s = self._app.settings
        if msg.provider == "telegram":
            if int(msg.chat_id) not in s.telegram_allowed_chat_ids:
                return False
            return (
                msg.from_button or msg.text.lstrip().startswith("/") or s.telegram_group_plain_text
            )
        return False  # Signal groups are not supported as command channels

    async def _handle_stranger(
        self, msg: Incoming, parsed: ParsedCommand | None, sender: str
    ) -> None:
        token = normalize(msg.text).split()
        code = None
        if parsed and parsed.command.name == "menu" and parsed.tokens:
            code = parsed.tokens[0]
        elif len(token) == 1 and _INVITE_RE.match(token[0]):
            code = token[0]
        if code and self._limiter.allow(f"invite:{sender}"):
            await self._redeem(msg, code, sender)
            return
        log.info("rejected message from unknown sender %s", sender)
        self._app.db.audit(sender, "rejected", msg.provider)
        last = self._unknown_replied.get(sender, 0.0)
        if not msg.is_group and time.monotonic() - last > _UNKNOWN_REPLY_INTERVAL:
            self._unknown_replied[sender] = time.monotonic()
            lang = self._app.settings.language
            await self._send_safely(
                msg.provider, msg.chat_id, Reply(t("auth.unknown", lang, id=msg.sender_id))
            )

    async def _redeem(self, msg: Incoming, code: str, sender: str) -> None:
        if msg.is_group:
            return
        user = self._app.users.redeem(code, msg.provider, msg.sender_id, msg.sender_name)
        lang = self._app.settings.language
        if user is None:
            log.info("invalid invite code from %s", sender)
            await self._send_safely(msg.provider, msg.chat_id, Reply(t("invite.invalid", lang)))
            return
        log.info("%s joined as %s", sender, user.role)
        await self._notify_admins(t("invite.joined_admin", lang, name=user.name or user.user_id))
        ctx = self._context(msg, user)
        welcome = await self._app.registry.get("menu").handler(ctx, {})  # type: ignore[union-attr]
        await self._reply(
            msg,
            user,
            Reply(t("invite.welcome", lang) + "\n\n" + welcome.text, buttons=welcome.buttons),
        )

    async def _execute(self, msg: Incoming, user: User, parsed: ParsedCommand) -> None:
        ctx = self._context(msg, user)
        command = parsed.command
        try:
            args = bind_args(command.args, parsed.tokens, ctx, parsed.raw_tokens)
            reply = await command.handler(ctx, args)
        except ArgError as exc:
            reply = Reply(
                ctx.t(exc.key, **exc.values), buttons=((ctx.button("button.menu", "menu"),),)
            )
        except Exception:
            log.exception("command %s failed", command.name)
            reply = Reply(ctx.t("error.internal"), buttons=((ctx.button("button.menu", "menu"),),))
        if command.role is Role.ADMIN:
            self._app.db.audit(user.key, "command", command.name)
        await self._reply(msg, ctx.user, reply)

    def _context(self, msg: Incoming, user: User) -> CommandContext:
        async def progress(text: str) -> None:
            await self._send_safely(msg.provider, msg.chat_id, Reply(text))

        return CommandContext(
            self._app,
            user,
            msg.chat_id,
            msg.is_group,
            progress,
            attachment=msg.attachment,
            attachment_meta=msg.attachment_meta,
        )

    def _lang(self, user: User) -> str:
        return user.language or self._app.settings.language

    async def _reply(self, msg: Incoming, user: User, reply: Reply) -> None:
        await self._send_safely(msg.provider, msg.chat_id, reply)

    async def send(self, provider_name: str, chat_id: str, reply: Reply) -> None:
        """Send or raise; used by the notifier, which handles retries."""
        provider = self._app.providers[provider_name]
        if not provider.supports_buttons and reply.buttons:
            self._menus[(provider_name, chat_id)] = [b.command for b in flatten(reply)]
        await provider.send(chat_id, reply)

    async def _send_safely(self, provider_name: str, chat_id: str, reply: Reply) -> None:
        try:
            await self.send(provider_name, chat_id, reply)
        except Exception as exc:
            log.warning("could not reply via %s: %s", provider_name, exc)

    async def _notify_admins(self, text: str) -> None:
        for user in self._app.users.all():
            if user.role is Role.ADMIN and user.provider in self._app.providers:
                await self._send_safely(user.provider, user.user_id, Reply(text))

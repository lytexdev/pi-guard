"""Routes incoming chat messages to commands and sends replies.

Authorization happens here, once, for every provider: unknown senders, chats
that are not allowed and users without the required role never reach a
command handler.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from pi_guard.core.permissions import Role, User
from pi_guard.i18n import t
from pi_guard.messaging.base import Button, Incoming, Reply
from pi_guard.messaging.commands.context import CommandContext
from pi_guard.messaging.commands.registry import ArgError, ParsedCommand, bind_args, normalize
from pi_guard.messaging.intents import understand
from pi_guard.security.ratelimit import RateLimiter
from pi_guard.security.redact import mask_sender

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

_INVITE_RE = re.compile(r"^[0-9a-f]{12}$")
_UNKNOWN_REPLY_INTERVAL = 3600.0
_EXPECT_SECONDS = 600.0
_INTENT_LABELS = {
    "photo": "menu.photo",
    "video": "menu.video",
    "status": "menu.status",
    "help": "menu.help",
    "events": "more.events",
    "home": "mode.button_home",
    "away": "mode.button_away",
    "sharper": "image.sharper_button",
    "zoom": "image.zoom_button",
    "brighter": "image.brighter_button",
}


def flatten(reply: Reply) -> list[Button]:
    return [button for row in reply.buttons for button in row]


class Messenger:
    def __init__(self, app: App) -> None:
        self._app = app
        self._limiter = RateLimiter(per_minute=20, burst=8)
        self._unknown_replied: dict[str, float] = {}
        self._menus: dict[tuple[str, str], list[str]] = {}
        self._expect: dict[tuple[str, str, str], tuple[str, float]] = {}

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
            log.warning("rate limit hit by %s", mask_sender(sender))
            return
        if parsed is None and msg.attachment_meta.get("too_large"):
            await self._reply(msg, user, Reply(t("error.file_too_large", self._lang(user))))
            return
        if parsed is None:
            parsed = self._follow_up(msg)
        if parsed is None and not msg.is_group and msg.attachment is None:
            understood = understand(
                msg.text, {cid: p.camera.name for cid, p in app.cameras.items()}
            )
            if understood.command:
                parsed = app.registry.parse(understood.command)
            elif understood.options:
                await self._reply(msg, user, self._suggest(user, understood.options))
                return
        if parsed is None:
            if msg.attachment_kind == "audio":
                parsed = app.registry.parse("upload")
            elif msg.attachment_kind in ("photo", "video") and app.faces is not None:
                parsed = app.registry.parse("enroll add")
            elif msg.is_group and not msg.from_button:
                return
            elif (
                app.addons.assistant is not None
                and msg.text.strip()
                and not msg.text.lstrip().startswith("/")
                and not msg.is_group
            ):
                # Anything that is no command is a question for the assistant.
                parsed = app.registry.parse(f"ask {msg.text}")
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

    def _suggest(self, user: User, intents: tuple[str, ...]) -> Reply:
        lang = self._lang(user)
        rows = tuple(
            (Button(t(_INTENT_LABELS[i], lang), i),) for i in intents if i in _INTENT_LABELS
        )
        return Reply(
            t("intent.which", lang), buttons=(*rows, (Button(t("button.menu", lang), "menu"),))
        )

    def _follow_up(self, msg: Incoming) -> ParsedCommand | None:
        """A plain-text answer to a question the bot asked in this chat."""
        pending = self._expect.pop((msg.provider, msg.chat_id, msg.sender_id), None)
        if pending is None or msg.attachment is not None or time.monotonic() > pending[1]:
            return None
        return self._app.registry.parse(f"{pending[0]} {msg.text}")

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
        if self._app.escalation.is_contact(msg.provider, msg.sender_id) and not msg.is_group:
            last = self._unknown_replied.get(sender, 0.0)
            if time.monotonic() - last > _UNKNOWN_REPLY_INTERVAL:
                self._unknown_replied[sender] = time.monotonic()
                lang = self._app.settings.language
                await self._send_safely(msg.provider, msg.chat_id,
                                        Reply(t("emergency.registered", lang)))  # fmt: skip
            return
        log.info("rejected message from unknown sender %s", mask_sender(sender))
        self._app.db.audit(sender, "rejected", msg.provider)
        if len(self._unknown_replied) > 1000:  # bound memory if many strangers write
            self._unknown_replied.clear()
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
            log.info("invalid invite code from %s", mask_sender(sender))
            await self._send_safely(msg.provider, msg.chat_id, Reply(t("invite.invalid", lang)))
            return
        log.info("%s joined as %s", mask_sender(sender), user.role)
        await self.notify_admins(t("invite.joined_admin", lang, name=user.name or user.user_id))
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
        key = (msg.provider, msg.chat_id, msg.sender_id)
        if reply.expect:
            self._expect[key] = (reply.expect, time.monotonic() + _EXPECT_SECONDS)
        else:
            self._expect.pop(key, None)
        await self._reply(msg, ctx.user, reply)

    def _context(self, msg: Incoming, user: User) -> CommandContext:
        async def send(reply: Reply) -> None:
            await self._send_safely(msg.provider, msg.chat_id, reply)

        async def progress(text: str) -> None:
            await send(Reply(text))

        return CommandContext(
            self._app,
            user,
            msg.chat_id,
            msg.is_group,
            progress,
            send,
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
        # Answers to commands get two more tries: a short network hiccup must not mean
        # that the user gets no answer at all.
        for attempt, pause in enumerate((2.0, 5.0, None)):
            try:
                await self.send(provider_name, chat_id, reply)
                return
            except Exception as exc:
                if pause is None or not _transient(exc):
                    log.warning("could not reply via %s: %s", provider_name, exc)
                    return
                log.info("reply via %s failed (%s), try %d again", provider_name, exc, attempt + 2)
                await asyncio.sleep(pause)

    async def notify_admins(self, text: str) -> None:
        for user in self._app.users.all():
            if user.role is Role.ADMIN and user.provider in self._app.providers:
                await self._send_safely(user.provider, user.user_id, Reply(text))


def _transient(exc: Exception) -> bool:
    """Timeouts and connection problems, not refusals (blocked bot, bad request)."""
    name = type(exc).__name__
    return isinstance(exc, (TimeoutError, OSError)) or name in ("TimedOut", "NetworkError")

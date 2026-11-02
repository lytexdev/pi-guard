"""Telegram via python-telegram-bot, using long polling (no inbound ports)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.error import Forbidden, NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from pi_guard.messaging.base import AttachmentKind, Incoming, ProviderUnavailableError, Reply
from pi_guard.messaging.base import MessageHandler as OnMessage
from pi_guard.security.files import secure_dir

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
CALLBACK_LIMIT = 64
UPLOAD_TIMEOUT = 120
TELEGRAM_DOWNLOAD_LIMIT = 20_000_000  # what the Bot API lets bots download


class TelegramProvider:
    name = "telegram"
    supports_buttons = True

    def __init__(
        self,
        token: str,
        upload_dir: Path,
        max_upload_bytes: int,
        may_upload: Callable[[str], bool],
        command_menu: dict[str, list[tuple[str, str]]],
    ) -> None:
        self._token = token
        self._upload_dir = upload_dir
        self._max_upload_bytes = max_upload_bytes
        self._may_upload = may_upload
        self._command_menu = command_menu
        self._application: Application[Any, Any, Any, Any, Any, Any] | None = None
        self._on_message: OnMessage | None = None
        self.username: str | None = None

    @property
    def connected(self) -> bool:
        return self._application is not None

    async def run(self, on_message: OnMessage) -> None:
        self._on_message = on_message
        application = Application.builder().token(self._token).concurrent_updates(True).build()
        application.add_handler(CallbackQueryHandler(self._on_callback))
        application.add_handler(MessageHandler(filters.TEXT, self._on_text))
        application.add_handler(
            MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.AUDIO, self._on_audio)
        )
        application.add_handler(
            MessageHandler(filters.PHOTO | filters.Document.IMAGE, self._on_photo)
        )
        application.add_handler(
            MessageHandler(
                filters.VIDEO | filters.VIDEO_NOTE | filters.Document.VIDEO, self._on_video
            )
        )
        async with application:
            me = await application.bot.get_me()
            self.username = me.username
            await self._register_commands(application)
            await application.start()
            assert application.updater is not None
            # Old commands queued while offline (e.g. /photo from hours ago) are dropped.
            await application.updater.start_polling(
                drop_pending_updates=True, allowed_updates=["message", "callback_query"]
            )
            self._application = application
            log.info("telegram connected as @%s", self.username)
            try:
                await asyncio.Event().wait()
            finally:
                self._application = None
                await application.updater.stop()
                await application.stop()

    async def _register_commands(
        self, application: Application[Any, Any, Any, Any, Any, Any]
    ) -> None:
        for language, commands in self._command_menu.items():
            await application.bot.set_my_commands(
                [BotCommand(name, description[:256]) for name, description in commands],
                language_code=language,
            )

    def _incoming(self, message: Message, text: str, **extra: Any) -> Incoming | None:
        user = message.from_user
        if user is None or user.is_bot:
            return None
        return Incoming(
            provider=self.name,
            sender_id=str(user.id),
            chat_id=str(message.chat.id),
            text=text,
            is_group=message.chat.type != "private",
            sender_name=user.full_name,
            language_hint=user.language_code,
            **extra,
        )

    async def _dispatch(self, incoming: Incoming | None) -> None:
        if incoming is not None and self._on_message is not None:
            await self._on_message(incoming)

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message and message.text:
            await self._dispatch(self._incoming(message, message.text))

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        message = query.message
        if not isinstance(message, Message) or not query.data:
            return
        user = query.from_user
        await self._dispatch(
            Incoming(
                provider=self.name,
                sender_id=str(user.id),
                chat_id=str(message.chat.id),
                text=query.data,
                is_group=message.chat.type != "private",
                sender_name=user.full_name,
                language_hint=user.language_code,
                from_button=True,
            )
        )

    async def _on_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None or message.from_user is None:
            return
        media = message.voice or message.audio or message.document
        if media is None or not self._may_upload(str(message.from_user.id)):
            await self._dispatch(self._incoming(message, message.caption or ""))
            return
        if (media.file_size or 0) > self._max_upload_bytes:
            await self._dispatch(
                self._incoming(message, message.caption or "", attachment_meta={"too_large": True})
            )
            return
        suffix = Path(getattr(media, "file_name", None) or "upload.ogg").suffix or ".ogg"
        path = await self._download(media.file_id, suffix)
        await self._dispatch(
            self._incoming(
                message,
                message.caption or "",
                attachment=path,
                attachment_kind="audio",
                attachment_meta={
                    "duration": getattr(media, "duration", None),
                    "mime": getattr(media, "mime_type", None),
                },
            )
        )

    async def _on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        if message is None:
            return
        if message.photo:
            await self._receive(message, message.photo[-1].file_id, None, ".jpg", "photo")
        elif message.document:
            suffix = Path(message.document.file_name or "image.jpg").suffix or ".jpg"
            await self._receive(
                message, message.document.file_id, message.document.file_size, suffix, "photo"
            )

    async def _on_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        media = message and (message.video or message.video_note or message.document)
        if message is None or not media:
            return
        await self._receive(message, media.file_id, media.file_size, ".mp4", "video")

    async def _receive(
        self,
        message: Message,
        file_id: str,
        size: int | None,
        suffix: str,
        kind: AttachmentKind,
    ) -> None:
        """Download an admin's photo or video; others only get their caption handled."""
        if message.from_user is None:
            return
        caption = message.caption or ""
        if not self._may_upload(str(message.from_user.id)):
            await self._dispatch(self._incoming(message, caption))
            return
        if (size or 0) > TELEGRAM_DOWNLOAD_LIMIT:
            await self._dispatch(
                self._incoming(message, caption, attachment_meta={"too_large": True})
            )
            return
        path = await self._download(file_id, suffix)
        await self._dispatch(
            self._incoming(message, caption, attachment=path, attachment_kind=kind)
        )

    async def _download(self, file_id: str, suffix: str) -> Path:
        assert self._application is not None
        secure_dir(self._upload_dir)
        path = self._upload_dir / f"{uuid.uuid4().hex}{suffix.lower()[:8]}"
        file = await self._application.bot.get_file(file_id)
        await file.download_to_drive(path)
        path.chmod(0o600)
        return path

    async def send(self, chat_id: str, reply: Reply) -> None:
        application = self._application
        if application is None:
            raise ProviderUnavailableError("telegram is not connected")
        bot = application.bot
        markup = self._markup(reply)
        chat = int(chat_id)
        text = reply.text
        caption = text if len(text) <= CAPTION_LIMIT else None
        try:
            if reply.media is not None and caption is None:
                await bot.send_message(chat, text)
            if reply.photo:
                with reply.photo.open("rb") as fh:
                    await bot.send_photo(
                        chat, fh, caption=caption, reply_markup=markup, write_timeout=UPLOAD_TIMEOUT
                    )
            elif reply.video:
                with reply.video.open("rb") as fh:
                    await bot.send_video(
                        chat,
                        fh,
                        caption=caption,
                        reply_markup=markup,
                        supports_streaming=True,
                        write_timeout=UPLOAD_TIMEOUT,
                        read_timeout=UPLOAD_TIMEOUT,
                    )
            elif reply.audio:
                with reply.audio.open("rb") as fh:
                    await bot.send_audio(
                        chat, fh, caption=caption, reply_markup=markup, write_timeout=UPLOAD_TIMEOUT
                    )
            else:
                await bot.send_message(chat, text, reply_markup=markup)
        except Forbidden as exc:
            log.warning("telegram chat %s blocked the bot or was deleted", chat_id)
            raise RuntimeError("recipient unreachable") from exc
        except (TimedOut, NetworkError) as exc:
            raise ProviderUnavailableError(str(exc)) from exc

    @staticmethod
    def _markup(reply: Reply) -> InlineKeyboardMarkup | None:
        rows = []
        for row in reply.buttons:
            buttons = []
            for button in row:
                if len(button.command.encode()) > CALLBACK_LIMIT:
                    log.error("button command too long for telegram: %s", button.command)
                    continue
                buttons.append(InlineKeyboardButton(button.label, callback_data=button.command))
            if buttons:
                rows.append(buttons)
        return InlineKeyboardMarkup(rows) if rows else None

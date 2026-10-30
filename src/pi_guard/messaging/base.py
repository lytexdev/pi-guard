"""Provider-independent message model.

Handlers return a `Reply`; each provider renders it with what the platform
offers (Telegram: inline buttons, Signal: a numbered text menu).
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class Button:
    label: str
    command: str


@dataclass(frozen=True)
class Reply:
    text: str
    photo: Path | None = None
    video: Path | None = None
    audio: Path | None = None
    buttons: tuple[tuple[Button, ...], ...] = ()

    @property
    def media(self) -> Path | None:
        return self.photo or self.video or self.audio

    def with_buttons(self, *rows: tuple[Button, ...]) -> Reply:
        return Reply(self.text, self.photo, self.video, self.audio, self.buttons + rows)


AttachmentKind = Literal["audio", "photo"]


@dataclass(frozen=True)
class Incoming:
    provider: str
    sender_id: str
    chat_id: str
    text: str
    is_group: bool = False
    sender_name: str = ""
    language_hint: str | None = None
    from_button: bool = False
    attachment: Path | None = None
    attachment_kind: AttachmentKind | None = None
    attachment_meta: dict[str, object] = field(default_factory=dict)


MessageHandler = Callable[[Incoming], Coroutine[None, None, None]]


class ProviderUnavailableError(Exception):
    """The provider is not connected; the caller may retry later."""


class Provider(Protocol):
    name: str
    supports_buttons: bool

    @property
    def connected(self) -> bool: ...

    async def run(self, on_message: MessageHandler) -> None:
        """Receive messages until cancelled; raise to be restarted by the supervisor."""

    async def send(self, chat_id: str, reply: Reply) -> None: ...

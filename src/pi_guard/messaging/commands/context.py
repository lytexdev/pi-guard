from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_guard.core.permissions import User
from pi_guard.i18n import t
from pi_guard.messaging.base import Button
from pi_guard.messaging.commands.registry import ArgError

if TYPE_CHECKING:
    from pi_guard.app import App
    from pi_guard.cameras.pipeline import CameraPipeline


@dataclass
class CommandContext:
    app: App
    user: User
    chat_id: str
    is_group: bool
    progress: Callable[[str], Awaitable[None]]
    attachment: Path | None = None
    attachment_meta: dict[str, object] = field(default_factory=dict)

    @property
    def lang(self) -> str:
        return self.user.language or self.app.settings.language

    @property
    def simple(self) -> bool:
        return self.user.layout == "simple"

    def t(self, key: str, /, **values: object) -> str:
        return t(key, self.lang, **values)

    def button(self, key: str, command: str, **values: object) -> Button:
        return Button(self.t(key, **values), command)

    def camera_names(self) -> dict[str, str]:
        return {cid: p.camera.name for cid, p in self.app.cameras.items()}

    def camera(self, args: dict[str, Any]) -> CameraPipeline | None:
        """The requested camera, the only camera, or None when the user must choose."""
        camera_id = args.get("camera")
        if camera_id:
            return self.app.cameras[camera_id]
        if not self.app.cameras:
            raise ArgError("error.no_cameras")
        if len(self.app.cameras) == 1:
            return next(iter(self.app.cameras.values()))
        return None

    def camera_choice(self, command: str, suffix: str = "") -> tuple[tuple[Button, ...], ...]:
        tail = f" {suffix}" if suffix else ""
        return tuple(
            (Button(p.camera.name, f"{command} {cid}{tail}"),)
            for cid, p in self.app.cameras.items()
        )

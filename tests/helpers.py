from __future__ import annotations

from pathlib import Path
from typing import Any

from pi_guard.app import App
from pi_guard.config import Config
from pi_guard.config.files import CameraConfig, FileConfig
from pi_guard.config.settings import Settings
from pi_guard.messaging.base import Incoming, MessageHandler, ProviderUnavailableError, Reply

TOKEN = "123456789:AAH" + "x" * 32
ADMIN = "1001"
VIEWER = "2002"
STRANGER = "3003"


class FakeProvider:
    def __init__(self, name: str = "telegram", buttons: bool = True) -> None:
        self.name = name
        self.supports_buttons = buttons
        self.sent: list[tuple[str, Reply]] = []
        self.fail = False
        self.username = "TestBot"

    @property
    def connected(self) -> bool:
        return not self.fail

    async def run(self, on_message: MessageHandler) -> None:
        raise NotImplementedError

    async def send(self, chat_id: str, reply: Reply) -> None:
        if self.fail:
            raise ProviderUnavailableError("down")
        self.sent.append((chat_id, reply))

    def last(self) -> Reply:
        return self.sent[-1][1]

    def texts(self) -> list[str]:
        return [r.text for _, r in self.sent]


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "telegram_bot_token": TOKEN,
        "telegram_admin_user_ids": [int(ADMIN)],
        "telegram_viewer_user_ids": [int(VIEWER)],
        "data_dir": tmp_path / "data",
        "runtime_dir": tmp_path / "run",
        "config_file": tmp_path / "config.yaml",
    }
    values.update(overrides)
    return Settings(**values)


def camera(camera_id: str = "living_room", name: str = "Living Room", **kw: Any) -> CameraConfig:
    return CameraConfig.model_validate(
        {"id": camera_id, "name": name, "device": f"/dev/v4l/by-id/{camera_id}"} | kw
    )


def make_app(
    tmp_path: Path,
    cameras: list[CameraConfig] | None = None,
    file: FileConfig | None = None,
    providers: dict[str, Any] | None = None,
    **settings: Any,
) -> App:
    cameras = [camera()] if cameras is None else cameras
    file = file or FileConfig(cameras=cameras)
    config = Config(make_settings(tmp_path, **settings), file)
    providers = providers if providers is not None else {"telegram": FakeProvider()}
    return App(config, providers=providers, cameras=cameras)


def msg(text: str, sender: str = ADMIN, chat: str | None = None, **kw: Any) -> Incoming:
    return Incoming(
        provider=kw.pop("provider", "telegram"),
        sender_id=sender,
        chat_id=chat or sender,
        text=text,
        **kw,
    )


def fake_snapshot(app: App, camera_id: str = "living_room") -> None:
    path = app.cameras[camera_id].snapshot_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8jpeg\xff\xd9")

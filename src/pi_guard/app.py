"""Wires the components together and runs them under supervision."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from pi_guard import __version__
from pi_guard.cameras import discovery
from pi_guard.cameras.pipeline import CameraPipeline
from pi_guard.config import Config
from pi_guard.config.files import CameraConfig
from pi_guard.core.events import Event, EventBus, EventType
from pi_guard.core.lifecycle import ComponentState, supervise
from pi_guard.core.modes import ModeController
from pi_guard.core.permissions import Role, UserStore
from pi_guard.core.timeutil import utcnow
from pi_guard.i18n import LANGUAGES, t
from pi_guard.messaging.base import Provider
from pi_guard.messaging.commands import build_registry
from pi_guard.messaging.hub import Messenger
from pi_guard.recording.retention import Retention
from pi_guard.recording.segments import recent_clip
from pi_guard.security.files import atomic_write, secure_dir
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 5
MAX_PARALLEL_CLIPS = 2


def discovered_cameras() -> list[CameraConfig]:
    """Used when config.yaml lists no cameras: every detected capture device, default settings."""
    cameras: list[CameraConfig] = []
    seen: set[str] = set()
    for device in discovery.discover():
        camera_id = device.slug
        while camera_id in seen:
            camera_id = f"{camera_id[:29]}_{len(seen)}"
        seen.add(camera_id)
        cameras.append(CameraConfig(id=camera_id, name=device.name, device=str(device.path)))
    return cameras


class App:
    def __init__(
        self,
        config: Config,
        providers: dict[str, Provider] | None = None,
        cameras: list[CameraConfig] | None = None,
    ) -> None:
        s = self.settings = config.settings
        self.config = config
        secure_dir(s.data_dir)
        self.media_dir = secure_dir(s.data_dir / "media")
        self.db = Database(s.data_dir / "pi-guard.db")
        self.bus = EventBus()
        self.users = UserStore(
            self.db,
            {
                "telegram": {str(uid): Role.VIEWER for uid in s.telegram_viewer_user_ids}
                | {str(uid): Role.ADMIN for uid in s.telegram_admin_user_ids},
                "signal": {n: Role.VIEWER for n in s.signal_allowed_numbers}
                | {n: Role.ADMIN for n in s.signal_admin_numbers},
            },
        )
        self.modes = ModeController(
            self.db, self.bus, s.default_mode, s.presence_enabled, config.file.presence.unknown_mode
        )
        if cameras is None:
            cameras = config.cameras
        self.cameras: dict[str, CameraPipeline] = {
            camera.id: self._pipeline(camera) for camera in cameras
        }
        self.motion: dict[str, Any] = {}
        self.presence: Any = None
        self.audio: Any = None
        self.faces: Any = None
        self.retention = Retention(config, self.db, self.bus)
        self.registry = build_registry()
        self.providers: dict[str, Provider] = providers if providers is not None else {}
        self.messenger = Messenger(self)
        self.components: dict[str, ComponentState] = {}
        self._started = time.monotonic()
        self._clip_slots = asyncio.Semaphore(MAX_PARALLEL_CLIPS)

    def _pipeline(self, camera: CameraConfig) -> CameraPipeline:
        s = self.settings
        detect = s.motion_enabled and camera.motion_detection
        return CameraPipeline(
            camera,
            self.bus,
            segment_dir=s.recordings_path / camera.id if s.recording_enabled else None,
            segment_seconds=s.recording_segment_seconds,
            runtime_dir=s.runtime_dir,
            analysis_fps=camera.motion.analysis_fps if detect else 1.0,
            microphone=camera.microphone if s.audio_recording_enabled else None,
        )

    @staticmethod
    def clock() -> datetime:
        return utcnow()

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._started

    def command_menu(self) -> dict[str, list[tuple[str, str]]]:
        return {
            lang: [(c.name, t(c.help_key, lang)) for c in self.registry if c.menu and c.help_key]
            for lang in LANGUAGES
        }

    async def take_photo(self, camera_id: str, prefix: str = "photo") -> Path:
        pipeline = self.cameras[camera_id]
        dest = self.media_dir / "snapshots" / pipeline.snapshot_name(prefix)
        return await pipeline.snapshot(dest)

    async def clip(self, camera_id: str, seconds: int, audio_only: bool = False) -> Path:
        pipeline = self.cameras[camera_id]
        assert pipeline.segment_dir is not None
        suffix = "m4a" if audio_only else "mp4"
        kind = "audio" if audio_only else "clips"
        out = self.media_dir / kind / f"{camera_id}_{utcnow():%Y%m%dT%H%M%S%fZ}.{suffix}"
        async with self._clip_slots:
            return await recent_clip(pipeline.segment_dir, seconds, out, audio_only=audio_only)

    async def _record_events(self, event: Event) -> None:
        data = dict(event.data)
        if event.snapshot:
            data["snapshot"] = event.snapshot.name
        await asyncio.to_thread(self.db.add_event, event.ts, event.type.value, event.camera, data)

    async def _heartbeat(self) -> None:
        """Expires timed mode overrides and proves liveness to `pi-guard health`."""
        path = secure_dir(self.settings.runtime_dir) / "heartbeat"
        while True:
            self.modes.tick()
            await asyncio.to_thread(
                atomic_write, path, datetime.now(UTC).isoformat().encode(), 0o644
            )
            await asyncio.sleep(HEARTBEAT_SECONDS)

    def _components(self) -> dict[str, Callable[[], Awaitable[None]]]:
        """Name -> coroutine factory for every long-running part."""
        parts: dict[str, Callable[[], Awaitable[None]]] = {
            "events": lambda: self.bus.consume("event-log", self._record_events, 500),
            "heartbeat": self._heartbeat,
            "retention": self.retention.run,
        }
        for camera_id, pipeline in self.cameras.items():
            parts[f"camera:{camera_id}"] = pipeline.run
        for name, provider in self.providers.items():
            parts[f"provider:{name}"] = partial(provider.run, self.messenger.handle)
        return parts

    async def run(self) -> None:
        log.info("pi-guard %s starting with %d camera(s)", __version__, len(self.cameras))
        tasks = []
        for name, factory in self._components().items():
            state = self.components[name] = ComponentState(name)
            tasks.append(asyncio.create_task(supervise(state, factory), name=name))
        self.bus.publish(Event(EventType.SYSTEM_STARTED, data={"version": __version__}))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.db.close()
            log.info("pi-guard stopped")


def build_app(config: Config) -> App:
    cameras = config.cameras
    if not config.file.cameras:
        cameras = discovered_cameras()
        log.warning(
            "no cameras configured; using detected devices: %s",
            ", ".join(c.id for c in cameras) or "none",
        )
    app = App(config, cameras=cameras)
    s = config.settings
    if s.telegram_enabled and s.telegram_bot_token:
        from pi_guard.messaging.telegram.provider import TelegramProvider

        def may_upload(user_id: str) -> bool:
            user = app.users.get("telegram", user_id)
            return user is not None and user.role is Role.ADMIN

        app.providers["telegram"] = TelegramProvider(
            s.telegram_bot_token.get_secret_value(),
            app.media_dir / "uploads",
            config.file.audio.max_upload_bytes,
            may_upload,
            app.command_menu(),
        )
    return app

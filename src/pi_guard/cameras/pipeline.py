"""Runs and watches the FFmpeg capture process of one camera.

The process is restarted with backoff when the camera disappears, stops
delivering frames or stops writing segments. Disconnect/reconnect events are
only published after a grace period so that a brief USB hiccup does not wake
anyone up.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import numpy as np
import numpy.typing as npt

from pi_guard.cameras import discovery
from pi_guard.cameras.ffmpeg import (
    CaptureChoice,
    LiveOutputs,
    build_command,
    choose_capture,
    detection_size,
    supports_atomic_snapshots,
)
from pi_guard.cameras.live import AudioFanout, LiveFrames, open_reader, read_chunks, read_mpjpeg
from pi_guard.config.files import CameraConfig
from pi_guard.core.events import Event, EventBus, EventType
from pi_guard.core.lifecycle import Backoff
from pi_guard.core.timeutil import utcnow
from pi_guard.recording.segments import latest_segment
from pi_guard.security.files import atomic_write

log = logging.getLogger(__name__)

Frame = npt.NDArray[np.uint8]
FrameHandler = Callable[[Frame, float], None]

FIRST_FRAME_TIMEOUT = 20.0
FRAME_TIMEOUT = 10.0
RECORDING_STALL_SECONDS = 45.0
DISCONNECT_GRACE_SECONDS = 20.0


async def _discard(stream: asyncio.StreamReader | None) -> None:
    if stream is not None:
        while await stream.read(65536):
            pass


class CameraState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"


class SnapshotUnavailableError(Exception):
    pass


@dataclass
class CameraStatus:
    state: CameraState = CameraState.STARTING
    since: float = 0.0
    last_frame_at: float | None = None
    restarts: int = 0
    last_error: str | None = None
    input_format: str | None = None
    encoder: str | None = None


class CameraPipeline:
    def __init__(
        self,
        camera: CameraConfig,
        bus: EventBus,
        *,
        segment_dir: Path | None,
        segment_seconds: int,
        runtime_dir: Path,
        analysis_fps: float,
        microphone: str | None = None,
        live_fps: float = 0,
        live_width: int = 960,
    ) -> None:
        self.camera = camera
        self.live_fps = live_fps
        self.live_width = live_width
        self.live_frames = LiveFrames()
        self.live_audio = AudioFanout()
        self.segment_dir = segment_dir
        self.segment_seconds = segment_seconds
        self.snapshot_path = runtime_dir / f"{camera.id}.jpg"
        self.analysis_fps = analysis_fps
        self.microphone = microphone
        self.status = CameraStatus(since=time.monotonic())
        self._bus = bus
        self._handlers: list[FrameHandler] = []
        self._stderr: deque[str] = deque(maxlen=20)
        self._failing_since: float | None = None
        self._announced_offline = False

    @property
    def online(self) -> bool:
        return self.status.state is CameraState.RUNNING

    @property
    def stderr_tail(self) -> list[str]:
        return list(self._stderr)

    def add_frame_handler(self, handler: FrameHandler) -> None:
        self._handlers.append(handler)

    async def run(self) -> None:
        backoff = Backoff(initial=2, maximum=60)
        if self.segment_dir:
            self.segment_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            started = time.monotonic()
            if not Path(self.camera.device).exists():
                self._failed(f"device {self.camera.device} not found")
            else:
                await self._run_once()
            if time.monotonic() - started > 60:
                backoff.reset()
            delay = backoff.next()
            self._check_offline()
            await asyncio.sleep(delay)
            self._check_offline()

    async def _run_once(self) -> None:
        device = await asyncio.to_thread(discovery.find, self.camera.device)
        try:
            choice = await asyncio.to_thread(choose_capture, self.camera, device)
        except ValueError as exc:
            self._failed(str(exc))
            return
        if choice.warning:
            log.warning("camera %s: %s", self.camera.id, choice.warning)
        self.status.input_format, self.status.encoder = choice.input_format, choice.encoder
        live, read_ends = self._live_pipes()
        try:
            process = await self._spawn(choice, live)
        finally:
            for fd in (live.video_fd, live.audio_fd) if live else ():
                if fd is not None:
                    os.close(fd)  # the child has its copy
        tasks = [asyncio.create_task(self._drain_stderr(process))]
        tasks += [asyncio.create_task(reader) for reader in await self._live_readers(read_ends)]
        try:
            await self._read_frames(process)
        finally:
            await self._stop(process)
            for task in tasks:
                task.cancel()
            self.status.restarts += 1

    def _live_pipes(self) -> tuple[LiveOutputs | None, list[int]]:
        if self.live_fps <= 0:
            return None, []
        video_r, video_w = os.pipe()
        audio_r, audio_w = os.pipe() if self.microphone and self.segment_dir else (None, None)
        live = LiveOutputs(video_w, self.live_fps, self.live_width, audio_w)
        return live, [fd for fd in (video_r, audio_r) if fd is not None]

    async def _live_readers(self, read_ends: list[int]) -> list[Coroutine[None, None, None]]:
        readers: list[Coroutine[None, None, None]] = []
        if read_ends:
            readers.append(read_mpjpeg(await open_reader(read_ends[0]), self.live_frames))
        if len(read_ends) > 1:
            readers.append(read_chunks(await open_reader(read_ends[1]), self.live_audio))
        return readers

    async def _spawn(
        self, choice: CaptureChoice, live: LiveOutputs | None = None
    ) -> asyncio.subprocess.Process:
        args = build_command(
            self.camera,
            choice,
            segment_dir=self.segment_dir,
            segment_seconds=self.segment_seconds,
            snapshot_path=self.snapshot_path,
            analysis_fps=self.analysis_fps,
            microphone=self.microphone,
            atomic_snapshots=supports_atomic_snapshots(),
            live=live,
        )
        log.info(
            "camera %s: starting capture (%s -> %s)",
            self.camera.id,
            choice.input_format,
            choice.encoder if self.segment_dir else "no recording",
        )
        log.debug("camera %s: %s", self.camera.id, " ".join(args))
        self._set_state(CameraState.STARTING)
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Segment names come from strftime; UTC keeps them unambiguous across DST.
            env={**os.environ, "TZ": "UTC"},
            pass_fds=[fd for fd in (live.video_fd, live.audio_fd) if fd is not None]
            if live
            else (),
        )

    async def _read_frames(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        width, height = detection_size(self.camera)
        frame_bytes = width * height
        timeout = FIRST_FRAME_TIMEOUT
        last_segment_check = time.monotonic()
        while True:
            try:
                data = await asyncio.wait_for(process.stdout.readexactly(frame_bytes), timeout)
            except (TimeoutError, asyncio.IncompleteReadError) as exc:
                reason = "no frames" if isinstance(exc, TimeoutError) else "capture process ended"
                self._failed(f"{reason}: {self._last_stderr()}")
                return
            now = time.monotonic()
            if timeout == FIRST_FRAME_TIMEOUT:
                timeout = FRAME_TIMEOUT
                self._recovered()
            self.status.last_frame_at = now
            frame = np.frombuffer(data, dtype=np.uint8).reshape(height, width)
            for handler in self._handlers:
                try:
                    handler(frame, now)
                except Exception:
                    log.exception("camera %s: frame handler failed", self.camera.id)
            if self.segment_dir and now - last_segment_check > RECORDING_STALL_SECONDS:
                last_segment_check = now
                if not self._recording_progressing():
                    self._bus.publish(
                        Event(
                            EventType.RECORDING_FAILED,
                            camera=self.camera.id,
                            data={"reason": self._last_stderr()},
                        )
                    )
                    self._failed("recording stalled")
                    return

    def _recording_progressing(self) -> bool:
        assert self.segment_dir is not None
        newest = latest_segment(self.segment_dir)
        if newest is None:
            return False
        try:
            age = time.time() - newest.path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age < RECORDING_STALL_SECONDS

    async def _stop(self, process: asyncio.subprocess.Process) -> None:
        # wait() only returns once stdout reaches EOF, so keep draining the frame pipe;
        # otherwise a full pipe buffer blocks shutdown forever.
        drain = asyncio.create_task(_discard(process.stdout))
        try:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        finally:
            drain.cancel()

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        async for raw in process.stderr:
            line = raw.decode(errors="replace").strip()
            if line and "deprecated pixel format" not in line:
                self._stderr.append(line)
                log.debug("camera %s ffmpeg: %s", self.camera.id, line)

    def _last_stderr(self) -> str:
        return self._stderr[-1] if self._stderr else "no output"

    def _set_state(self, state: CameraState) -> None:
        if state is not self.status.state:
            self.status.state = state
            self.status.since = time.monotonic()

    def _failed(self, reason: str) -> None:
        if self._failing_since is None:
            self._failing_since = time.monotonic()
            log.warning("camera %s: %s", self.camera.id, reason)
        self.status.last_error = reason
        self._set_state(
            CameraState.OFFLINE if self._announced_offline else CameraState.RECONNECTING
        )

    def _check_offline(self) -> None:
        if self._failing_since is None or self._announced_offline:
            return
        if time.monotonic() - self._failing_since >= DISCONNECT_GRACE_SECONDS:
            self._announced_offline = True
            self._set_state(CameraState.OFFLINE)
            self._bus.publish(
                Event(
                    EventType.CAMERA_DISCONNECTED,
                    camera=self.camera.id,
                    data={"reason": self.status.last_error},
                )
            )

    def _recovered(self) -> None:
        if self._announced_offline:
            self._bus.publish(Event(EventType.CAMERA_RECONNECTED, camera=self.camera.id))
        if self._failing_since is not None:
            log.info("camera %s: capturing again", self.camera.id)
        self._failing_since = None
        self._announced_offline = False
        self.status.last_error = None
        self._set_state(CameraState.RUNNING)

    async def snapshot(self, dest: Path) -> Path:
        """Copy the latest full-resolution frame; never touches the camera device."""
        max_age = max(5.0, self.camera.snapshot_interval_seconds * 3)
        for _ in range(5):
            try:
                stat = self.snapshot_path.stat()
                data = self.snapshot_path.read_bytes()
            except FileNotFoundError:
                break
            if time.time() - stat.st_mtime > max_age:
                break
            if data[:2] == b"\xff\xd8" and data.rstrip(b"\0")[-2:] == b"\xff\xd9":
                dest.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(atomic_write, dest, data)
                return dest
            await asyncio.sleep(0.2)  # caught FFmpeg mid-write
        raise SnapshotUnavailableError(self.camera.id)

    def snapshot_name(self, prefix: str = "snapshot") -> str:
        return f"{self.camera.id}_{prefix}_{utcnow():%Y%m%dT%H%M%S%fZ}.jpg"

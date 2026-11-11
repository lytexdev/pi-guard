"""Live picture, live sound, talking through the speaker, and image tools.

Live listening is only available with PI_GUARD_AUDIO_RECORDING_ENABLED. Each
session is audited, limited to LISTEN_LIMIT seconds and, when a speaker is set
up, announced on site with a chime. Talking is push-to-talk: the browser
records while the button is held and sends the recording when released.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from pi_guard.audio.service import AudioError, sniff
from pi_guard.cameras import imaging
from pi_guard.cameras.live import AudioFanout
from pi_guard.cameras.pipeline import CameraPipeline, SnapshotUnavailableError
from pi_guard.core.timeutil import utcnow
from pi_guard.messaging.commands.images import parse_region
from pi_guard.security.files import is_within
from pi_guard.web.server import current, dashboard, read_body, read_json

BOUNDARY = "frame"
LISTEN_LIMIT = 600
TALK_MAX_BYTES = 2_000_000


def routes() -> list[web.RouteDef]:
    return [
        web.get("/api/live/{camera}/video", live_video),
        web.get("/api/live/{camera}/overlay", live_overlay),
        web.get("/api/live/{camera}/audio", live_audio),
        web.post("/api/live/{camera}/snapshot", snapshot),
        web.get("/api/live/{camera}/call", call),
        web.post("/api/talk", talk),
        web.post("/api/say", say),
        web.post("/api/image/sharper", sharper),
        web.post("/api/image/zoom", zoom),
        web.post("/api/image/brighter", brighter),
    ]


def _pipeline(request: web.Request) -> CameraPipeline:
    pipeline = dashboard(request).app.cameras.get(request.match_info["camera"])
    if pipeline is None:
        raise web.HTTPNotFound()
    return pipeline


OVERLAY_SECONDS = 1.0  # at most one object check per camera and second, however many watch
_overlay: dict[str, tuple[float, int, list[dict[str, Any]]]] = {}
_overlay_lock = asyncio.Lock()


def _objects(detector: Any, frame: bytes) -> tuple[int, int, list[dict[str, Any]]]:
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(frame, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return 0, 0, []
    height, width = image.shape[:2]
    found = [{"box": [round(x / width, 4), round(y / height, 4), round(w / width, 4),
                      round(h / height, 4)], "label": label, "score": round(score, 2)}
             for (x, y, w, h), label, score in detector.objects(image)]  # fmt: skip
    return width, height, found


async def live_overlay(request: web.Request) -> web.Response:
    """What the live view draws over the picture: frame rate, and with ?objects=1 the people
    and things YOLOX finds in the newest frame, as fractions of the picture."""
    pipeline = _pipeline(request)
    app = dashboard(request).app
    answer: dict[str, Any] = {"fps": round(pipeline.live_frames.fps, 1)}
    if request.query.get("objects") != "1":
        return web.json_response(answer)
    detector = app.person_locator()
    if detector is None or detector.body_detector != "yolox":
        raise web.HTTPConflict(text="web.live_needs_yolox")
    camera = pipeline.camera.id
    now = time.monotonic()
    cached = _overlay.get(camera)
    if cached is None or now - cached[0] > OVERLAY_SECONDS:
        frame = pipeline.live_frames.frame
        if frame is not None and not _overlay_lock.locked():  # one check at a time on a Pi
            async with _overlay_lock:
                width, height, found = await asyncio.to_thread(_objects, detector, frame)
            cached = (time.monotonic(), pipeline.live_frames.number, found)
            _overlay[camera] = cached
            answer["size"] = [width, height]
    answer["objects"] = cached[2] if cached else []
    return web.json_response(answer)


async def live_video(request: web.Request) -> web.StreamResponse:
    pipeline = _pipeline(request)
    if pipeline.live_fps <= 0:
        raise web.HTTPServiceUnavailable(text="live view is disabled")
    response = web.StreamResponse(headers={
        "Content-Type": f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })  # fmt: skip
    await response.prepare(request)
    number = 0
    with contextlib.suppress(ConnectionResetError, asyncio.CancelledError, TimeoutError):
        while True:
            number, frame = await pipeline.live_frames.next(number)
            await response.write(
                f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}"
                "\r\n\r\n".encode()
                + frame
                + b"\r\n"
            )
    return response


class MicrophoneStream:
    """A standalone microphone, opened only while someone is listening."""

    def __init__(self, device: str) -> None:
        self.device = device
        self.fanout = AudioFanout()
        self._process: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task[None] | None = None

    async def subscribe(self) -> asyncio.Queue[bytes]:
        queue = self.fanout.subscribe()
        if self._process is None:
            self._process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                "-f", "alsa", "-ac", "1", "-i", self.device,
                "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "pipe:1",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )  # fmt: skip
            self._task = asyncio.create_task(self._pump(self._process))
        return queue

    async def _pump(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        while chunk := await process.stdout.read(4096):
            self.fanout.publish(chunk)

    async def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        self.fanout.unsubscribe(queue)
        if self.fanout.listeners == 0 and self._process is not None:
            process, self._process = self._process, None
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            if self._task:
                self._task.cancel()


async def call(request: web.Request) -> web.WebSocketResponse:
    """Talking like on the phone: speech comes in as a stream and goes straight to the speaker."""
    d = dashboard(request)
    audio = d.app.audio
    if audio is None or not audio.playback_enabled or not audio.settings.speaker:
        raise web.HTTPServiceUnavailable(text="no speaker configured")
    origin = urlsplit(request.headers.get("Origin", "")).netloc
    if origin != request.host:  # cookies alone must not open the speaker
        raise web.HTTPForbidden(text="origin")
    socket = web.WebSocketResponse(max_msg_size=1_000_000)
    await socket.prepare(request)
    d.app.db.audit(f"web:{current(request).username}", "audio.call", request.match_info["camera"])
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-fflags", "nobuffer",
        "-i", "pipe:0", "-af", "dynaudnorm=f=150:g=7", "-f", "alsa", audio.settings.speaker,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    assert process.stdin is not None
    ended = asyncio.get_running_loop().time() + LISTEN_LIMIT
    try:
        async for message in socket:
            if message.type is not WSMsgType.BINARY or asyncio.get_running_loop().time() > ended:
                break
            process.stdin.write(message.data)
            await process.stdin.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass  # the speaker process ended the call
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
    return socket


async def live_audio(request: web.Request) -> web.StreamResponse:
    d = dashboard(request)
    app = d.app
    audio = app.audio
    if audio is None or not audio.recording_enabled:
        raise web.HTTPServiceUnavailable(text="microphone recording is disabled")
    pipeline = _pipeline(request)
    standalone: MicrophoneStream | None = None
    if pipeline.microphone and pipeline.segment_dir and pipeline.live_fps > 0:
        queue = pipeline.live_audio.subscribe()
    elif audio.settings.microphone:
        standalone = d.microphones.setdefault(
            audio.settings.microphone, MicrophoneStream(audio.settings.microphone)
        )
        queue = await standalone.subscribe()
    else:
        raise web.HTTPServiceUnavailable(text="no microphone configured")
    app.db.audit(f"web:{current(request).username}", "audio.listen_live", pipeline.camera.id)
    chime = audio.sounds.path("chime")
    if audio.playback_enabled and chime:
        with contextlib.suppress(AudioError):
            await audio.play(chime, ignore_quiet_hours=True)
    response = web.StreamResponse(headers={"Content-Type": "audio/mpeg",
                                           "Cache-Control": "no-store"})  # fmt: skip
    await response.prepare(request)
    try:
        async with asyncio.timeout(LISTEN_LIMIT):
            while True:
                await response.write(await queue.get())
    except (ConnectionResetError, asyncio.CancelledError, TimeoutError):
        pass
    finally:
        if standalone is not None:
            await standalone.unsubscribe(queue)
        else:
            pipeline.live_audio.unsubscribe(queue)
    return response


async def snapshot(request: web.Request) -> web.Response:
    pipeline = _pipeline(request)
    try:
        path = await dashboard(request).app.take_photo(pipeline.camera.id)
    except SnapshotUnavailableError:
        raise web.HTTPServiceUnavailable(text="no picture") from None
    return web.json_response({"url": f"/api/media/snapshots/{path.name}", "name": path.name})


async def talk(request: web.Request) -> web.Response:
    app = dashboard(request).app
    audio = app.audio
    if audio is None or not audio.playback_enabled:
        raise web.HTTPServiceUnavailable(text="no speaker configured")
    body = await read_body(request, TALK_MAX_BYTES)
    if not body:
        raise web.HTTPBadRequest(text="web.error")
    path = Path(tempfile.gettempdir()) / f"pi-guard-talk-{uuid.uuid4().hex}"
    path.write_bytes(body)
    try:
        if not sniff(path):
            raise web.HTTPBadRequest(text="unsupported audio")
        await audio.play(path, ignore_quiet_hours=True, speech=True)
    except AudioError as exc:
        raise web.HTTPConflict(text=exc.key) from None
    finally:
        path.unlink(missing_ok=True)
    app.db.audit(f"web:{current(request).username}", "audio.talk")
    return web.json_response({"ok": True})


async def say(request: web.Request) -> web.Response:
    """Typed words spoken through the speaker, for anyone who cannot or does not want to talk."""
    from pi_guard.audio.voice import spoken

    d = dashboard(request)
    audio = d.app.audio
    if audio is None or not audio.playback_enabled:
        raise web.HTTPServiceUnavailable(text="no speaker configured")
    text = " ".join(str((await read_json(request)).get("text", "")).split())[:500]
    if not text:
        raise web.HTTPBadRequest(text="web.error")
    async with spoken(d.app, text, d.settings.language) as path:
        if path is None:
            raise web.HTTPConflict(text="web.say_no_voice")
        try:
            await audio.play(path, ignore_quiet_hours=True, speech=True)
        except AudioError as exc:
            raise web.HTTPConflict(text=exc.key) from None
    d.app.db.audit(f"web:{current(request).username}", "audio.say")
    return web.json_response({"ok": True})


# --- image tools ------------------------------------------------------------------------


def _source(request: web.Request, data: dict[str, Any]) -> Path:
    root = dashboard(request).app.media_dir / "snapshots"
    name = str(data.get("source", ""))
    path = root / name
    if not name or "/" in name or not is_within(path, root) or not path.is_file():
        raise web.HTTPNotFound(text="unknown picture")
    return path


def _result(request: web.Request, image: imaging.Image, kind: str) -> web.Response:
    root = dashboard(request).app.media_dir / "snapshots"
    path = imaging.save(image, root / f"{kind}_{utcnow():%Y%m%dT%H%M%S%fZ}.jpg")
    return web.json_response({"url": f"/api/media/snapshots/{path.name}", "name": path.name})


async def sharper(request: web.Request) -> web.Response:
    app = dashboard(request).app
    data = await read_json(request)
    camera_id = str(data.get("camera", ""))
    if camera_id not in app.cameras:
        raise web.HTTPNotFound()
    try:
        series = await app.photo_series(camera_id, 3)
    except SnapshotUnavailableError:
        raise web.HTTPServiceUnavailable(text="no picture") from None
    try:
        image, person = await asyncio.to_thread(imaging.best_view, series, app.person_locator())
    finally:
        for path in series:
            path.unlink(missing_ok=True)
    response = _result(request, image, f"{camera_id}_sharp")
    response.headers["X-Person"] = "1" if person else "0"
    return response


async def zoom(request: web.Request) -> web.Response:
    data = await read_json(request)
    region = parse_region(str(data.get("region", "")))
    if region is None:
        raise web.HTTPBadRequest(text="unknown region")
    source = _source(request, data)
    image = await asyncio.to_thread(lambda: imaging.zoom(imaging.load(source), region))
    return _result(request, image, "zoom")


async def brighter(request: web.Request) -> web.Response:
    source = _source(request, await read_json(request))
    image = await asyncio.to_thread(lambda: imaging.brighten(imaging.load(source)))
    return _result(request, image, "bright")

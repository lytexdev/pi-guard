"""Evidence videos: record for some minutes, with sound and traceable metadata.

The result is an MP4 plus a JSON file with when, where and by whom it was requested, and the
SHA-256 of the video, so later changes to the file can be noticed. This is documentation,
not a qualified electronic signature.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_guard import __version__
from pi_guard.audio.service import AudioError
from pi_guard.core.timeutil import utcnow
from pi_guard.recording.segments import extract_clip

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

MAX_MINUTES = 30
SEND_LIMIT_BYTES = 45_000_000  # Telegram bots may send 50 MB
FLUSH_SECONDS = 4


@dataclass(frozen=True)
class Evidence:
    video: Path
    metadata: Path
    sha256: str
    seconds: int
    has_sound: bool
    size: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


async def _ffmpeg(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, err = await process.communicate()
    if process.returncode != 0:
        raise OSError(err.decode(errors="replace")[-200:])


async def record_evidence(app: App, camera_id: str, minutes: int, actor: str) -> Evidence:
    """Runs for `minutes`; recording goes on as usual meanwhile."""
    pipeline = app.cameras[camera_id]
    if pipeline.segment_dir is None:
        raise ValueError("this camera does not record")
    seconds = max(1, min(minutes, MAX_MINUTES)) * 60
    directory = app.media_dir / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    start = utcnow()
    name = f"evidence_{camera_id}_{start:%Y%m%dT%H%M%SZ}"
    audio = app.audio
    sound_task = None
    sound = directory / f"{name}.m4a"
    separate_mic = bool(
        not pipeline.microphone and audio and audio.recording_enabled and audio.settings.microphone
    )
    if audio is not None and (separate_mic or pipeline.microphone):
        chime = audio.sounds.path("chime")
        if audio.playback_enabled and chime:  # a microphone is always announced
            try:
                await audio.play(chime, ignore_quiet_hours=True)
            except AudioError:
                log.info("could not announce the microphone")
    if separate_mic and audio is not None and audio.settings.microphone:
        sound_task = asyncio.create_task(
            audio.record(audio.settings.microphone, seconds, sound, limit=seconds)
        )
    await asyncio.sleep(seconds + FLUSH_SECONDS)
    end = start + timedelta(seconds=seconds)
    video = directory / f"{name}.mp4"
    raw = directory / f"{name}.raw.mp4"
    await extract_clip(pipeline.segment_dir, start, end, raw)
    has_sound = bool(pipeline.microphone)
    inputs = ["-i", str(raw)]
    maps = ["-map", "0"]
    if sound_task is not None:
        try:
            await sound_task
            inputs += ["-i", str(sound)]
            maps = ["-map", "0:v", "-map", "1:a"]
            has_sound = True
        except AudioError:
            log.warning("evidence video without sound: microphone not available")
    comment = f"pi-guard evidence; camera {pipeline.camera.name}; requested by {actor}"
    await _ffmpeg(*inputs, *maps, "-c", "copy", "-movflags", "+faststart", "-metadata",
                  f"creation_time={start.isoformat()}", "-metadata", f"comment={comment}",
                  str(video))  # fmt: skip
    raw.unlink(missing_ok=True)
    sound.unlink(missing_ok=True)
    digest = await asyncio.to_thread(_sha256, video)
    info: dict[str, Any] = {
        "camera": pipeline.camera.name, "camera_id": camera_id, "room": pipeline.camera.room,
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "start_local": start.astimezone(app.settings.tz).isoformat(),
        "duration_seconds": seconds, "sound": has_sound, "requested_by": actor,
        "mode": app.modes.effective.value, "device": socket.gethostname(),
        "software": f"pi-guard {__version__}", "file": video.name, "sha256": digest,
    }  # fmt: skip
    metadata = directory / f"{name}.json"
    metadata.write_text(json.dumps(info, indent=2, ensure_ascii=False))
    app.db.audit(actor, "evidence", f"{video.name} {digest[:16]}")
    return Evidence(video, metadata, digest, seconds, has_sound, video.stat().st_size)


def evidence_files(media_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((media_dir / "evidence").glob("*.json"), reverse=True):
        try:
            info = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        video = path.with_suffix(".mp4")
        rows.append({**info, "size": video.stat().st_size if video.exists() else 0,
                     "metadata": path.name})  # fmt: skip
    return rows

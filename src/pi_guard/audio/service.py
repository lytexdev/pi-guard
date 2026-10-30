"""Speaker playback and bounded microphone recordings.

Both use FFmpeg with ALSA, in separate short-lived processes, so they never
touch the video pipeline. Microphones that are attached to a camera are
recorded *inside* that camera's pipeline (ALSA capture devices are exclusive);
listening then cuts the audio track out of the recording instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pi_guard.audio.sounds import SoundLibrary
from pi_guard.config.files import AudioSettings
from pi_guard.core.timeutil import TimeWindow, utcnow
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

ALLOWED_FORMATS = {"ogg", "mp3", "wav", "flac", "mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm"}
ALLOWED_CODECS = {"opus", "vorbis", "mp3", "aac", "flac", "pcm_s16le", "pcm_s24le", "pcm_u8"}
DEFAULT_VOLUME = 60


class AudioError(Exception):
    """`key` is a translation key describing the problem to the user."""

    def __init__(self, key: str, **values: object) -> None:
        super().__init__(key)
        self.key = key
        self.values = values


@dataclass(frozen=True)
class AudioInfo:
    format: str
    codec: str
    duration: float


async def _run(args: list[str], limit: float) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), limit)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise
    return process.returncode or 0, stdout, stderr


async def probe(path: Path) -> AudioInfo:
    code, out, _ = await _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        20,
    )
    if code != 0:
        raise AudioError("audio.invalid_file")
    data = json.loads(out)
    streams = data.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(audio) != 1 or any(s.get("codec_type") == "video" for s in streams):
        raise AudioError("audio.invalid_file")
    duration = float(data.get("format", {}).get("duration") or audio[0].get("duration") or 0)
    return AudioInfo(
        data.get("format", {}).get("format_name", ""), audio[0].get("codec_name", ""), duration
    )


class AudioService:
    def __init__(
        self,
        settings: AudioSettings,
        db: Database,
        sounds: SoundLibrary,
        tz: ZoneInfo,
        *,
        playback_enabled: bool,
        recording_enabled: bool,
        max_volume: int,
    ) -> None:
        self.settings = settings
        self.sounds = sounds
        self.playback_enabled = playback_enabled and settings.speaker is not None
        self.recording_enabled = recording_enabled
        self.max_volume = max_volume
        self._db = db
        self._tz = tz
        self._quiet = TimeWindow.parse(settings.quiet_hours) if settings.quiet_hours else None
        self._playing = False

    @property
    def volume(self) -> int:
        stored = self._db.get("audio.volume")
        return min(int(stored if stored is not None else DEFAULT_VOLUME), self.max_volume)

    def set_volume(self, value: int) -> int:
        value = max(0, min(value, self.max_volume))
        self._db.set("audio.volume", value)
        return value

    def quiet_now(self, now: datetime | None = None) -> bool:
        return bool(self._quiet and self._quiet.contains((now or utcnow()).astimezone(self._tz)))

    async def validate_upload(self, path: Path) -> AudioInfo:
        if path.stat().st_size > self.settings.max_upload_bytes:
            raise AudioError("audio.too_large")
        info = await probe(path)
        if info.format not in ALLOWED_FORMATS or info.codec not in ALLOWED_CODECS:
            raise AudioError("audio.unsupported")
        if info.duration <= 0 or info.duration > self.settings.max_upload_seconds:
            raise AudioError("audio.too_long", seconds=self.settings.max_upload_seconds)
        return info

    async def play(self, path: Path, *, ignore_quiet_hours: bool = False) -> None:
        if not self.playback_enabled or self.settings.speaker is None:
            raise AudioError("audio.playback_disabled")
        if self.quiet_now() and not ignore_quiet_hours:
            raise AudioError("audio.quiet_hours")
        if self._playing:
            raise AudioError("audio.busy")
        self._playing = True  # claimed before the first await, so requests cannot overlap
        try:
            info = await probe(path)
            gain = self.volume / 100
            code, _, err = await _run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-loglevel",
                    "error",
                    "-i",
                    str(path),
                    "-t",
                    str(self.settings.max_upload_seconds),
                    "-af",
                    f"volume={gain:.2f}",
                    "-f",
                    "alsa",
                    self.settings.speaker,
                ],
                limit=min(info.duration, self.settings.max_upload_seconds) + 10,
            )
        finally:
            self._playing = False
        if code != 0:
            log.warning("playback failed: %s", err.decode(errors="replace").strip()[-200:])
            raise AudioError("audio.playback_failed")

    async def record(self, device: str, seconds: int, output: Path) -> Path:
        """Record a bounded clip from a microphone that no camera pipeline is using."""
        if not self.recording_enabled:
            raise AudioError("audio.recording_disabled")
        seconds = min(seconds, self.settings.max_clip_seconds)
        output.parent.mkdir(parents=True, exist_ok=True)
        code, _, err = await _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "alsa",
                "-ac",
                "1",
                "-i",
                device,
                "-t",
                str(seconds),
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                str(output),
            ],
            limit=seconds + 15,
        )
        if code != 0 or not output.exists():
            log.warning("audio recording failed: %s", err.decode(errors="replace").strip()[-200:])
            raise AudioError("audio.recording_failed")
        return output

"""Collecting face samples for one person before they are stored.

An admin opens a session with a name and then adds photos, videos or live
camera pictures. Nothing reaches the biometric store until the admin confirms
that the person consented. Source images are never kept.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pi_guard.detection.faces import Embedding

SESSION_SECONDS = 900
MAX_VIDEO_SECONDS = 60
VIDEO_FRAMES = 40
# Two samples this similar add nothing; keep the session's samples diverse.
DUPLICATE_SIMILARITY = 0.93


@dataclass
class EnrollmentSession:
    name: str | None = None
    samples: list[Embedding] = field(default_factory=list)
    expires: float = field(default_factory=lambda: time.monotonic() + SESSION_SECONDS)

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires

    def add(self, embedding: Embedding, limit: int) -> bool:
        """Add a sample unless it duplicates one already collected or the limit is reached."""
        if len(self.samples) >= limit:
            return False
        for sample in self.samples:
            if float(np.dot(sample, embedding)) > DUPLICATE_SIMILARITY:
                return False
        self.samples.append(embedding)
        self.expires = time.monotonic() + SESSION_SECONDS
        return True


async def video_frames(video: Path, count: int = VIDEO_FRAMES) -> tuple[Path, list[Path]]:
    """Spread `count` frames over the first minute of a video; returns (temp dir, frames).

    The caller deletes the directory. Only local files are read.
    """
    workdir = Path(tempfile.mkdtemp(prefix="pi-guard-enroll-"))
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-show_entries",
        "format=duration",
        "-of",
        "csv=p=0",
        str(video),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await asyncio.wait_for(process.communicate(), 20)
    try:
        duration = min(float(out.decode().strip() or 0), MAX_VIDEO_SECONDS)
    except ValueError:
        duration = 0.0
    if duration <= 0:
        return workdir, []
    rate = max(count / duration, 0.2)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
        "-t",
        str(MAX_VIDEO_SECONDS),
        "-i",
        str(video),
        "-vf",
        f"fps={rate:.3f},scale='min(1280,iw)':-2",
        "-frames:v",
        str(count),
        "-q:v",
        "3",
        str(workdir / "frame_%03d.jpg"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(process.wait(), 120)
    return workdir, sorted(workdir.glob("frame_*.jpg"))

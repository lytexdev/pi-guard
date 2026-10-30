"""Recorded segments on disk and clip extraction from them.

Segments are named after their UTC start time (`20261030T140000Z.mp4`), so the
directory listing is the index; no database bookkeeping can drift out of sync.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_NAME_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True, order=True)
class Segment:
    start: datetime
    path: Path


class ClipError(Exception):
    pass


def parse_segment(path: Path) -> Segment | None:
    if path.suffix != ".mp4":
        return None
    try:
        start = datetime.strptime(path.stem, _NAME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    return Segment(start, path)


def list_segments(directory: Path) -> list[Segment]:
    if not directory.is_dir():
        return []
    segments = (parse_segment(p) for p in directory.iterdir())
    return sorted(s for s in segments if s is not None)


def latest_segment(directory: Path) -> Segment | None:
    segments = list_segments(directory)
    return segments[-1] if segments else None


def overlapping(segments: list[Segment], start: datetime, end: datetime) -> list[Segment]:
    """Segments whose span intersects [start, end]; a segment lasts until the next begins."""
    result = []
    for index, segment in enumerate(segments):
        next_start = segments[index + 1].start if index + 1 < len(segments) else None
        if segment.start < end and (next_start is None or next_start > start):
            result.append(segment)
    return result


def concat_list(segments: list[Segment], start: datetime, end: datetime) -> str:
    lines = []
    for index, segment in enumerate(segments):
        lines.append(f"file '{segment.path.as_posix()}'")
        inpoint = (start - segment.start).total_seconds()
        if index == 0 and inpoint > 0:
            lines.append(f"inpoint {inpoint:.3f}")
        outpoint = (end - segment.start).total_seconds()
        is_last = index == len(segments) - 1
        if is_last:
            lines.append(f"outpoint {outpoint:.3f}")
    return "\n".join(lines) + "\n"


async def extract_clip(
    directory: Path, start: datetime, end: datetime, output: Path, audio_only: bool = False
) -> Path:
    """Cut [start, end] out of the segments without re-encoding.

    Cuts snap to keyframes (every two seconds), which is fine for viewing.
    The segment being written is readable up to its last complete fragment.
    """
    if end <= start:
        raise ClipError("empty time range")
    segments = overlapping(list_segments(directory), start, end)
    if not segments:
        raise ClipError(f"no recording between {start:%H:%M:%S} and {end:%H:%M:%S}")
    output.parent.mkdir(parents=True, exist_ok=True)
    listing = output.with_suffix(".txt")
    listing.write_text(concat_list(segments, start, end))
    streams = ["-vn", "-c:a", "copy"] if audio_only else ["-c", "copy"]
    args = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(listing),
        *streams,
        "-movflags",
        "+faststart",
        str(output),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    finally:
        listing.unlink(missing_ok=True)
    if process.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise ClipError(stderr.decode(errors="replace").strip()[-300:] or "ffmpeg failed")
    return output


async def recent_clip(
    directory: Path,
    seconds: int,
    output: Path,
    flush_seconds: float = 3.0,
    audio_only: bool = False,
) -> Path:
    """The last `seconds` up to now, after waiting for the current fragment to reach disk."""
    end = datetime.now(UTC)
    await asyncio.sleep(flush_seconds)
    return await extract_clip(directory, end - timedelta(seconds=seconds), end, output, audio_only)

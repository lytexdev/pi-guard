"""Trying out a speaker or microphone before it is chosen, to find out which is which."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np

from pi_guard.audio.service import AudioError, _run, open_mixer
from pi_guard.audio.sounds import _silence, _tone, write_wav

log = logging.getLogger(__name__)

TEST_VOLUME = 0.6
SILENT_BELOW_DB = -55.0
_BASE = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
_PEAK_RE = re.compile(r"max_volume: (-?[\d.]+|-inf) dB")


def _failed(error: bytes) -> AudioError:
    text = error.decode(errors="replace")
    log.warning("audio test failed: %s", text.strip()[-200:])
    return AudioError("web.dev_test_busy" if "busy" in text.lower() else "web.dev_test_failed")


def write_test_sound(path: Path) -> None:
    """Three rising tones: easy to recognise, and clearly not an alarm."""
    parts = []
    for frequency in (660.0, 880.0, 1100.0):
        parts += [_tone(frequency, 0.25), _silence(0.08)]
    write_wav(path, np.concatenate(parts))


async def play_on(device: str, path: Path) -> None:
    await open_mixer(device)
    code, _, error = await _run(
        [*_BASE, "-i", str(path), "-af", f"volume={TEST_VOLUME}", "-f", "alsa", device], 10
    )
    if code != 0:
        raise _failed(error)


async def record_from(device: str, seconds: int, output: Path) -> None:
    code, _, error = await _run(
        [*_BASE, "-y", "-f", "alsa", "-ac", "1", "-i", device, "-t", str(seconds),
         "-c:a", "libmp3lame", "-b:a", "64k", str(output)],
        seconds + 10,
    )  # fmt: skip
    if code != 0 or not output.exists():
        raise _failed(error)


async def peak_db(path: Path) -> float | None:
    """The loudest moment in dB below full scale; None if nothing could be read."""
    _, _, report = await _run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "volumedetect",
         "-f", "null", "-"],
        15,
    )  # fmt: skip
    match = _PEAK_RE.search(report.decode(errors="replace"))
    if match is None:
        return None
    return float("-inf") if match.group(1) == "-inf" else float(match.group(1))

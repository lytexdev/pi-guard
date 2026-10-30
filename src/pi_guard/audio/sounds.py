"""Built-in sounds, synthesised on first use so no audio files are shipped."""

from __future__ import annotations

import re
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

RATE = 22050
_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def _tone(freq: float, seconds: float, decay: float = 3.0) -> np.ndarray:
    t = np.linspace(0, seconds, int(RATE * seconds), endpoint=False)
    envelope = np.exp(-decay * t)
    return np.sin(2 * np.pi * freq * t) * envelope


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds))


BUILTIN: dict[str, Callable[[], np.ndarray]] = {
    # two-tone "ding-dong"
    "doorbell": lambda: np.concatenate([_tone(660, 0.6), _tone(523, 1.0)]),
    # three soft rising notes, used to announce microphone use
    "chime": lambda: np.concatenate([_tone(523, 0.25, 6), _tone(659, 0.25, 6), _tone(784, 0.5, 4)]),
    # insistent beeping
    "warning": lambda: np.concatenate(
        [np.concatenate([_tone(880, 0.25, 0.5), _silence(0.15)]) for _ in range(6)]
    ),
}


def valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 0.8 * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm.tobytes())


class SoundLibrary:
    def __init__(self, directory: Path) -> None:
        self.builtin_dir = directory / "builtin"
        self.custom_dir = directory / "custom"

    def ensure_builtin(self) -> None:
        self.builtin_dir.mkdir(parents=True, exist_ok=True)
        for name, make in BUILTIN.items():
            path = self.builtin_dir / f"{name}.wav"
            if not path.exists():
                write_wav(path, make())

    def names(self) -> list[str]:
        custom = (
            sorted(p.stem for p in self.custom_dir.glob("*") if p.is_file())
            if self.custom_dir.is_dir()
            else []
        )
        return [*BUILTIN, *(n for n in custom if n not in BUILTIN)]

    def path(self, name: str) -> Path | None:
        if not valid_name(name):
            return None
        if name in BUILTIN:
            self.ensure_builtin()
            return self.builtin_dir / f"{name}.wav"
        if self.custom_dir.is_dir():
            for candidate in self.custom_dir.glob(f"{name}.*"):
                return candidate
        return None

    def save(self, name: str, source: Path) -> Path:
        if not valid_name(name) or name in BUILTIN:
            raise ValueError(name)
        self.custom_dir.mkdir(parents=True, exist_ok=True)
        for old in self.custom_dir.glob(f"{name}.*"):
            old.unlink()
        dest = self.custom_dir / f"{name}{source.suffix}"
        source.replace(dest)
        return dest

    def delete(self, name: str) -> bool:
        path = self.path(name)
        if path is None or name in BUILTIN:
            return False
        path.unlink()
        return True

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from pi_guard.config.files import CameraConfig, FileConfig, load_file_config
from pi_guard.config.settings import Settings

DEFAULT_ENV_FILE = Path("/etc/pi-guard/pi-guard.env")


@dataclass(frozen=True)
class Config:
    settings: Settings
    file: FileConfig

    @property
    def cameras(self) -> list[CameraConfig]:
        return [c for c in self.file.cameras if c.enabled]


def find_env_file(explicit: Path | None = None) -> Path | None:
    for candidate in (explicit, os.environ.get("PI_GUARD_ENV_FILE"), ".env", DEFAULT_ENV_FILE):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def load_config(env_file: Path | None = None) -> Config:
    settings = Settings(_env_file=find_env_file(env_file))
    return Config(settings, load_file_config(settings.config_file))


__all__ = ["CameraConfig", "Config", "FileConfig", "Settings", "load_config"]

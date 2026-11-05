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
    # What the service runs with comes first: a forgotten .env in the folder you happen to be in
    # must not make a command report on a different setup.
    system = DEFAULT_ENV_FILE if os.access(DEFAULT_ENV_FILE, os.R_OK) else None
    for candidate in (explicit, os.environ.get("PI_GUARD_ENV_FILE"), system, ".env"):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def reads_other_settings(found: Path | None) -> bool:
    """True if the service has settings this user may not read, and something else is used."""
    return DEFAULT_ENV_FILE.is_file() and found != DEFAULT_ENV_FILE


# Written by the dashboard into the data directory, because /etc/pi-guard is not
# writable for the service. They take precedence over everything else.
OVERRIDE_ENV = "dashboard.env"
OVERRIDE_YAML = "dashboard.yaml"


def read_overrides(data_dir: Path) -> dict[str, str]:
    path = data_dir / OVERRIDE_ENV
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.startswith("PI_GUARD_") and not key.startswith("#"):
            values[key.removeprefix("PI_GUARD_").lower()] = value
    return values


def load_config(env_file: Path | None = None) -> Config:
    found = find_env_file(env_file)
    settings = Settings(_env_file=found)
    overrides = read_overrides(settings.data_dir)
    if overrides:
        settings = Settings(_env_file=found, **overrides)  # type: ignore[arg-type]
    yaml_override = settings.data_dir / OVERRIDE_YAML
    yaml_path = yaml_override if yaml_override.is_file() else settings.config_file
    return Config(settings, load_file_config(yaml_path))


__all__ = [
    "CameraConfig",
    "Config",
    "FileConfig",
    "Settings",
    "find_env_file",
    "load_config",
    "reads_other_settings",
]

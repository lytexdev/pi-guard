"""Structured settings from the optional YAML file (cameras, zones, devices, rules)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pi_guard.core.timeutil import TimeWindow

_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_BITRATE_RE = re.compile(r"^\d+(\.\d+)?[kM]?$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_id(value: str) -> str:
    if not _ID_RE.match(value):
        raise ValueError(f"{value!r} must be 1-32 characters of a-z, 0-9 and _")
    return value


def _check_windows(values: list[str]) -> list[str]:
    for value in values:
        TimeWindow.parse(value)
    return values


class Zone(_Model):
    name: str
    kind: Literal["include", "exclude"] = "include"
    # Polygon corners as fractions of the frame (0.0-1.0), so zones survive resolution changes.
    points: list[tuple[float, float]] = Field(min_length=3)

    @field_validator("points")
    @classmethod
    def _in_frame(cls, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        for x, y in points:
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError("zone points are fractions of the frame between 0 and 1")
        return points


class MotionSettings(_Model):
    sensitivity: int | None = Field(None, ge=0, le=100)
    min_area_percent: float = Field(0.4, gt=0, le=50)
    min_duration_seconds: float = Field(1.0, ge=0, le=30)
    analysis_fps: float = Field(4.0, ge=1, le=10)
    analysis_width: int = Field(320, ge=160, le=640)
    zones: list[Zone] = Field(default_factory=list)
    # Detection runs only inside these local-time windows ("22:00-06:30"); empty = always.
    schedule: list[str] = Field(default_factory=list)

    _windows = field_validator("schedule")(_check_windows)


class CameraConfig(_Model):
    id: str
    name: str
    device: str
    enabled: bool = True
    resolution: str = "1280x720"
    fps: int = Field(15, ge=1, le=60)
    input_format: Literal["auto", "mjpeg", "yuyv422", "h264"] = "auto"
    codec: Literal["auto", "copy", "h264_v4l2m2m", "libx264"] = "auto"
    bitrate: str = "2M"
    recording: bool = True
    retention_days: int | None = Field(None, ge=1, le=365)
    motion_detection: bool = True
    motion: MotionSettings = Field(default_factory=MotionSettings)
    snapshot_interval_seconds: float = Field(1.0, ge=0.5, le=10)
    microphone: str | None = None
    speaker: str | None = None

    _id = field_validator("id")(_check_id)

    @field_validator("resolution")
    @classmethod
    def _check_resolution(cls, value: str) -> str:
        width, sep, height = value.partition("x")
        if not sep or not width.isdigit() or not height.isdigit():
            raise ValueError("resolution must look like 1280x720")
        if int(width) % 2 or int(height) % 2:
            raise ValueError("resolution must use even numbers")
        return value

    @field_validator("bitrate")
    @classmethod
    def _check_bitrate(cls, value: str) -> str:
        if not _BITRATE_RE.match(value):
            raise ValueError("bitrate must look like 2M or 1500k")
        return value

    @property
    def size(self) -> tuple[int, int]:
        width, _, height = self.resolution.partition("x")
        return int(width), int(height)


class TrustedDevice(_Model):
    id: str
    name: str
    mac_address: str
    ip_address: str | None = None
    enabled: bool = True

    _id = field_validator("id")(_check_id)

    @field_validator("mac_address", mode="before")
    @classmethod
    def _normalize_mac(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower().replace("-", ":")
            if not _MAC_RE.match(value):
                raise ValueError(f"{value!r} is not a MAC address")
        return value

    @property
    def randomized_mac(self) -> bool:
        """Locally administered addresses are usually per-network random MACs."""
        return bool(int(self.mac_address[:2], 16) & 0x02)


PresenceSource = Literal["neighbours", "fritzbox", "pihole"]


class FritzBoxSettings(_Model):
    host: str = "fritz.box"
    port: int = 49000
    username: str = ""


class PresenceSettings(_Model):
    check_interval_seconds: int = Field(30, ge=5, le=600)
    away_grace_period_seconds: int = Field(600, ge=60, le=7200)
    home_confirmation_seconds: int = Field(30, ge=0, le=600)
    # Effective mode while presence is UNKNOWN; "last" keeps the previous effective mode.
    unknown_mode: Literal["AWAY", "HOME", "last"] = "AWAY"
    sources: list[PresenceSource] = Field(default=["neighbours"], min_length=1)
    interface: str | None = None
    fritzbox: FritzBoxSettings = Field(default_factory=FritzBoxSettings)
    devices: list[TrustedDevice] = Field(default_factory=list)


EventName = Literal[
    "motion_detected",
    "person_detected",
    "known_person",
    "unknown_person",
    "camera_disconnected",
    "camera_reconnected",
    "recording_failed",
    "storage_low",
    "presence_changed",
    "arrival_detected",
    "emergency_escalated",
    "mode_changed",
    "system_started",
]


class NotificationRule(_Model):
    enabled: bool | None = None
    priority: Literal["low", "normal", "high", "critical"] | None = None
    modes: list[Literal["HOME", "AWAY"]] | None = None
    recipients: Literal["all", "admins"] | None = None
    providers: list[Literal["telegram", "signal"]] | None = None
    cooldown_seconds: int | None = Field(None, ge=0, le=86400)
    attach: Literal["none", "snapshot", "clip"] | None = None


class NotificationSettings(_Model):
    quiet_hours: str | None = None
    max_per_hour: int = Field(30, ge=1, le=600)
    clip_seconds: int = Field(10, ge=3, le=60)
    rules: dict[EventName, NotificationRule] = Field(default_factory=dict)

    @field_validator("quiet_hours")
    @classmethod
    def _check_quiet(cls, value: str | None) -> str | None:
        if value:
            TimeWindow.parse(value)
        return value


class AudioSettings(_Model):
    speaker: str | None = None
    microphone: str | None = None
    max_clip_seconds: int = Field(60, ge=5, le=300)
    max_upload_seconds: int = Field(60, ge=5, le=600)
    max_upload_bytes: int = Field(5_000_000, ge=10_000, le=50_000_000)
    quiet_hours: str | None = None
    alert_sound: str | None = None

    @field_validator("quiet_hours")
    @classmethod
    def _check_quiet(cls, value: str | None) -> str | None:
        if value:
            TimeWindow.parse(value)
        return value


class FaceSettings(_Model):
    match_threshold: float = Field(0.40, ge=0.2, le=0.8)
    uncertain_margin: float = Field(0.06, ge=0.0, le=0.2)
    min_interval_seconds: float = Field(5.0, ge=1, le=300)
    max_samples_per_person: int = Field(10, ge=1, le=50)


class FileConfig(_Model):
    cameras: list[CameraConfig] = Field(default_factory=list)
    presence: PresenceSettings = Field(default_factory=PresenceSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    faces: FaceSettings = Field(default_factory=FaceSettings)

    @model_validator(mode="after")
    def _unique_ids(self) -> FileConfig:
        for label, ids in (
            ("camera", [c.id for c in self.cameras]),
            ("camera device", [c.device for c in self.cameras]),
            ("presence device", [d.id for d in self.presence.devices]),
            ("presence MAC", [d.mac_address for d in self.presence.devices]),
        ):
            duplicates = {i for i in ids if ids.count(i) > 1}
            if duplicates:
                raise ValueError(f"duplicate {label}: {', '.join(sorted(duplicates))}")
        return self


def load_file_config(path: Path) -> FileConfig:
    if not path.exists():
        return FileConfig()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem = getattr(exc, "problem", None) or "invalid YAML"
        raise ValueError(f"{path}{where}: {problem}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return FileConfig.model_validate(data)

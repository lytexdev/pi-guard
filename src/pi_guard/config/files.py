"""Structured settings from the optional YAML file (cameras, zones, devices, rules)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pi_guard.core.timeutil import TimeWindow

_ID_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_MAC_RE = re.compile(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$")
_BITRATE_RE = re.compile(r"^\d+(\.\d+)?[kM]?$")
# Starts with a letter or digit, so a host can never be read as a command-line option.
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")


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
    device: str  # usb: /dev path; rtsp/http: URL; picamera: camera number
    source: Literal["usb", "rtsp", "http", "picamera"] = "usb"
    room: str = ""
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

    @model_validator(mode="after")
    def _check_device(self) -> CameraConfig:
        schemes = {"rtsp": ("rtsp://", "rtsps://"), "http": ("http://", "https://")}
        wanted = schemes.get(self.source)
        if wanted and not self.device.startswith(wanted):
            raise ValueError(f"{self.id}: address must start with {' or '.join(wanted)}")
        if self.source == "picamera" and not self.device.isdigit():
            raise ValueError(f"{self.id}: the camera number must be 0, 1, ...")
        return self

    @property
    def is_local_device(self) -> bool:
        return self.source == "usb"

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
    "service_down",
    "service_up",
]


class NotificationRule(_Model):
    enabled: bool | None = None
    priority: Literal["low", "normal", "high", "critical"] | None = None
    modes: list[Literal["HOME", "AWAY"]] | None = None
    recipients: Literal["all", "admins"] | None = None
    providers: (
        list[
            Literal[
                "telegram",
                "signal",
                "whatsapp",
                "discord",
                "email",
                "mobile",
                "push",
                "slack",
                "rocketchat",
                "matrix",
                "mastodon",
            ]
        ]
        | None
    ) = None
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
    # Many varied pictures (light, glasses, angles) make recognition reliable.
    max_samples_per_person: int = Field(40, ge=1, le=80)
    # Also run the old body detector (HOG) and note what it found, to compare it with YOLOX.
    compare_body_detectors: bool = False


class HeartbeatTarget(_Model):
    id: str
    name: str
    kind: Literal["http", "tcp", "ping"] = "http"
    address: str  # URL for http, host:port for tcp, host for ping
    interval_seconds: int = Field(60, ge=10, le=86400)
    timeout_seconds: int = Field(10, ge=1, le=60)
    failures_before_alert: int = Field(3, ge=1, le=20)
    verify_tls: bool = True
    expected_text: str | None = None  # http: must appear in the answer
    enabled: bool = True

    _id = field_validator("id")(_check_id)

    @model_validator(mode="after")
    def _check_address(self) -> HeartbeatTarget:
        if self.kind == "http" and not self.address.startswith(("http://", "https://")):
            raise ValueError(f"{self.id}: address must start with http:// or https://")
        if self.kind == "tcp":
            host, sep, port = self.address.rpartition(":")
            if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
                raise ValueError(f"{self.id}: address must look like host:port")
        if self.kind == "ping" and not _HOST_RE.match(self.address):
            raise ValueError(f"{self.id}: address must be a host name or IP address")
        return self


class HeartbeatAddon(_Model):
    enabled: bool = False
    targets: list[HeartbeatTarget] = Field(default_factory=list)


class AnalyticsAddon(_Model):
    enabled: bool = False


class SmartDevice(_Model):
    id: str
    name: str
    kind: Literal["switch", "light", "sensor"] = "switch"
    state_topic: str | None = None
    command_topic: str | None = None
    payload_on: str = "ON"
    payload_off: str = "OFF"
    # For JSON state messages: the key holding the value, e.g. "state" or "temperature".
    value_key: str | None = None
    unit: str = ""

    _id = field_validator("id")(_check_id)

    @field_validator("command_topic")
    @classmethod
    def _plain_topic(cls, value: str | None) -> str | None:
        if value and any(c in value for c in "+#"):
            raise ValueError("a command topic cannot contain + or #")
        return value


class SmartHomeAddon(_Model):
    enabled: bool = False
    # Also list what Zigbee2MQTT, Tasmota, ESPHome & co announce via Home Assistant discovery.
    discovery: bool = True
    devices: list[SmartDevice] = Field(default_factory=list)


class RemoteAction(_Model):
    id: str
    name: str
    host: str
    port: int = Field(22, ge=1, le=65535)
    user: str
    command: str = Field(min_length=1, max_length=500)
    enabled: bool = True

    _id = field_validator("id")(_check_id)

    @field_validator("host")
    @classmethod
    def _check_host(cls, value: str) -> str:
        if not _HOST_RE.match(value):
            raise ValueError("host must be a host name or IP address")
        return value

    @field_validator("user")
    @classmethod
    def _check_user(cls, value: str) -> str:
        if not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$", value):
            raise ValueError("not a user name")
        return value


class RemoteAddon(_Model):
    enabled: bool = False
    actions: list[RemoteAction] = Field(default_factory=list)


class AssistantAddon(_Model):
    enabled: bool = False
    provider: Literal["openai", "mistral", "anthropic", "gemini", "grok", "ollama", "custom"] = (
        "mistral"
    )
    model: str = ""  # empty: the provider's small default
    base_url: str | None = None  # own server: Ollama elsewhere, or any OpenAI-compatible API
    # Passages from your files and documents may be sent to a cloud provider.
    # Local models (Ollama) always may use them; nothing leaves the network then.
    share_documents: bool = False
    # The AI may be told names of devices and cameras to plan actions. Simple commands
    # ("Licht Schlafzimmer aus") work without this, locally and without tokens.
    ai_actions: bool = False
    # Camera pictures may be sent to the provider, each time after confirmation.
    allow_vision: bool = False
    paperless_url: str | None = None
    nextcloud_url: str | None = None
    nextcloud_user: str = ""
    # Spending limit: answers stop for the day when this many tokens are used.
    daily_token_limit: int = Field(50000, ge=1000, le=10_000_000)
    # For the cost shown under Usage: per million tokens, and what you put on your account.
    price_input: float | None = Field(None, ge=0, le=1000)
    price_output: float | None = Field(None, ge=0, le=1000)
    budget: float | None = Field(None, ge=0, le=1_000_000)
    currency: Literal["USD", "EUR"] = "USD"
    max_answer_tokens: int = Field(300, ge=50, le=2000)
    history_turns: int = Field(3, ge=0, le=10)
    # Weather without any AI tokens (Open-Meteo, no key).
    location: str = ""
    # Answer spoken questions aloud (costs extra; OpenAI only).
    speech_output: bool = False


class AgentsAddon(_Model):
    enabled: bool = False


class LanguagesAddon(_Model):
    enabled: bool = False


class AddonSettings(_Model):
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)
    agents: AgentsAddon = Field(default_factory=AgentsAddon)
    languages: LanguagesAddon = Field(default_factory=LanguagesAddon)
    heartbeat: HeartbeatAddon = Field(default_factory=HeartbeatAddon)
    analytics: AnalyticsAddon = Field(default_factory=AnalyticsAddon)
    smarthome: SmartHomeAddon = Field(default_factory=SmartHomeAddon)
    remote: RemoteAddon = Field(default_factory=RemoteAddon)
    assistant: AssistantAddon = Field(default_factory=AssistantAddon)

    @model_validator(mode="before")
    @classmethod
    def _one_server_addon(cls, data: Any) -> Any:
        """Computers with the agent and watched services are one add-on, "Server". Whoever
        had one of the two switched on before has both now."""
        if not isinstance(data, dict):
            return data
        sections = [data.get(k) for k in ("agents", "heartbeat")]
        if not any(isinstance(x, dict) and x.get("enabled") for x in sections):
            return data
        data = dict(data)
        for key in ("agents", "heartbeat"):
            section = data.get(key)
            data[key] = {**(section if isinstance(section, dict) else {}), "enabled": True}
        return data


class FileConfig(_Model):
    cameras: list[CameraConfig] = Field(default_factory=list)
    presence: PresenceSettings = Field(default_factory=PresenceSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    faces: FaceSettings = Field(default_factory=FaceSettings)
    addons: AddonSettings = Field(default_factory=AddonSettings)

    @model_validator(mode="after")
    def _unique_ids(self) -> FileConfig:
        for label, ids in (
            ("camera", [c.id for c in self.cameras]),
            ("camera device", [c.device for c in self.cameras]),
            ("presence device", [d.id for d in self.presence.devices]),
            ("presence MAC", [d.mac_address for d in self.presence.devices]),
            ("heartbeat target", [t.id for t in self.addons.heartbeat.targets]),
            ("smart home device", [d.id for d in self.addons.smarthome.devices]),
            ("remote action", [a.id for a in self.addons.remote.actions]),
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

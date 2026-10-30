"""`pi-guard doctor`: checks that explain what is wrong and what to do about it."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import httpx

from pi_guard.audio import devices as audio_devices
from pi_guard.cameras import discovery, ffmpeg
from pi_guard.config import Config
from pi_guard.detection.models import MODELS
from pi_guard.diagnostics.system import system_snapshot
from pi_guard.storage.db import Database


class Level(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    name: str
    level: Level
    detail: str


def heartbeat_age(runtime_dir: Path) -> float | None:
    try:
        stamp = datetime.fromisoformat((runtime_dir / "heartbeat").read_text().strip())
    except (OSError, ValueError):
        return None
    return (datetime.now(UTC) - stamp).total_seconds()


def service_state() -> str:
    if shutil.which("systemctl") is None:
        return "unknown"
    result = subprocess.run(
        ["systemctl", "is-active", "pi-guard"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def check_ffmpeg(config: Config) -> list[Check]:
    version = ffmpeg.ffmpeg_version()
    if version is None:
        return [Check("ffmpeg", Level.FAIL, "not found; install it with: sudo apt install ffmpeg")]
    checks = [Check("ffmpeg", Level.OK, version)]
    if ffmpeg.encoder_works(ffmpeg.HARDWARE_ENCODER):
        checks.append(Check("h264 encoder", Level.OK, "hardware (h264_v4l2m2m)"))
    elif ffmpeg.encoder_works(ffmpeg.SOFTWARE_ENCODER):
        checks.append(
            Check(
                "h264 encoder",
                Level.WARN,
                "software only (libx264); keep resolution/FPS modest on a Pi",
            )
        )
    else:
        checks.append(Check("h264 encoder", Level.FAIL, "no working H.264 encoder"))
    return checks


def check_cameras(config: Config) -> list[Check]:
    checks = []
    found = discovery.discover()
    if not config.file.cameras:
        level = Level.WARN if found else Level.FAIL
        names = ", ".join(f"{d.name} ({d.path})" for d in found) or "none"
        checks.append(
            Check("cameras", level, f"none configured; detected: {names}. Run `pi-guard cameras`.")
        )
    for camera in config.file.cameras:
        name = f"camera {camera.id}"
        if not camera.enabled:
            checks.append(Check(name, Level.SKIP, "disabled"))
            continue
        if not Path(camera.device).exists():
            checks.append(Check(name, Level.FAIL, f"{camera.device} does not exist"))
            continue
        if camera.device.startswith("/dev/video"):
            checks.append(
                Check(
                    name,
                    Level.WARN,
                    f"{camera.device} may change after reboot; use /dev/v4l/by-id/...",
                )
            )
        device = discovery.find(camera.device)
        if device is None:
            checks.append(
                Check(name, Level.FAIL, f"{camera.device} is not a capture device (metadata node?)")
            )
            continue
        try:
            choice = ffmpeg.choose_capture(camera, device)
        except ValueError as exc:
            checks.append(Check(name, Level.FAIL, str(exc)))
            continue
        level = Level.WARN if choice.warning else Level.OK
        detail = (
            f"{device.name}: {choice.input_format} {camera.resolution}@{camera.fps} -> "
            f"{choice.encoder}" + (f" ({choice.warning})" if choice.warning else "")
        )
        checks.append(Check(name, level, detail))
    return checks


def check_audio(config: Config) -> list[Check]:
    s, audio = config.settings, config.file.audio
    if not (s.audio_playback_enabled or s.audio_recording_enabled):
        return [Check("audio", Level.SKIP, "disabled")]
    available = {d.alsa_name for d in audio_devices.discover()}
    checks = []
    wanted = [
        ("speaker", audio.speaker, s.audio_playback_enabled),
        ("microphone", audio.microphone, s.audio_recording_enabled),
    ]
    wanted += [
        (f"microphone {c.id}", c.microphone, s.audio_recording_enabled)
        for c in config.file.cameras
        if c.microphone
    ]
    for label, device, enabled in wanted:
        if not enabled or device is None:
            continue
        ok = device in available or not device.startswith("plughw:")
        checks.append(
            Check(
                label,
                Level.OK if ok else Level.FAIL,
                device if ok else f"{device} not found; see `pi-guard audio devices`",
            )
        )
    return checks or [Check("audio", Level.WARN, "enabled but no device configured")]


def check_storage(config: Config) -> list[Check]:
    s = config.settings
    snap = system_snapshot(s.recordings_path)
    level = Level.OK
    if snap.disk_used_percent > s.recording_max_storage_percent:
        level = Level.WARN
    if snap.disk_free_gb * 1000 < s.recording_min_free_mb:
        level = Level.FAIL
    checks = [
        Check(
            "storage",
            level,
            f"{s.recordings_path}: {snap.disk_used_percent:.0f}% used, "
            f"{snap.disk_free_gb:.1f} GB free",
        )
    ]
    root = system_snapshot(Path("/"))
    if s.recordings_path.exists() and s.recordings_path.stat().st_dev == Path("/").stat().st_dev:
        checks.append(
            Check(
                "storage location",
                Level.OK if root.disk_free_gb > 2 else Level.WARN,
                "recordings share the root filesystem; the disk limit protects it",
            )
        )
    memory = f"{snap.memory_available_mb} of {snap.memory_total_mb} MB available"
    checks.append(
        Check("memory", Level.WARN if snap.memory_available_mb < 200 else Level.OK, memory)
    )
    load = f"load {snap.load1:.2f} on {snap.cpu_count} cores"
    checks.append(Check("cpu", Level.WARN if snap.load1 > snap.cpu_count else Level.OK, load))
    if snap.temperature_c is not None:
        hot = snap.temperature_c >= 75
        checks.append(
            Check(
                "temperature",
                Level.WARN if hot else Level.OK,
                f"{snap.temperature_c:.0f} °C" + (" (add cooling)" if hot else ""),
            )
        )
    return checks


def check_database(config: Config) -> list[Check]:
    path = config.settings.data_dir / "pi-guard.db"
    if not path.exists():
        return [Check("database", Level.WARN, f"{path} does not exist yet (created on first run)")]
    try:
        db = Database(path)
        result = db.check()
        db.close()
    except Exception as exc:
        return [Check("database", Level.FAIL, str(exc))]
    return [Check("database", Level.OK if result == "ok" else Level.FAIL, result)]


def check_telegram(config: Config) -> list[Check]:
    s = config.settings
    if not s.telegram_enabled or s.telegram_bot_token is None:
        return [Check("telegram", Level.SKIP, "disabled")]
    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/getMe",
            timeout=10,
        )
    except httpx.HTTPError as exc:
        return [Check("telegram", Level.FAIL, f"no connection to Telegram: {type(exc).__name__}")]
    if response.status_code == 401:
        return [Check("telegram", Level.FAIL, "the bot token was rejected; check @BotFather")]
    if response.status_code != 200:
        return [Check("telegram", Level.FAIL, f"HTTP {response.status_code}")]
    username = response.json().get("result", {}).get("username", "?")
    return [Check("telegram", Level.OK, f"bot @{username}")]


def check_signal(config: Config) -> list[Check]:
    s = config.settings
    if not s.signal_enabled:
        return [Check("signal", Level.SKIP, "disabled")]
    from pi_guard.messaging.signal.provider import probe

    try:
        version = asyncio.run(probe(s.signal_cli_address))
    except (OSError, TimeoutError, ValueError) as exc:
        return [
            Check(
                "signal", Level.FAIL, f"signal-cli not reachable at {s.signal_cli_address}: {exc}"
            )
        ]
    return [Check("signal", Level.OK, f"signal-cli {version}")]


def check_detection(config: Config) -> list[Check]:
    s = config.settings
    if not s.motion_enabled:
        return [Check("motion", Level.SKIP, "disabled")]
    try:
        import cv2
    except ImportError:
        return [Check("opencv", Level.FAIL, "not installed; motion detection will be off")]
    checks = [Check("opencv", Level.OK, cv2.__version__)]
    if s.person_detection_enabled or s.face_recognition_enabled:
        models_dir = s.data_dir / "models"
        for model in MODELS:
            needed = model.name == "sface" and s.face_recognition_enabled
            present = model.present(models_dir)
            level = Level.OK if present else (Level.FAIL if needed else Level.WARN)
            checks.append(
                Check(
                    f"model {model.name}",
                    level,
                    "installed" if present else "missing: `pi-guard models download`",
                )
            )
    return checks


def check_service(config: Config) -> list[Check]:
    state = service_state()
    age = heartbeat_age(config.settings.runtime_dir)
    level = Level.OK if state == "active" and age is not None and age < 60 else Level.WARN
    beat = f"heartbeat {age:.0f}s ago" if age is not None else "no heartbeat"
    return [Check("service", level, f"systemd: {state}, {beat}")]


CHECKS: list[Callable[[Config], list[Check]]] = [
    check_ffmpeg,
    check_cameras,
    check_audio,
    check_storage,
    check_database,
    check_detection,
    check_telegram,
    check_signal,
    check_service,
]


def run_checks(config: Config) -> list[Check]:
    results: list[Check] = []
    for check in CHECKS:
        try:
            results.extend(check(config))
        except Exception as exc:
            results.append(
                Check(check.__name__.removeprefix("check_"), Level.FAIL, f"check crashed: {exc}")
            )
    return results

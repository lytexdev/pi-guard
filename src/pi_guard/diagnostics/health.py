"""Health checks of the running service and what the device is, for the dashboard's System page.

Unlike `pi-guard doctor` nothing here starts FFmpeg or opens a camera: the checks read what the
running service already knows, and files in /proc and /sys. Every check is
{"id", "level", "params"}; the dashboard turns id and params into words.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_guard import __version__
from pi_guard.core.timeutil import utcnow
from pi_guard.detection.models import MODELS
from pi_guard.diagnostics.system import meminfo, system_snapshot

if TYPE_CHECKING:
    from pi_guard.app import App

HOT_C = 75.0  # the Pi slows down from 80-85 °C
TOO_HOT_C = 82.0
FRAME_LATE_S = 10.0
BACKUP_LATE_H = 48
# Bits of the Pi firmware's throttled word: now (0-3) and since boot (16-19).
_THROTTLE = {0: "undervoltage", 1: "capped", 2: "throttled", 3: "soft_limit"}
_THROTTLED = (
    Path("/sys/devices/platform/soc/soc:firmware/get_throttled"),
    Path("/sys/devices/platform/soc/soc:firmware/raspberrypi-hwmon/throttled"),
)


def _check(check_id: str, level: str, **params: Any) -> dict[str, Any]:
    return {"id": check_id, "level": level, "params": params}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace").strip("\x00\n ")
    except OSError:
        return None


def throttled() -> dict[str, list[str]] | None:
    """What the Pi firmware says about power and heat, or None off a Pi."""
    for path in _THROTTLED:
        raw = _read(path)
        if raw is None:
            continue
        try:
            word = int(raw, 16)
        except ValueError:
            continue
        return {
            "now": [name for bit, name in _THROTTLE.items() if word & (1 << bit)],
            "since_boot": [name for bit, name in _THROTTLE.items() if word & (1 << (bit + 16))],
        }
    return None


def _service(app: App) -> list[dict[str, Any]]:
    failing = [c.name for c in app.components.values() if not c.running]
    restarts = sum(c.restarts for c in app.components.values())
    level = "warn" if failing else "ok"
    return [_check("service", level, uptime=round(app.uptime_seconds), failing=failing,
                   restarts=restarts, components=len(app.components))]  # fmt: skip


def _cameras(app: App) -> list[dict[str, Any]]:
    out = []
    for pipeline in app.cameras.values():
        status = pipeline.status
        last = status.last_frame_at
        age = round(time.monotonic() - last, 1) if last else None
        state = status.state.value
        level = "ok" if state == "running" and age is not None and age < FRAME_LATE_S else "fail"
        if state in ("starting", "reconnecting") and level == "fail":
            level = "warn"
        out.append(_check("camera", level, name=pipeline.camera.name, state=state, age=age,
                          recording=pipeline.segment_dir is not None))  # fmt: skip
    return out or [_check("camera", "warn", name="", state="none", age=None, recording=False)]


def _resources(app: App) -> list[dict[str, Any]]:
    s = app.settings
    snap = system_snapshot(s.recordings_path)
    level = "ok"
    if snap.disk_used_percent > s.recording_max_storage_percent:
        level = "warn"
    if snap.disk_free_gb * 1000 < s.recording_min_free_mb:
        level = "fail"
    checks = [
        _check("storage", level, used=round(snap.disk_used_percent),
               free_gb=round(snap.disk_free_gb, 1)),
        _check("memory", "warn" if snap.memory_available_mb < 200 else "ok",
               available_mb=snap.memory_available_mb, total_mb=snap.memory_total_mb),
        _check("cpu", "warn" if snap.load1 > snap.cpu_count else "ok", load=round(snap.load1, 2),
               cores=snap.cpu_count),
    ]  # fmt: skip
    if snap.temperature_c is not None:
        hot = (
            "fail"
            if snap.temperature_c >= TOO_HOT_C
            else "warn"
            if snap.temperature_c >= HOT_C
            else "ok"
        )
        checks.append(_check("temperature", hot, celsius=round(snap.temperature_c)))
    power = throttled()
    if power is not None:
        level = "fail" if power["now"] else "warn" if power["since_boot"] else "ok"
        checks.append(_check("power", level, **power))
    return checks


def _database(app: App) -> list[dict[str, Any]]:
    try:
        result = app.db.check()
    except Exception as exc:  # a broken database must still show up as a failed check
        return [_check("database", "fail", result=type(exc).__name__)]
    return [_check("database", "ok" if result == "ok" else "fail", result=result)]


# Not messengers that can be down: a linked household is offline by itself (its own check is
# "federation"), and the phone app's provider always says connected, which tells nothing.
NOT_MESSENGERS = {"household", "mobile"}


def _messengers(app: App) -> list[dict[str, Any]]:
    return [
        _check("messenger", "ok" if getattr(provider, "connected", True) else "fail", name=name)
        for name, provider in sorted(app.providers.items())
        if name not in NOT_MESSENGERS
    ]


def _models(app: App) -> list[dict[str, Any]]:
    s = app.settings
    if not (s.person_detection_enabled or s.face_recognition_enabled):
        return []
    models_dir = s.data_dir / "models"
    missing = [m.name for m in MODELS if not m.present(models_dir)]
    needed = s.face_recognition_enabled and any(m in missing for m in ("sface", "yunet"))
    return [_check("models", "fail" if needed else "warn" if missing else "ok", missing=missing)]


def _backup(app: App) -> list[dict[str, Any]]:
    if not app.settings.backup_enabled:
        return [_check("backup", "skip")]
    last = app.backups.last()
    if not last.get("time"):
        return [_check("backup", "warn", time=None, ok=None)]
    try:
        hours = (utcnow() - datetime.fromisoformat(last["time"])).total_seconds() / 3600
    except (TypeError, ValueError):
        hours = None
    level = (
        "fail" if not last.get("ok") else "warn" if hours is None or hours > BACKUP_LATE_H else "ok"
    )
    return [_check("backup", level, time=last["time"], ok=bool(last.get("ok")))]


def _clock() -> list[dict[str, Any]]:
    # systemd-timesyncd leaves this file once the clock was set from the network.
    if not Path("/run/systemd/timesync").exists():
        return []
    synced = Path("/run/systemd/timesync/synchronized").exists()
    return [_check("clock", "ok" if synced else "warn", synced=synced)]


def _federation(app: App) -> list[dict[str, Any]]:
    federation = getattr(app.dashboard, "federation", None) if app.dashboard else None
    if federation is None or not app.settings.federation_listen:
        return []
    problem = federation.listen_problem
    return [_check("federation", "fail" if problem else "ok", problem=problem,
                   port=app.settings.federation_port)]  # fmt: skip


def _ddns(app: App) -> list[dict[str, Any]]:
    if app.settings.ddns_provider == "off":
        return []
    status = app.ddns.status()
    if not status.get("time"):
        return [_check("ddns", "warn", host=app.settings.ddns_hostname, error=None)]
    return [_check("ddns", "ok" if status.get("ok") else "fail", host=status.get("hostname"),
                   address=status.get("ipv4"), error=status.get("error"))]  # fmt: skip


PARTS = ("service", "cameras", "resources", "database", "messengers", "models", "backup",
         "federation", "ddns")  # fmt: skip


def checks(app: App) -> list[dict[str, Any]]:
    """All checks; one that crashes is reported as failed, the others still run."""
    out: list[dict[str, Any]] = []
    for name in PARTS:
        try:
            out.extend(globals()[f"_{name}"](app))
        except Exception as exc:
            out.append(_check(name, "fail", error=type(exc).__name__))
    out.extend(_clock())
    return out


# --- what the device is ------------------------------------------------------------------


def _os_name() -> str:
    for line in (_read(Path("/etc/os-release")) or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.partition("=")[2].strip().strip('"')
    return platform.system()


def _cpu() -> str:
    names: dict[str, str] = {}
    for line in (_read(Path("/proc/cpuinfo")) or "").splitlines():
        key, _, value = line.partition(":")
        names.setdefault(key.strip().lower(), value.strip())
    return (
        names.get("model name")
        or names.get("model")
        or names.get("hardware")
        or platform.processor()
    )


def _model() -> str:
    for path in (Path("/proc/device-tree/model"), Path("/sys/class/dmi/id/product_name")):
        value = _read(path)
        if value:
            return value
    return ""


def _mounts() -> dict[str, tuple[str, str]]:
    found = {}
    for line in (_read(Path("/proc/mounts")) or "").splitlines():
        parts = line.split()
        if len(parts) >= 3:
            found[parts[1].replace("\\040", " ")] = (parts[0], parts[2])
    return found


def _disk(label: str, path: Path, mounts: dict[str, tuple[str, str]]) -> dict[str, Any] | None:
    target = path if path.exists() else Path("/")
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return None
    point = target.resolve()
    while str(point) not in mounts and point != point.parent:
        point = point.parent
    device, kind = mounts.get(str(point), ("", ""))
    return {"label": label, "path": str(path), "mount": str(point), "device": device,
            "type": kind, "total_gb": round(usage.total / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1)}  # fmt: skip


def _usb() -> list[dict[str, str]]:
    found = []
    for device in sorted(Path("/sys/bus/usb/devices").glob("*")):
        product = _read(device / "product")
        if not product or _read(device / "bDeviceClass") == "09":  # hubs
            continue
        found.append({"name": product, "maker": _read(device / "manufacturer") or "",
                      "speed": _read(device / "speed") or ""})  # fmt: skip
    return found


VIRTUAL = ("lo", "docker", "br-", "veth", "virbr")  # container bridges say nothing about the home


def _addresses() -> list[dict[str, str]]:
    if shutil.which("ip") is None:
        return []
    try:
        raw = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=3,
                             check=False).stdout  # fmt: skip
        links = json.loads(raw or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    out = []
    for link in links:
        if link.get("ifname", "lo").startswith(VIRTUAL):
            continue
        for addr in link.get("addr_info", []):
            if addr.get("scope") == "global":
                out.append(
                    {"interface": link["ifname"], "address": f"{addr['local']}/{addr['prefixlen']}"}
                )
    return out


def _camera(pipeline: Any) -> dict[str, Any]:
    camera, status = pipeline.camera, pipeline.status
    return {"name": camera.name, "source": camera.source, "resolution": camera.resolution,
            "fps": camera.fps, "encoder": status.encoder, "input": status.input_format}  # fmt: skip


def hardware(app: App) -> dict[str, Any]:
    s = app.settings
    mem = meminfo()
    uptime = _read(Path("/proc/uptime"))
    mounts = _mounts()
    disks = [_disk("data", s.data_dir, mounts), _disk("recordings", s.recordings_path, mounts)]
    audio = app.config.file.audio
    return {
        "model": _model(), "hostname": socket.gethostname(), "os": _os_name(),
        "kernel": platform.release(), "arch": platform.machine(), "cpu": _cpu(),
        "cores": os.cpu_count() or 1, "memory_mb": mem.get("MemTotal", 0) // 1024,
        "swap_mb": mem.get("SwapTotal", 0) // 1024,
        "boot_uptime": round(float(uptime.split()[0])) if uptime else None,
        "python": platform.python_version(), "version": __version__,
        "disks": [d for d in disks if d],
        "cameras": [_camera(p) for p in app.cameras.values()],
        "audio": {"speaker": audio.speaker, "microphone": audio.microphone},
        "usb": _usb(), "addresses": _addresses(),
    }  # fmt: skip

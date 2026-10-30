"""Host metrics from /proc and /sys, without extra dependencies."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

_THERMAL = Path("/sys/class/thermal/thermal_zone0/temp")


@dataclass(frozen=True)
class SystemSnapshot:
    disk_used_percent: float
    disk_free_gb: float
    load1: float
    cpu_count: int
    memory_total_mb: int
    memory_available_mb: int
    temperature_c: float | None

    @property
    def memory_used_percent(self) -> float:
        if not self.memory_total_mb:
            return 0.0
        return 100 * (1 - self.memory_available_mb / self.memory_total_mb)


def meminfo() -> dict[str, int]:
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            values[key] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return values


def temperature() -> float | None:
    try:
        return int(_THERMAL.read_text().strip()) / 1000
    except (OSError, ValueError):
        return None


def system_snapshot(path: Path) -> SystemSnapshot:
    target = path if path.exists() else Path("/")
    usage = shutil.disk_usage(target)
    mem = meminfo()
    return SystemSnapshot(
        disk_used_percent=usage.used / usage.total * 100 if usage.total else 0.0,
        disk_free_gb=usage.free / 1e9,
        load1=os.getloadavg()[0],
        cpu_count=os.cpu_count() or 1,
        memory_total_mb=mem.get("MemTotal", 0) // 1024,
        memory_available_mb=mem.get("MemAvailable", 0) // 1024,
        temperature_c=temperature(),
    )

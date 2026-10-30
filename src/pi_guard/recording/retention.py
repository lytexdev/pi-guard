"""Deletes old recordings and keeps the filesystem from filling up.

Rules, applied per camera directory:
  * continuous recording: keep segments for the retention period
  * event recording (PI_GUARD_RECORDING_CONTINUOUS=false): keep segments that
    overlap a motion event for the retention period, others only for the buffer
  * camera with `recording: false`: keep the short buffer used for /video
Then, if disk usage is still above the limit, the oldest segments of any camera
go first. The segment being written is never deleted.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from pi_guard.config import Config
from pi_guard.core.events import Event, EventBus, EventType
from pi_guard.core.timeutil import utcnow
from pi_guard.recording.segments import Segment, list_segments
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

EVENT_PADDING = timedelta(seconds=60)
SWEEP_INTERVAL_SECONDS = 300
CLIP_RETENTION = timedelta(days=1)


@dataclass
class SweepReport:
    deleted: list[Path] = field(default_factory=list)
    freed_bytes: int = 0
    pressure_deletions: int = 0
    used_percent: float = 0.0
    free_bytes: int = 0
    over_limit: bool = False


class Retention:
    def __init__(self, config: Config, db: Database, bus: EventBus | None) -> None:
        self._settings = config.settings
        self._cameras = {c.id: c for c in config.file.cameras}
        self._db = db
        self._bus = bus
        self.root = config.settings.recordings_path
        self.media = config.settings.data_dir / "media"
        self.last_report: SweepReport | None = None

    async def run(self) -> None:
        while True:
            report = await asyncio.to_thread(self.sweep)
            if report.over_limit and self._bus:
                self._bus.publish(
                    Event(
                        EventType.STORAGE_LOW,
                        data={
                            "used_percent": round(report.used_percent),
                            "free_mb": report.free_bytes // 1_000_000,
                        },
                    )
                )
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)

    def _keep_for(self, camera_id: str) -> tuple[timedelta, bool]:
        """(how long to keep, whether only event-overlapping segments get that long)."""
        s = self._settings
        camera = self._cameras.get(camera_id)
        retention = timedelta(
            days=(
                camera.retention_days
                if camera and camera.retention_days
                else s.recording_retention_days
            )
        )
        if camera is not None and not camera.recording:
            return timedelta(minutes=s.recording_buffer_minutes), False
        return retention, not s.recording_continuous

    def _active(self, segments: list[Segment], now_ts: float) -> set[Path]:
        active = {segments[-1].path} if segments else set()
        for segment in segments:
            try:
                if now_ts - segment.path.stat().st_mtime < 30:
                    active.add(segment.path)
            except FileNotFoundError:
                continue
        return active

    def sweep(self, now: datetime | None = None) -> SweepReport:
        now = now or utcnow()
        report = SweepReport()
        if not self.root.is_dir():
            return report
        buffer = timedelta(minutes=self._settings.recording_buffer_minutes)
        candidates: list[Segment] = []
        for directory in sorted(p for p in self.root.iterdir() if p.is_dir()):
            segments = list_segments(directory)
            active = self._active(segments, time.time())
            keep, events_only = self._keep_for(directory.name)
            event_times = (
                self._db.events_between(
                    now - keep - EVENT_PADDING, now, ["motion_detected"], directory.name
                )
                if events_only
                else []
            )
            for index, segment in enumerate(segments):
                if segment.path in active:
                    continue
                end = segments[index + 1].start if index + 1 < len(segments) else now
                expired = segment.start < now - keep
                if events_only and not expired and segment.start < now - buffer:
                    expired = not any(
                        segment.start - EVENT_PADDING <= t <= end + EVENT_PADDING
                        for t in event_times
                    )
                if expired or self._empty(segment.path):
                    self._delete(segment.path, report)
                else:
                    candidates.append(segment)

        self._sweep_media(now, report)
        self._relieve_pressure(sorted(candidates), report)
        self._db.prune(now - timedelta(days=max(30, self._settings.recording_retention_days)))
        if report.deleted:
            log.info(
                "retention removed %d files (%.1f MB)",
                len(report.deleted),
                report.freed_bytes / 1e6,
            )
        self.last_report = report
        return report

    def _over_limit(self, report: SweepReport) -> bool:
        usage = shutil.disk_usage(self.root)
        report.used_percent = usage.used / usage.total * 100
        report.free_bytes = usage.free
        return (
            report.used_percent > self._settings.recording_max_storage_percent
            or usage.free < self._settings.recording_min_free_mb * 1_000_000
        )

    def _relieve_pressure(self, candidates: list[Segment], report: SweepReport) -> None:
        for segment in candidates:
            if not self._over_limit(report):
                break
            self._delete(segment.path, report)
            report.pressure_deletions += 1
        report.over_limit = self._over_limit(report)
        if report.pressure_deletions:
            log.warning("disk above limit: removed %d recordings early", report.pressure_deletions)

    def _sweep_media(self, now: datetime, report: SweepReport) -> None:
        keep_snapshots = timedelta(days=self._settings.recording_retention_days)
        for kind, keep in (
            ("snapshots", keep_snapshots),
            ("clips", CLIP_RETENTION),
            ("audio", CLIP_RETENTION),
        ):
            directory = self.media / kind
            if not directory.is_dir():
                continue
            cutoff = (now - keep).timestamp()
            for path in directory.iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff:
                    self._delete(path, report)

    @staticmethod
    def _empty(path: Path) -> bool:
        try:
            return path.stat().st_size < 1024
        except FileNotFoundError:
            return False

    @staticmethod
    def _delete(path: Path, report: SweepReport) -> None:
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("could not delete %s: %s", path, exc)
            return
        report.deleted.append(path)
        report.freed_bytes += size

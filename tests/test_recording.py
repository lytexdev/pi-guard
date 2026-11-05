from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from pi_guard.config import Config
from pi_guard.config.files import CameraConfig, FileConfig
from pi_guard.config.settings import Settings
from pi_guard.recording import retention as retention_module
from pi_guard.recording.retention import Retention
from pi_guard.recording.segments import (
    ClipError,
    Segment,
    concat_list,
    extract_clip,
    list_segments,
    overlapping,
    parse_segment,
)
from pi_guard.storage.db import Database

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
Usage = namedtuple("Usage", "total used free")


def seg(minutes_ago: float, directory: Path = Path("/r/cam")) -> Segment:
    start = NOW - timedelta(minutes=minutes_ago)
    return Segment(start, directory / f"{start:%Y%m%dT%H%M%SZ}.mp4")


def test_parse_segment_names() -> None:
    assert parse_segment(Path("20261030T120000Z.mp4")) == Segment(NOW, Path("20261030T120000Z.mp4"))
    assert parse_segment(Path("notes.txt")) is None
    assert parse_segment(Path("garbage.mp4")) is None


def test_overlapping_segments() -> None:
    segments = [seg(15), seg(10), seg(5)]
    window = overlapping(segments, NOW - timedelta(minutes=7), NOW - timedelta(minutes=4))
    assert window == [seg(10), seg(5)]
    assert overlapping(segments, NOW - timedelta(minutes=1), NOW) == [seg(5)]
    assert overlapping(segments, NOW - timedelta(hours=2), NOW - timedelta(hours=1)) == []


def test_concat_list_trims_first_and_last() -> None:
    text = concat_list([seg(10), seg(5)], NOW - timedelta(minutes=7), NOW - timedelta(minutes=4))
    assert "inpoint 180.000" in text
    assert "outpoint 60.000" in text
    assert text.count("file ") == 2


def _make_segments(directory: Path, count: int, seconds: int = 4) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=count * seconds + 2)
    paths = []
    for i in range(count):
        path = directory / f"{start + timedelta(seconds=i * seconds):%Y%m%dT%H%M%SZ}.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x120:rate=10",
                "-t",
                str(seconds),
                "-c:v",
                "libx264",
                "-g",
                "10",
                "-movflags",
                "+frag_keyframe+empty_moov+default_base_moof",
                str(path),
            ],
            check=True,
        )
        paths.append(path)
    return paths


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout)


@pytest.mark.ffmpeg
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_extract_clip_spanning_segments(tmp_path: Path) -> None:
    paths = _make_segments(tmp_path / "cam", 3)
    segments = list_segments(tmp_path / "cam")
    start = segments[0].start + timedelta(seconds=2)
    end = segments[1].start + timedelta(seconds=3)
    clip = await extract_clip(tmp_path / "cam", start, end, tmp_path / "out" / "clip.mp4")
    assert 4.0 <= _duration(clip) <= 6.5
    assert not clip.with_suffix(".txt").exists()
    assert len(paths) == 3
    with pytest.raises(ClipError):
        await extract_clip(
            tmp_path / "cam",
            start - timedelta(hours=1),
            start - timedelta(minutes=50),
            tmp_path / "x.mp4",
        )


# --- retention --------------------------------------------------------------------------


def _config(tmp_path: Path, cameras: list[dict[str, object]] | None = None, **kw: object) -> Config:
    settings = Settings(telegram_enabled=False, data_dir=tmp_path, **kw)  # type: ignore[arg-type]
    return Config(
        settings, FileConfig(cameras=[CameraConfig.model_validate(c) for c in cameras or []])
    )


def _touch(directory: Path, minutes_ago: float, size: int = 4096) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    start = NOW - timedelta(minutes=minutes_ago)
    path = directory / f"{start:%Y%m%dT%H%M%SZ}.mp4"
    path.write_bytes(b"\0" * size)
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


@pytest.fixture
def roomy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(retention_module.shutil, "disk_usage", lambda p: Usage(100e9, 10e9, 90e9))


def test_retention_deletes_expired_and_keeps_active(
    tmp_path: Path, db: Database, roomy: None
) -> None:
    config = _config(tmp_path, recording_retention_days=1)
    cam = tmp_path / "recordings" / "cam"
    old = _touch(cam, 60 * 30)
    recent = _touch(cam, 60)
    report = Retention(config, db, None).sweep(NOW)
    assert report.deleted == [old]
    assert recent.exists()


def test_retention_never_deletes_newest_segment(tmp_path: Path, db: Database, roomy: None) -> None:
    config = _config(tmp_path, recording_retention_days=1)
    only = _touch(tmp_path / "recordings" / "cam", 60 * 48)
    Retention(config, db, None).sweep(NOW)
    assert only.exists()


def test_retention_removes_interrupted_stubs(tmp_path: Path, db: Database, roomy: None) -> None:
    config = _config(tmp_path)
    cam = tmp_path / "recordings" / "cam"
    stub = _touch(cam, 20, size=28)
    _touch(cam, 10)
    Retention(config, db, None).sweep(NOW)
    assert not stub.exists()


def test_buffer_only_camera(tmp_path: Path, db: Database, roomy: None) -> None:
    config = _config(
        tmp_path,
        [{"id": "cam", "name": "C", "device": "/dev/video0", "recording": False}],
        recording_buffer_minutes=15,
    )
    cam = tmp_path / "recordings" / "cam"
    old, fresh, newest = _touch(cam, 30), _touch(cam, 10), _touch(cam, 5)
    Retention(config, db, None).sweep(NOW)
    assert not old.exists() and fresh.exists() and newest.exists()


def test_event_recording_keeps_motion_segments(tmp_path: Path, db: Database, roomy: None) -> None:
    config = _config(tmp_path, recording_continuous=False, recording_buffer_minutes=15)
    cam = tmp_path / "recordings" / "cam"
    boring = _touch(cam, 120)
    eventful = _touch(cam, 60)
    _touch(cam, 55)
    _touch(cam, 5)
    db.add_event(NOW - timedelta(minutes=58), "motion_detected", "cam", {})
    Retention(config, db, None).sweep(NOW)
    assert not boring.exists()
    assert eventful.exists()


def test_disk_pressure_removes_oldest_first(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path, recording_max_storage_percent=80)
    a = [_touch(tmp_path / "recordings" / "a", m) for m in (50, 40, 5)]
    b = [_touch(tmp_path / "recordings" / "b", m) for m in (45, 4)]
    used = iter([95e9, 90e9, 85e9, 70e9, 70e9])
    monkeypatch.setattr(
        retention_module.shutil, "disk_usage", lambda p: Usage(100e9, (u := next(used)), 100e9 - u)
    )
    report = Retention(config, db, None).sweep(NOW)
    assert report.pressure_deletions == 3
    assert not a[0].exists() and not b[0].exists() and not a[1].exists()
    assert a[2].exists() and b[1].exists()
    assert not report.over_limit


def test_disk_pressure_reports_when_unfixable(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    _touch(tmp_path / "recordings" / "a", 5)
    monkeypatch.setattr(retention_module.shutil, "disk_usage", lambda p: Usage(100e9, 99e9, 1e9))
    assert Retention(config, db, None).sweep(NOW).over_limit


def test_after_the_full_hours_only_events_stay(tmp_path: Path, db: Database, roomy: None) -> None:
    config = _config(tmp_path, recording_retention_days=7, recording_full_hours=24)
    cam = tmp_path / "recordings" / "cam"
    quiet = _touch(cam, 60 * 48)
    with_event = _touch(cam, 60 * 47)
    fresh = _touch(cam, 60 * 2)
    _touch(cam, 1)
    db.add_event(NOW - timedelta(hours=46, minutes=58), "motion_detected", "cam", {})
    Retention(config, db, None).sweep(NOW)
    assert not quiet.exists() and with_event.exists() and fresh.exists()


def test_without_full_hours_nothing_changes(tmp_path: Path, db: Database, roomy: None) -> None:
    config = _config(tmp_path, recording_retention_days=7)
    cam = tmp_path / "recordings" / "cam"
    quiet = _touch(cam, 60 * 48)
    _touch(cam, 1)
    Retention(config, db, None).sweep(NOW)
    assert quiet.exists()

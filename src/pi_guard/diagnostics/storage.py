"""Where the disk space went, and what can safely go. Evidence videos are never touched here."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

MEDIA_KINDS = ("snapshots", "clips", "audio", "uploads")
CLEANABLE = ("recordings", *MEDIA_KINDS)
DAY = 86400


def _walk(root: Path) -> tuple[int, int, float | None]:
    """(bytes, files, oldest modification time) below a directory or of one file."""
    total = files = 0
    oldest: float | None = None
    if root.is_file():
        info = root.stat()
        return info.st_size, 1, info.st_mtime
    for folder, _, names in os.walk(root):
        for name in names:
            try:
                info = (Path(folder) / name).stat()
            except OSError:
                continue  # deleted while looking
            total += info.st_size
            files += 1
            oldest = info.st_mtime if oldest is None else min(oldest, info.st_mtime)
    return total, files, oldest


def _entry(key: str, path: Path, cleanable: bool, name: str = "") -> dict[str, Any]:
    size, files, oldest = _walk(path) if path.exists() else (0, 0, None)
    return {"id": key, "name": name, "bytes": size, "files": files, "oldest": oldest,
            "cleanable": cleanable}  # fmt: skip


def analyse(app: Any) -> dict[str, Any]:
    s = app.settings
    root = s.recordings_path
    usage = shutil.disk_usage(s.data_dir)
    items = []
    if root.is_dir():
        cameras = sorted(p for p in root.iterdir() if p.is_dir())
        items += [_entry("recordings", p, True, p.name) for p in cameras]
    media = app.media_dir
    items += [_entry(kind, media / kind, True) for kind in MEDIA_KINDS]
    items.append(_entry("evidence", media / "evidence", False))
    items.append(_entry("faces", s.data_dir / "biometrics.db", False))
    items.append(_entry("database", s.data_dir / "pi-guard.db", True))
    items.append(_entry("models", s.data_dir / "models", False))
    items.append(_entry("sounds", s.data_dir / "sounds", False))
    items.sort(key=lambda i: i["bytes"], reverse=True)
    return {"total": usage.total, "used": usage.used, "free": usage.free, "items": items,
            "retention_days": s.recording_retention_days}  # fmt: skip


def clean(app: Any, target: str, days: int, camera: str = "") -> tuple[int, int]:
    """Delete files older than `days` days; returns (files, bytes)."""
    if target not in CLEANABLE or days < 1:
        raise ValueError(target)
    root = app.settings.recordings_path if target == "recordings" else app.media_dir / target
    if camera:
        root = root / camera
        if root.parent != app.settings.recordings_path or "/" in camera or camera.startswith("."):
            raise ValueError(camera)
    cutoff = time.time() - days * DAY
    removed = freed = 0
    for folder, _, names in os.walk(root):
        for name in names:
            path = Path(folder, name)
            try:
                info = path.stat()
                if info.st_mtime < cutoff:
                    path.unlink()
                    removed += 1
                    freed += info.st_size
            except OSError:
                continue
    return removed, freed

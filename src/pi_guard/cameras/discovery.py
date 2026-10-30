"""V4L2 camera discovery.

Formats are read through FFmpeg so no extra tooling is required. UVC cameras
usually expose two nodes (video capture + metadata); only nodes that report
capture formats are returned.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_FORMAT_RE = re.compile(r"(Raw|Compressed)\s*:\s*(\S+)\s*:\s*(.+?)\s*:\s*(.*)$")
_SIZE_RE = re.compile(r"^(\d+)x(\d+)$")
_V4L_DIRS = (Path("/dev/v4l/by-id"), Path("/dev/v4l/by-path"))


@dataclass(frozen=True)
class VideoFormat:
    name: str
    compressed: bool
    sizes: tuple[tuple[int, int], ...]

    def supports(self, size: tuple[int, int]) -> bool:
        return not self.sizes or size in self.sizes


@dataclass(frozen=True)
class VideoDevice:
    node: Path
    path: Path
    name: str
    formats: tuple[VideoFormat, ...] = field(default_factory=tuple)

    def format(self, name: str) -> VideoFormat | None:
        return next((f for f in self.formats if f.name == name), None)

    @property
    def slug(self) -> str:
        base = re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")
        return (base or self.node.name)[:32]


def parse_formats(output: str) -> tuple[VideoFormat, ...]:
    formats = []
    for line in output.splitlines():
        match = _FORMAT_RE.search(line)
        if not match:
            continue
        sizes = []
        for token in match.group(4).split():
            size = _SIZE_RE.match(token)
            if size:
                sizes.append((int(size.group(1)), int(size.group(2))))
        formats.append(VideoFormat(match.group(2), match.group(1) == "Compressed", tuple(sizes)))
    return tuple(formats)


def probe_formats(node: Path, timeout: float = 10.0) -> tuple[VideoFormat, ...]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "v4l2", "-list_formats", "all", "-i", str(node)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    # FFmpeg exits non-zero after listing because no output was requested.
    return parse_formats(result.stderr)


def stable_paths() -> dict[Path, Path]:
    """Map /dev/videoN to the most stable alias (by-id beats by-path)."""
    aliases: dict[Path, Path] = {}
    for directory in reversed(_V4L_DIRS):
        if directory.is_dir():
            for link in sorted(directory.iterdir()):
                aliases[link.resolve()] = link
    return aliases


def device_name(node: Path) -> str:
    try:
        return (Path("/sys/class/video4linux") / node.name / "name").read_text().strip()
    except OSError:
        return node.name


def discover() -> list[VideoDevice]:
    aliases = stable_paths()
    devices = []
    nodes = sorted(Path("/dev").glob("video*"), key=lambda p: int(p.name[5:] or 0))
    for node in nodes:
        if not node.name[5:].isdigit():
            continue
        formats = probe_formats(node)
        if formats:
            devices.append(VideoDevice(node, aliases.get(node, node), device_name(node), formats))
    return devices


def find(device: str) -> VideoDevice | None:
    node = Path(device).resolve()
    if not node.exists():
        return None
    formats = probe_formats(node)
    if not formats:
        return None
    return VideoDevice(node, Path(device), device_name(node), formats)

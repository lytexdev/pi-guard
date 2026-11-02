"""FFmpeg capability probing and capture command construction.

One FFmpeg process per camera owns the device and fans the stream out to:
  1. H.264 MP4 segments on disk (fragmented, so a crash loses seconds, not files)
  2. small grayscale frames on stdout for motion detection
  3. a periodically refreshed full-size JPEG for snapshots
This avoids several processes fighting over an exclusive V4L2 device.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pi_guard.cameras.discovery import VideoDevice
from pi_guard.config.files import CameraConfig

log = logging.getLogger(__name__)

SEGMENT_PATTERN = "%Y%m%dT%H%M%SZ.mp4"
HARDWARE_ENCODER = "h264_v4l2m2m"
SOFTWARE_ENCODER = "libx264"


@cache
def encoder_works(name: str) -> bool:
    """Listing an encoder is not enough: v4l2m2m is compiled into many builds without hardware."""
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x240:rate=5",
                "-frames:v",
                "5",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                name,
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


@cache
def supports_atomic_snapshots() -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "muxer=image2"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "atomic_writing" in result.stdout


@cache
def ffmpeg_version() -> str | None:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.split("\n", 1)[0] if result.returncode == 0 else None


@dataclass(frozen=True)
class LiveOutputs:
    """File descriptors the capture process writes live picture and sound to."""

    video_fd: int
    fps: float
    width: int
    audio_fd: int | None = None


@dataclass(frozen=True)
class CaptureChoice:
    input_format: str
    encoder: str  # "copy" or an FFmpeg encoder name
    warning: str | None = None


def choose_capture(camera: CameraConfig, device: VideoDevice | None) -> CaptureChoice:
    """Pick input format and encoder: camera H.264 > MJPEG > raw YUYV."""
    size = camera.size
    warning = None
    if camera.input_format != "auto":
        input_format: str = camera.input_format
    elif device is None:
        input_format = "mjpeg"
    else:
        h264 = device.format("h264")
        mjpeg = device.format("mjpeg")
        if h264 and h264.supports(size) and camera.codec in ("auto", "copy"):
            input_format = "h264"
        elif mjpeg and mjpeg.supports(size):
            input_format = "mjpeg"
        elif device.format("yuyv422"):
            input_format = "yuyv422"
            if size[0] * size[1] > 1280 * 720:
                warning = "raw YUYV above 720p usually limits USB 2.0 cameras to a few FPS"
        else:
            input_format = device.formats[0].name if device.formats else "mjpeg"
        supported = [f for f in device.formats if f.supports(size)]
        if not supported:
            warning = (
                f"{camera.resolution} is not advertised by the camera; it may pick another size"
            )

    if camera.codec == "copy" or (camera.codec == "auto" and input_format == "h264"):
        if input_format != "h264":
            raise ValueError(f"camera {camera.id}: codec 'copy' needs input_format h264")
        return CaptureChoice(input_format, "copy", warning)
    if camera.codec != "auto":
        return CaptureChoice(input_format, camera.codec, warning)
    encoder = HARDWARE_ENCODER if encoder_works(HARDWARE_ENCODER) else SOFTWARE_ENCODER
    return CaptureChoice(input_format, encoder, warning)


def detection_size(camera: CameraConfig) -> tuple[int, int]:
    width, height = camera.size
    out_w = camera.motion.analysis_width
    out_h = max(2, round(out_w * height / width / 2) * 2)
    return out_w, out_h


SEGMENT_MOVFLAGS = (
    "movflags=+frag_keyframe+empty_moov+default_base_moof:frag_duration=2000000:flush_packets=1"
)


def build_command(
    camera: CameraConfig,
    choice: CaptureChoice,
    *,
    segment_dir: Path | None,
    segment_seconds: int,
    snapshot_path: Path,
    analysis_fps: float,
    microphone: str | None = None,
    atomic_snapshots: bool = False,
    live: LiveOutputs | None = None,
) -> list[str]:
    width, height = camera.size
    det_w, det_h = detection_size(camera)
    # fmt: off
    args = [
        "ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "warning",
        # Thread pools scale with the core count; a capture pipeline does not need that many.
        "-filter_complex_threads", "2",
        "-threads", "2", "-f", "v4l2", "-thread_queue_size", "64",
        "-input_format", choice.input_format,
        "-video_size", f"{width}x{height}", "-framerate", str(camera.fps),
        "-i", camera.device,
    ]
    if microphone and segment_dir:
        args += ["-f", "alsa", "-thread_queue_size", "1024", "-ac", "1", "-i", microphone]

    encode = segment_dir is not None and choice.encoder != "copy"
    branches = ["det", "snap"] + (["rec"] if encode else []) + (["live"] if live else [])
    chains = [
        f"[0:v]split={len(branches)}" + "".join(f"[{b}_in]" for b in branches),
        f"[det_in]fps={analysis_fps:g},scale={det_w}:{det_h},format=gray[det]",
        f"[snap_in]fps=1/{camera.snapshot_interval_seconds:g}[snap]",
    ]
    if encode:
        chains.append("[rec_in]format=yuv420p[rec]")
    if live:
        chains.append(f"[live_in]fps={live.fps:g},scale='min({live.width},iw)':-2[live]")
    args += ["-filter_complex", ";".join(chains)]

    if segment_dir is not None:
        if encode:
            args += ["-map", "[rec]", "-c:v", choice.encoder, "-b:v", camera.bitrate,
                     "-g", str(camera.fps * 2)]
            if choice.encoder == SOFTWARE_ENCODER:
                args += ["-preset", "veryfast", "-tune", "zerolatency", "-threads", "2",
                         "-maxrate", camera.bitrate, "-bufsize", camera.bitrate]
        else:
            args += ["-map", "0:v", "-c:v", "copy"]
        if microphone:
            args += ["-map", "1:a", "-c:a", "aac", "-b:a", "64k"]
        args += [
            "-f", "segment", "-segment_time", str(segment_seconds), "-segment_atclocktime", "1",
            "-segment_format", "mp4",
            # frag_duration: cameras sending H.264 with rare keyframes would otherwise
            # write nothing for minutes (looks like a stall, and a crash loses it all).
            "-segment_format_options", SEGMENT_MOVFLAGS,
            "-reset_timestamps", "1", "-strftime", "1",
            str(segment_dir / SEGMENT_PATTERN),
        ]

    args += ["-map", "[det]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    args += ["-map", "[snap]", "-q:v", "3", "-f", "image2", "-update", "1"]
    if atomic_snapshots:
        args += ["-atomic_writing", "1"]
    args += ["-y", str(snapshot_path)]
    if live:
        args += ["-map", "[live]", "-c:v", "mjpeg", "-q:v", "6", "-f", "mpjpeg",
                 f"pipe:{live.video_fd}"]
        if live.audio_fd is not None and microphone and segment_dir:
            args += ["-map", "1:a", "-c:a", "libmp3lame", "-b:a", "64k", "-ac", "1",
                     "-f", "mp3", f"pipe:{live.audio_fd}"]
    # fmt: on
    return args

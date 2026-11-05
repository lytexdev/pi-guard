from __future__ import annotations

import asyncio
import contextlib
import shutil
from pathlib import Path

import pytest

from pi_guard.cameras import ffmpeg, pipeline
from pi_guard.cameras.discovery import VideoDevice, VideoFormat, parse_formats
from pi_guard.cameras.ffmpeg import CaptureChoice, build_command, choose_capture, detection_size
from pi_guard.cameras.pipeline import CameraPipeline, CameraState, SnapshotUnavailableError
from pi_guard.config.files import CameraConfig
from pi_guard.core.events import EventBus, EventType

LIST_FORMATS = """\
[in#0 @ 0x5652] Raw       :     yuyv422 :           YUYV 4:2:2 : 640x480 1280x720 1920x1080
[in#0 @ 0x5652] Compressed:       mjpeg :          Motion-JPEG : 640x480 1280x720
[in#0 @ 0x5652] Compressed:        h264 :                H.264 : {640-1920, 2}x{480-1080, 2}
Error opening input file /dev/video0.
"""


def cam(**kwargs: object) -> CameraConfig:
    base: dict[str, object] = {"id": "cam", "name": "Cam", "device": "/dev/video0"}
    return CameraConfig.model_validate(base | kwargs)


def device(*formats: VideoFormat) -> VideoDevice:
    return VideoDevice(Path("/dev/video0"), Path("/dev/v4l/by-id/x"), "Test", formats)


MJPEG = VideoFormat("mjpeg", True, ((640, 480), (1280, 720)))
YUYV = VideoFormat("yuyv422", False, ((640, 480), (1280, 720), (1920, 1080)))
H264 = VideoFormat("h264", True, ((1280, 720),))


def test_parse_ffmpeg_format_listing() -> None:
    formats = parse_formats(LIST_FORMATS)
    assert [f.name for f in formats] == ["yuyv422", "mjpeg", "h264"]
    assert formats[1].sizes == ((640, 480), (1280, 720))
    assert formats[1].compressed and not formats[0].compressed
    assert formats[2].sizes == ()  # stepwise sizes: treated as "anything"
    assert formats[2].supports((800, 600))


def test_metadata_node_has_no_formats() -> None:
    assert parse_formats("ioctl(VIDIOC_G_INPUT): Inappropriate ioctl for device") == ()


def test_camera_h264_is_copied(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not recoded by default: the Pi's encoder hung the capture when used twice at once.
    monkeypatch.setattr("pi_guard.cameras.ffmpeg.encoder_works", lambda _: True)
    choice = choose_capture(cam(), device(YUYV, MJPEG, H264))
    assert choice == CaptureChoice("h264", "copy")


def test_copying_stays_possible_on_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pi_guard.cameras.ffmpeg.encoder_works", lambda _: True)
    choice = choose_capture(cam(codec="copy"), device(YUYV, MJPEG, H264))
    assert choice == CaptureChoice("h264", "copy")


def test_mjpeg_preferred_and_hardware_encoder_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg, "encoder_works", lambda name: name == "h264_v4l2m2m")
    assert choose_capture(cam(), device(YUYV, MJPEG)) == CaptureChoice("mjpeg", "h264_v4l2m2m")
    monkeypatch.setattr(ffmpeg, "encoder_works", lambda name: False)
    assert choose_capture(cam(), device(YUYV, MJPEG)).encoder == "libx264"


def test_yuyv_fallback_warns_for_large_sizes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ffmpeg, "encoder_works", lambda name: False)
    choice = choose_capture(cam(resolution="1920x1080"), device(YUYV, MJPEG))
    assert choice.input_format == "yuyv422"
    assert choice.warning and "YUYV" in choice.warning


def test_copy_requires_h264_input() -> None:
    with pytest.raises(ValueError, match="copy"):
        choose_capture(cam(codec="copy", input_format="mjpeg"), None)


def test_detection_size_keeps_aspect() -> None:
    assert detection_size(cam(resolution="1920x1080")) == (320, 180)
    assert detection_size(cam(resolution="640x480")) == (320, 240)


def test_command_with_encoding_has_three_outputs(tmp_path: Path) -> None:
    args = build_command(
        cam(),
        CaptureChoice("mjpeg", "libx264"),
        segment_dir=tmp_path,
        segment_seconds=300,
        snapshot_path=tmp_path / "s.jpg",
        analysis_fps=4,
    )
    graph = args[args.index("-filter_complex") + 1]
    assert "split=3" in graph and "scale=320:180,format=gray" in graph
    assert args.count("-map") == 3
    assert "segment" in args and "pipe:1" in args
    assert args[args.index("-input_format") + 1] == "mjpeg"
    assert "-g" in args and args[args.index("-g") + 1] == "30"


def test_command_copy_and_no_recording(tmp_path: Path) -> None:
    copy = build_command(
        cam(),
        CaptureChoice("h264", "copy"),
        segment_dir=tmp_path,
        segment_seconds=300,
        snapshot_path=tmp_path / "s.jpg",
        analysis_fps=2,
    )
    assert "split=2" in copy[copy.index("-filter_complex") + 1]
    assert copy[copy.index("-c:v") + 1] == "copy"
    off = build_command(
        cam(),
        CaptureChoice("mjpeg", "libx264"),
        segment_dir=None,
        segment_seconds=300,
        snapshot_path=tmp_path / "s.jpg",
        analysis_fps=2,
    )
    assert "segment" not in off and "-c:v" not in off


def test_microphone_only_added_when_recording(tmp_path: Path) -> None:
    args = build_command(
        cam(),
        CaptureChoice("mjpeg", "libx264"),
        segment_dir=tmp_path,
        segment_seconds=300,
        snapshot_path=tmp_path / "s.jpg",
        analysis_fps=2,
        microphone="plughw:CARD=Mic,DEV=0",
    )
    assert "alsa" in args and "1:a" in args


# --- pipeline supervision with a synthetic FFmpeg source -------------------------------


async def eventually(condition, timeout: float = 8.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition not met in time"
        await asyncio.sleep(0.05)


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _fake_capture(seconds: float):  # type: ignore[no-untyped-def]
    def build(camera: CameraConfig, choice: CaptureChoice, **kw: object) -> list[str]:
        w, h = detection_size(camera)
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={w}x{h}:rate=10:duration={seconds}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "pipe:1",
        ]

    return build


@needs_ffmpeg
@pytest.mark.ffmpeg
async def test_pipeline_delivers_frames_and_reports_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device_file = tmp_path / "video0"
    device_file.touch()
    monkeypatch.setattr(pipeline, "build_command", _fake_capture(3))
    monkeypatch.setattr(pipeline, "choose_capture", lambda c, d: CaptureChoice("mjpeg", "libx264"))
    monkeypatch.setattr(pipeline.discovery, "find", lambda d: None)
    monkeypatch.setattr(pipeline, "DISCONNECT_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(pipeline, "FIRST_FRAME_TIMEOUT", 3.0)

    bus = EventBus()
    events = bus.subscribe("test")
    p = CameraPipeline(
        cam(device=str(device_file)),
        bus,
        segment_dir=None,
        segment_seconds=60,
        runtime_dir=tmp_path,
        analysis_fps=4,
    )
    frames: list[tuple[int, int]] = []
    p.add_frame_handler(lambda frame, ts: frames.append(frame.shape))
    task = asyncio.create_task(p.run())
    await eventually(lambda: p.status.state is CameraState.RUNNING)
    device_file.unlink()  # unplug; the capture then ends and cannot restart
    await eventually(lambda: not events.empty())
    assert frames and frames[0] == (180, 320)
    assert p.status.state is CameraState.OFFLINE
    assert events.get_nowait().type is EventType.CAMERA_DISCONNECTED

    device_file.touch()  # plug back in
    await eventually(lambda: not events.empty())
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert events.get_nowait().type is EventType.CAMERA_RECONNECTED
    assert p.status.state is CameraState.RUNNING


async def test_snapshot_validates_and_copies(tmp_path: Path) -> None:
    p = CameraPipeline(
        cam(),
        EventBus(),
        segment_dir=None,
        segment_seconds=60,
        runtime_dir=tmp_path,
        analysis_fps=4,
    )
    with pytest.raises(SnapshotUnavailableError):
        await p.snapshot(tmp_path / "out.jpg")
    p.snapshot_path.write_bytes(b"\xff\xd8partial")
    with pytest.raises(SnapshotUnavailableError):
        await p.snapshot(tmp_path / "out.jpg")
    p.snapshot_path.write_bytes(b"\xff\xd8image\xff\xd9")
    out = await p.snapshot(tmp_path / "media" / "out.jpg")
    assert out.read_bytes().endswith(b"\xff\xd9")

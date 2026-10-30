from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from pi_guard.audio import service as service_module
from pi_guard.audio.devices import parse
from pi_guard.audio.service import AudioError, AudioService
from pi_guard.audio.sounds import SoundLibrary
from pi_guard.config.files import AudioSettings, FileConfig
from pi_guard.messaging.base import Incoming

from .helpers import ADMIN, VIEWER, camera, make_app, msg

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

CARDS = """\
 0 [QuadCast       ]: USB-Audio - HyperX QuadCast
                      HP, Inc HyperX QuadCast at usb-0000:2f:00.3-1, full speed
 1 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0
                      vc4-hdmi-0
 4 [WEBCAM         ]: USB-Audio - C270 HD WEBCAM
                      C270 HD WEBCAM at usb-0000:2f:00.3-3, high speed
"""
PCM = """\
00-00: USB Audio : USB Audio : playback 1 : capture 1
01-00: MAI PCM i2s-hifi-0 : MAI PCM i2s-hifi-0 : playback 1
04-00: USB Audio : USB Audio : capture 1
"""


def test_parse_alsa_devices() -> None:
    devices = parse(CARDS, PCM)
    assert [d.alsa_name for d in devices] == [
        "plughw:CARD=QuadCast,DEV=0",
        "plughw:CARD=vc4hdmi0,DEV=0",
        "plughw:CARD=WEBCAM,DEV=0",
    ]
    assert devices[0].playback and devices[0].capture and devices[0].usb
    assert devices[1].playback and not devices[1].capture
    assert devices[2].capture and not devices[2].playback


def test_builtin_sounds_are_generated(tmp_path: Path) -> None:
    library = SoundLibrary(tmp_path)
    path = library.path("doorbell")
    assert path is not None and path.stat().st_size > 10_000
    assert library.names()[:3] == ["doorbell", "chime", "warning"]
    assert library.path("../../etc/passwd") is None
    assert library.path("missing") is None


def test_custom_sound_names_are_restricted(tmp_path: Path) -> None:
    library = SoundLibrary(tmp_path)
    upload = tmp_path / "x.ogg"
    upload.write_bytes(b"x")
    with pytest.raises(ValueError):
        library.save("doorbell", upload)  # cannot shadow a built-in
    with pytest.raises(ValueError):
        library.save("Bad Name", upload)
    saved = library.save("hallo", upload)
    assert library.path("hallo") == saved and "hallo" in library.names()
    assert library.delete("hallo") and not saved.exists()


def service(tmp_path: Path, db: Any, **kw: Any) -> AudioService:
    settings = AudioSettings(
        speaker=kw.pop("speaker", "plughw:CARD=Nope,DEV=0"),
        microphone=None,
        max_upload_seconds=10,
        **kw,
    )
    return AudioService(
        settings,
        db,
        SoundLibrary(tmp_path / "sounds"),
        ZoneInfo("UTC"),
        playback_enabled=True,
        recording_enabled=False,
        max_volume=70,
    )


def make_audio(path: Path, seconds: float, codec: str = "libopus", video: bool = False) -> Path:
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={seconds}",
    ]
    if video:
        args += ["-f", "lavfi", "-i", f"testsrc2=size=64x64:duration={seconds}"]
    args += ["-c:a", codec, str(path)]
    subprocess.run(args, check=True)
    return path


def test_volume_is_capped(tmp_path: Path, db: Any) -> None:
    audio = service(tmp_path, db)
    assert audio.volume == 60
    assert audio.set_volume(100) == 70
    assert audio.volume == 70


@needs_ffmpeg
@pytest.mark.ffmpeg
async def test_upload_validation(tmp_path: Path, db: Any) -> None:
    audio = service(tmp_path, db)
    voice = make_audio(tmp_path / "voice.ogg", 2)
    info = await audio.validate_upload(voice)
    assert info.codec == "opus" and 1.5 < info.duration < 2.5
    with pytest.raises(AudioError, match="too_long"):
        await audio.validate_upload(make_audio(tmp_path / "long.ogg", 12))
    with pytest.raises(AudioError, match="invalid_file"):
        await audio.validate_upload(make_audio(tmp_path / "clip.mp4", 1, "aac", video=True))
    playlist = tmp_path / "voice.ogg"
    playlist.write_text("#EXTM3U\n#EXTINF:1,\nfile:///etc/passwd\n")
    with pytest.raises(AudioError, match="invalid_file"):
        await audio.validate_upload(playlist)
    script = tmp_path / "evil.ogg"
    script.write_text("#!/bin/sh\nrm -rf /\n")
    with pytest.raises(AudioError, match="invalid_file"):
        await audio.validate_upload(script)


@needs_ffmpeg
@pytest.mark.ffmpeg
async def test_playback_errors(tmp_path: Path, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = service(tmp_path, db)
    sound = audio.sounds.path("chime")
    assert sound is not None
    with pytest.raises(AudioError, match="playback_failed"):
        await audio.play(sound)  # the ALSA device does not exist

    quiet = service(tmp_path, db, quiet_hours="00:00-23:59")
    with pytest.raises(AudioError, match="quiet_hours"):
        await quiet.play(sound)

    commands: list[list[str]] = []

    async def fake_run(args: list[str], limit: float) -> tuple[int, bytes, bytes]:
        commands.append(args)
        if args[0] == "ffmpeg":
            await asyncio.sleep(0.1)
            return 0, b"", b""
        return await real_run(args, limit)

    real_run = service_module._run
    monkeypatch.setattr(service_module, "_run", fake_run)
    first = asyncio.create_task(audio.play(sound))
    await asyncio.sleep(0.05)
    with pytest.raises(AudioError, match="busy"):
        await audio.play(sound)
    await first
    played = commands[-1]
    assert played[played.index("-af") + 1] == "volume=0.60"
    assert played[-1] == "plughw:CARD=Nope,DEV=0"


def _audio_app(tmp_path: Path, **settings: Any):  # type: ignore[no-untyped-def]
    file = FileConfig(cameras=[camera()], audio=AudioSettings(speaker="plughw:CARD=X,DEV=0"))
    return make_app(tmp_path, file=file, audio_playback_enabled=True, **settings)


async def test_viewers_cannot_use_audio(tmp_path: Path) -> None:
    app = _audio_app(tmp_path)
    for command in ("/sound doorbell", "/volume 80", "/listen 10"):
        await app.messenger.handle(msg(command, VIEWER))
        assert "administrator" in app.providers["telegram"].last().text
    app.db.close()


async def test_listen_needs_explicit_enable(tmp_path: Path) -> None:
    app = _audio_app(tmp_path)
    await app.messenger.handle(msg("/listen 10", ADMIN))
    assert "switched off" in app.providers["telegram"].last().text
    app.db.close()


@needs_ffmpeg
@pytest.mark.ffmpeg
async def test_upload_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = _audio_app(tmp_path)
    played: list[Path] = []

    async def fake_play(path: Path, **kw: Any) -> None:
        played.append(path)

    monkeypatch.setattr(app.audio, "play", fake_play)
    voice = make_audio(tmp_path / "voice.ogg", 1)
    await app.messenger.handle(
        Incoming("telegram", ADMIN, ADMIN, "", attachment=voice, attachment_kind="audio")
    )
    reply = app.providers["telegram"].last()
    play, save, _discard, _menu = (row[0].command for row in reply.buttons)
    await app.messenger.handle(msg(play, ADMIN, from_button=True))
    assert len(played) == 1 and played[0].exists()
    await app.messenger.handle(msg(save, ADMIN, from_button=True))
    assert any(name.startswith("message_") for name in app.audio.sounds.names())
    await app.messenger.handle(msg(play, ADMIN, from_button=True))
    assert "no longer available" in app.providers["telegram"].last().text
    app.db.close()


@pytest.mark.hardware
async def test_record_from_real_microphone(tmp_path: Path, db: Any) -> None:
    """Needs TEST_MICROPHONE, e.g. plughw:CARD=WEBCAM,DEV=0."""
    import os

    device = os.environ.get("TEST_MICROPHONE")
    if not device:
        pytest.skip("TEST_MICROPHONE not set")
    audio = AudioService(
        AudioSettings(),
        db,
        SoundLibrary(tmp_path),
        ZoneInfo("UTC"),
        playback_enabled=False,
        recording_enabled=True,
        max_volume=80,
    )
    path = await audio.record(device, 2, tmp_path / "mic.m4a")
    info = await service_module.probe(path)
    assert info.codec == "aac" and info.duration > 1.5

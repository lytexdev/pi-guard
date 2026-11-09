"""Evidence videos: record for some minutes, with sound and traceable metadata.

The result is an MP4, a JSON file and a readable report. The JSON holds when, where and by
whom it was requested, whether the clock was synchronised, the video's own technical data,
the events in that time, the device, and checksums of the video and of every recording
segment it was cut from. The JSON is signed with this device's own Ed25519 key, so changing
the video or the record afterwards shows (`pi-guard evidence verify FILE.json`). This is
documentation for an insurance or the police, not a qualified electronic signature.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import socket
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pi_guard import __version__
from pi_guard.audio.service import AudioError
from pi_guard.core.timeutil import utcnow
from pi_guard.recording.segments import extract_clip, list_segments, overlapping
from pi_guard.security.files import secure_file

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

MAX_MINUTES = 30
SEND_LIMIT_BYTES = 45_000_000  # Telegram bots may send 50 MB
FLUSH_SECONDS = 4


@dataclass(frozen=True)
class Evidence:
    video: Path
    metadata: Path
    sha256: str
    seconds: int
    has_sound: bool
    size: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


async def _ffmpeg(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, err = await process.communicate()
    if process.returncode != 0:
        raise OSError(err.decode(errors="replace")[-200:])


async def record_evidence(app: App, camera_id: str, minutes: int, actor: str) -> Evidence:
    """Runs for `minutes`; recording goes on as usual meanwhile."""
    pipeline = app.cameras[camera_id]
    if pipeline.segment_dir is None:
        raise ValueError("this camera does not record")
    seconds = max(1, min(minutes, MAX_MINUTES)) * 60
    directory = app.media_dir / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    start = utcnow()
    name = f"evidence_{camera_id}_{start:%Y%m%dT%H%M%SZ}"
    audio = app.audio
    sound_task = None
    sound = directory / f"{name}.m4a"
    separate_mic = bool(
        not pipeline.microphone and audio and audio.recording_enabled and audio.settings.microphone
    )
    if audio is not None and (separate_mic or pipeline.microphone):
        chime = audio.sounds.path("chime")
        if audio.playback_enabled and chime:  # a microphone is always announced
            try:
                await audio.play(chime, ignore_quiet_hours=True)
            except AudioError:
                log.info("could not announce the microphone")
    if separate_mic and audio is not None and audio.settings.microphone:
        sound_task = asyncio.create_task(
            audio.record(audio.settings.microphone, seconds, sound, limit=seconds)
        )
    await asyncio.sleep(seconds + FLUSH_SECONDS)
    end = start + timedelta(seconds=seconds)
    video = directory / f"{name}.mp4"
    raw = directory / f"{name}.raw.mp4"
    await extract_clip(pipeline.segment_dir, start, end, raw)
    has_sound = bool(pipeline.microphone)
    inputs = ["-i", str(raw)]
    maps = ["-map", "0"]
    if sound_task is not None:
        try:
            await sound_task
            inputs += ["-i", str(sound)]
            maps = ["-map", "0:v", "-map", "1:a"]
            has_sound = True
        except AudioError:
            log.warning("evidence video without sound: microphone not available")
    comment = f"pi-guard evidence; camera {pipeline.camera.name}; requested by {actor}"
    await _ffmpeg(*inputs, *maps, "-c", "copy", "-movflags", "+faststart", "-metadata",
                  f"creation_time={start.isoformat()}", "-metadata", f"comment={comment}",
                  str(video))  # fmt: skip
    raw.unlink(missing_ok=True)
    sound.unlink(missing_ok=True)
    digest = await asyncio.to_thread(_sha256, video)
    sources = overlapping(list_segments(pipeline.segment_dir), start, end)
    info: dict[str, Any] = {
        "camera": pipeline.camera.name, "camera_id": camera_id, "room": pipeline.camera.room,
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "start_local": start.astimezone(app.settings.tz).isoformat(),
        "end_local": end.astimezone(app.settings.tz).isoformat(),
        "time_zone": app.settings.timezone,
        "duration_seconds": seconds, "sound": has_sound, "requested_by": actor,
        "mode": app.modes.effective.value, "device": socket.gethostname(),
        "software": f"pi-guard {__version__}", "file": video.name, "sha256": digest,
        "size": video.stat().st_size,
        "clock": await asyncio.to_thread(clock_state),
        "hardware": hardware(),
        # The camera's address is left out: for network cameras it can hold a password.
        "camera_settings": {"source": pipeline.camera.source,
                            "resolution": pipeline.camera.resolution, "fps": pipeline.camera.fps,
                            "codec": pipeline.camera.codec},
        "video": await probe(video),
        "segments": [{"file": s.path.name, "start_utc": s.start.isoformat(),
                      "sha256": await asyncio.to_thread(_sha256, s.path)}
                     for s in sources if s.path.exists()],
        "events": [{"time_utc": r["ts"], "type": r["type"]} for r in app.db.execute(
            "SELECT ts, type FROM events WHERE camera = ? AND ts BETWEEN ? AND ? ORDER BY ts",
            (camera_id, (start - timedelta(minutes=1)).isoformat(),
             (end + timedelta(minutes=1)).isoformat())).fetchall()],
        "note": "Cuts snap to keyframes, so the video may start up to two seconds early.",
    }  # fmt: skip
    info["signature"] = sign(info, signing_key(app.settings.data_dir))
    metadata = directory / f"{name}.json"
    metadata.write_text(json.dumps(info, indent=2, ensure_ascii=False))
    (directory / f"{name}.txt").write_text(report(info, app.settings.language))
    app.db.audit(actor, "evidence", f"{video.name} {digest[:16]}")
    return Evidence(video, metadata, digest, seconds, has_sound, video.stat().st_size)


# --- what goes into the record ---------------------------------------------------------------


def clock_state() -> dict[str, Any]:
    """Whether the system clock was set from a time server when the video was made."""
    import subprocess

    try:
        command = ["timedatectl", "show", "-p", "NTPSynchronized", "-p", "NTP"]
        out = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {"synchronized": None}
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {
        "synchronized": values.get("NTPSynchronized") == "yes",
        "ntp": values.get("NTP") == "yes",
    }


def hardware() -> dict[str, str]:
    found: dict[str, str] = {}
    with contextlib.suppress(OSError):
        found["model"] = Path("/proc/device-tree/model").read_text().strip("\x00 \n")
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("Serial"):
                found["serial"] = line.split(":", 1)[1].strip()
    except OSError:
        pass
    return found


async def probe(video: Path) -> dict[str, Any]:
    """The video's own data, as ffprobe reads it."""
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(video), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await process.communicate()
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    streams = [{k: s.get(k) for k in ("codec_type", "codec_name", "width", "height",
                                      "avg_frame_rate", "sample_rate", "channels", "nb_frames")
                if s.get(k) is not None} for s in data.get("streams", [])]  # fmt: skip
    form = data.get("format", {})
    return {"duration_seconds": float(form.get("duration") or 0), "format": form.get("format_name"),
            "streams": streams}  # fmt: skip


# --- signature -------------------------------------------------------------------------------


def canonical(info: dict[str, Any]) -> bytes:
    body = {k: v for k, v in info.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def signing_key(data_dir: Path) -> Any:
    """This device's own key for evidence, made on first use. It never leaves the device."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    path = data_dir / "evidence-ed25519.pem"
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))  # fmt: skip
    secure_file(path)
    return key


def sign(info: dict[str, Any], key: Any) -> dict[str, str]:
    from cryptography.hazmat.primitives import serialization

    public = key.public_key().public_bytes(serialization.Encoding.Raw,
                                           serialization.PublicFormat.Raw)  # fmt: skip
    return {"algorithm": "Ed25519", "public_key": public.hex(),
            "value": key.sign(canonical(info)).hex()}  # fmt: skip


def verify(metadata: Path) -> list[str]:
    """What is wrong with an evidence record and its video; empty when everything matches."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    problems = []
    info = json.loads(metadata.read_text())
    signature = info.get("signature") or {}
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(signature["public_key"])).verify(
            bytes.fromhex(signature["value"]), canonical(info)
        )
    except (KeyError, ValueError, InvalidSignature):
        problems.append("the record's signature does not match: it was changed or never signed")
    video = metadata.with_name(str(info.get("file", "")))
    if not video.is_file():
        problems.append(f"the video {video.name} is missing")
    elif _sha256(video) != info.get("sha256"):
        problems.append("the video does not match its checksum: it was changed")
    return problems


def report(info: dict[str, Any], language: str) -> str:
    """The record in words, for people who read it on paper."""
    from pi_guard.i18n import t

    def line(key: str, value: Any) -> str:
        return f"{t(f'evidence.{key}', language)}: {value}"

    clock = info.get("clock", {}).get("synchronized")
    rows = [t("evidence.report_title", language), "",
            line("camera", f"{info['camera']} ({info.get('room') or '-'})"),
            line("from", info["start_local"]), line("to", info["end_local"]),
            line("time_zone", info["time_zone"]),
            line("clock", t("evidence.clock_yes" if clock else "evidence.clock_no", language)),
            line("requested_by", info["requested_by"]), line("mode", info["mode"]),
            line("sound", t("evidence.yes" if info["sound"] else "evidence.no", language)),
            line("device", f"{info['device']} {info.get('hardware', {}).get('model', '')}".strip()),
            line("serial", info.get("hardware", {}).get("serial", "-")),
            line("software", info["software"]), "",
            line("file", info["file"]), line("sha256", info["sha256"]),
            line("size", f"{info['size']} bytes"),
            line("segments", len(info.get("segments", []))), "",
            t("evidence.events", language) + ":"]  # fmt: skip
    for event in info["events"]:
        kind = t("event." + event["type"], language)
        rows.append(f"  {event['time_utc']}  {kind}")
    rows += ["", line("public_key", info["signature"]["public_key"]),
             line("signature", info["signature"]["value"]), "",
             t("evidence.how_to_check", language)]  # fmt: skip
    return "\n".join(rows) + "\n"


def evidence_files(media_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((media_dir / "evidence").glob("*.json"), reverse=True):
        try:
            info = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        video = path.with_suffix(".mp4")
        report = path.with_suffix(".txt")
        rows.append({**info, "size": video.stat().st_size if video.exists() else 0,
                     "metadata": path.name, "report": report.name if report.exists() else None,
                     "signed": "signature" in info})  # fmt: skip
    return rows

"""Generates a commented starter config.yaml from detected hardware."""

from __future__ import annotations

import json

from pi_guard.audio.devices import AudioDevice
from pi_guard.cameras.discovery import VideoDevice

_PREFERRED_SIZES = ((1280, 720), (1920, 1080), (1024, 576), (800, 600), (640, 480))


def _resolution(device: VideoDevice) -> str:
    fmt = device.format("h264") or device.format("mjpeg") or (device.formats or (None,))[0]
    sizes = fmt.sizes if fmt else ()
    for size in _PREFERRED_SIZES:
        if not sizes or size in sizes:
            return f"{size[0]}x{size[1]}"
    width, height = sizes[0]
    return f"{width}x{height}"


def _q(value: object) -> str:
    """Quote a value for YAML. Device names contain ': ' and other special characters
    (e.g. "HD Web Camera: HD Web Camera"); a JSON string is a valid YAML scalar."""
    return json.dumps(str(value), ensure_ascii=False)


def _prefer_usb(devices: list[AudioDevice]) -> AudioDevice | None:
    """USB sound devices are what people plug in for pi-guard; HDMI is rarely wanted."""
    return next((d for d in devices if d.usb), devices[0] if devices else None)


def _pick_microphone(devices: list[AudioDevice], cameras: list[VideoDevice]) -> AudioDevice | None:
    """A dedicated USB microphone (a card that cannot play sound) beats the dummy input of
    USB speakers and sound cards, which often records nothing but noise; a separate
    microphone beats the one built into a webcam."""
    playback_cards = {d.card for d in devices if d.playback}
    capture = [d for d in devices if d.capture]
    dedicated = [d for d in capture if d.usb and d.card not in playback_cards]
    camera_names = {c.name.split(":")[0].strip().casefold() for c in cameras} - {""}

    def in_camera(device: AudioDevice) -> bool:
        return any(name in device.name.casefold() for name in camera_names)

    ranked = sorted(dedicated, key=in_camera)
    return ranked[0] if ranked else _prefer_usb(capture)


def starter_config(cameras: list[VideoDevice], audio: list[AudioDevice]) -> str:
    lines = [
        "# pi-guard configuration. Reference: docs/configuration.md",
        "# Secrets and on/off switches live in the .env file, not here.",
        "",
        "cameras:",
    ]
    seen: set[str] = set()
    for device in cameras:
        camera_id = device.slug
        while camera_id in seen:
            camera_id = f"{camera_id[:28]}_{len(seen)}"
        seen.add(camera_id)
        lines += [
            f"  - id: {camera_id}",
            f"    name: {_q(device.name)}",
            f"    device: {_q(device.path)}",
            f"    resolution: {_resolution(device)}",
            "    fps: 15",
            "    recording: true",
            "    motion_detection: true",
            "    # motion:",
            "    #   sensitivity: 50",
            "    #   zones:",
            "    #     - name: street",
            "    #       kind: exclude",
            "    #       points: [[0.0, 0.0], [1.0, 0.0], [1.0, 0.3], [0.0, 0.3]]",
        ]
    if not cameras:
        lines.append("  []  # no camera detected; see `pi-guard cameras`")

    speaker = _prefer_usb([d for d in audio if d.playback])
    microphone = _pick_microphone(audio, cameras)
    lines += ["", "audio:"]
    lines.append(f"  speaker: {_q(speaker.alsa_name) if speaker else 'null'}")
    lines.append(f"  microphone: {_q(microphone.alsa_name) if microphone else 'null'}")
    lines += [f"  # available: {d.alsa_name} ({d.name})" for d in audio]
    lines += [
        '  quiet_hours: "22:00-07:00"',
        "",
        "presence:",
        "  sources: [neighbours]   # add fritzbox for AVM routers",
        "  devices: []",
        "  # devices:",
        "  #   - id: phone",
        "  #     name: My phone",
        '  #     mac_address: "AA:BB:CC:DD:EE:FF"',
        "",
        "notifications:",
        '  quiet_hours: "23:00-06:00"',
        "  rules: {}",
        "",
    ]
    return "\n".join(lines)

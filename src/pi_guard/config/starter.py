"""Generates a commented starter config.yaml from detected hardware."""

from __future__ import annotations

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
            f"    name: {device.name}",
            f"    device: {device.path}",
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

    speakers = [d for d in audio if d.playback]
    microphones = [d for d in audio if d.capture]
    lines += ["", "audio:"]
    lines.append(f"  speaker: {speakers[0].alsa_name if speakers else 'null'}")
    lines.append(f"  microphone: {microphones[0].alsa_name if microphones else 'null'}")
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

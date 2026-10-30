# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

First release.

### Added

- Continuous recording to fragmented MP4 segments from USB (V4L2) cameras,
  using camera H.264, the Raspberry Pi hardware encoder or libx264; one FFmpeg
  process per camera also provides motion frames and snapshots.
- Retention by age, event-only recording, buffer-only cameras and deletion of
  the oldest recordings under disk pressure.
- Camera watchdog with automatic reconnection and disconnect/reconnect alerts.
- Motion detection with zones, schedules, sensitivity, minimum area and
  duration, and light-change suppression.
- Optional person detection (HOG, YuNet) and opt-in face recognition (SFace)
  with consent confirmation and a separate biometric store.
- HOME/AWAY modes with timed manual overrides and automatic presence detection
  (neighbour table, FRITZ!Box TR-064) that reports UNKNOWN instead of guessing.
- Alert rules per event type: modes, priority, recipients, cooldown, quiet
  hours, attachments, hourly cap, retries.
- Telegram bot with Simple and Advanced views, English and German, aliases,
  invitations and roles; optional Signal via signal-cli.
- Optional audio: bounded microphone clips, speaker playback of built-in,
  saved and uploaded sounds, volume limit, quiet hours.
- Optional MQTT bridge with Home Assistant discovery.
- CLI: `doctor`, `status`, `health`, `cameras`, `mode`, `logs`, `config`,
  `audio`, `telegram`, `signal`, `presence`, `models`, `faces`.
- Native installer, update and uninstall scripts, hardened systemd unit,
  optional Docker Compose deployment.

# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Add-ons, chosen in the setup assistant and managed in the dashboard, with commands
  in every messenger: service watcher, network analysis (from Pi-hole), smart home
  over MQTT with Home Assistant discovery, remote commands over SSH with two
  confirmations, and an AI assistant (OpenAI, Mistral or any compatible API, DeepL,
  Open-Meteo) with cache, short prompts and a daily token limit.
- WhatsApp through the official Cloud API.
- Overview shows how you are connected: home network, home VPN (with the device's
  name), Tor, another VPN, and the public addresses involved.
- A logo.

- WireGuard VPN managed in the dashboard: `scripts/wireguard.sh setup` prepares
  the server once; devices (family phones, laptops) are added with a name and
  a QR code for the WireGuard app, shown again or removed later. A root path
  unit loads them after strict validation; pi-guard itself stays unprivileged.

- `PI_GUARD_PRESENCE_AUTO_HOME` / `PI_GUARD_PRESENCE_AUTO_AWAY`: limit which way
  presence detection switches the mode; an arriving trusted device can trigger
  an "I am at home" prompt instead of silencing alerts.
- Trusted people: enrollment sessions with photo series, videos and pictures
  taken by the camera, enrollment from alerts, consent before storing;
  `pi-guard faces enroll` accepts videos.
- `/sharper`, `/zoom` (nine areas) and `/brighter`; photo buttons for them;
  `/photo all`.
- `/alarm` with confirmation and stop; uploaded audio as alarm sound; Alarm and
  Listen buttons for admins.
- Everyday sentences in private chats, with buttons when unclear.
- Credit "made by lytex" in help, `pi-guard version` and the startup log.

- Optional web dashboard (see docs/dashboard.md) with password/key-file
  login, optional TOTP, local HTTPS certificate authority, CSRF protection,
  rate limits, bans, honeypots and noindex; live view, listening,
  push-to-talk, image tools, trusted people, sounds and alarm, device
  selection, settings, users, key files, sessions, statistics and logs;
  installable PWA with optional Web Push.
- Emergency contacts for alerts that stay unconfirmed; "Everything is fine"
  button and `ok` command.
- Pi-hole v6 integration (statistics, pause, LAN devices, presence source),
  WireGuard/PiVPN status, `scripts/network.sh` for a fixed address with
  automatic rollback, Pi-hole Docker profile.

- Dashboard login starts as admin / admin123 and must be changed at the first
  login; two-factor codes are set up with a QR code in the dashboard;
  `PI_GUARD_DASHBOARD_SECRET_KEY` peppers the password hash and encrypts the
  stored TOTP secret; `pi-guard dashboard reset-password`.
- Setup assistant in the dashboard (language, login, devices, Telegram,
  alerts, emergency contacts, trusted people with model download, design);
  light, high-contrast and large-text designs; Telegram can be set up in the
  dashboard after a start without bot token.

### Removed

- `PI_GUARD_DASHBOARD_USERNAME`, `…_PASSWORD`, `…_PASSWORD_HASH`, `…_TOTP_SECRET`
  and `pi-guard dashboard password`/`totp`: the login now lives in the
  database and is managed in the dashboard.

### Fixed

- Upload hardening described in an earlier commit was missing in
  `audio/service.py`: uploads are now checked by file signature and FFmpeg only
  reads local files. Stopping playback kills FFmpeg.
- Files too large to download got "I did not understand" instead of a clear
  message.

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

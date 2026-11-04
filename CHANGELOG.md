# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Connected computers: a small agent in Rust (`agent/`) connects out to pi-guard, with status,
  safe shutdown confirmed twice, and Docker management. Shown on the map.
- Add-on ecosystem: manifest with name, icon, version, source and authors; settings forms,
  dashboard panels, events and storage for plugins; install from Git; `pi-guard plugin new`,
  `check` and `pack`.
- Export of all data as JSON, TXT and CSV; config.json to back up and read in settings.
- More languages through DeepL.
- Pictures open large on click; icons on the map; VPN form opens on demand and devices show
  details; link in the credit.

### Fixed

- The lines of the network map were almost invisible.

- Updater: from the dashboard or `scripts/update.sh`, fast-forward only, with health check and
  automatic return to the previous version.
- Network management: Wi-Fi networks and device names from the FRITZ!Box, own device names,
  recognition by domains, blocking websites per device and for a while, block page.
- Doing things by asking, with confirmation: smart home, remote commands, video, VPN access,
  planned sound recordings; chat widget with voice; AI may look at camera pictures if allowed.
- Camera room with all cameras at once.
- API with tokens (`/api/v1`) and OpenAPI description.
- Own add-ons in the dashboard: ZIP upload with plugin.json, create, edit, delete, search, pin.
- Settings in one page with sections, search and save bar; recovery of what is stored per person;
  arrows on the map; links in security recommendations; back to top.

- Login with Telegram (number comparison), passkeys and hardware keys (WebAuthn), TOTP
  recovery codes and an optional code word.
- Dashboard behind a domain and reverse proxy: public URL, host name check, secure cookies.
- Security analysis under Logs; interactive network map; redesigned overview.
- Accessibility panel: text size and spacing, colours, colour blindness filters, reading
  aloud, large pointer, reading ruler, magnifier, tutorial.
- Notification switches per kind of message, effective at once.
- Cameras by RTSP, HTTP/MJPEG (ESP32-CAM) and the Raspberry Pi camera module; rooms.
- Evidence videos with sound, metadata and SHA-256 (`/beweis`).
- Device details (kind, system, open services) and Pi-hole block lists in the dashboard.
- Built-in sounds can be replaced, renamed, deleted and restored.
- AI assistant: Claude, Gemini, Grok and Ollama; knowledge from TXT, MD, JSON and PDF;
  Paperless-ngx and Nextcloud; stored answers to frequent questions.
- Plugin API; `scripts/mqtt.sh` provides a broker.

### Fixed

- Speech through the speaker was inaudible: hardware mixer at -24 dB and quiet recordings.
- The link to Pi-hole used 127.0.0.1 instead of the address of the Pi.

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

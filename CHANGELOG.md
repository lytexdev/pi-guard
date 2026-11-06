# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- The dashboard shows a placeholder while a page loads, instead of an empty area.
- The dashboard opens faster: it asks for the session once at the start and no longer waits for it when changing pages,
  the overview no longer waits for its two slowest panels, and the events page asks for everything at once.
- The dashboard's files and texts travel compressed, about a third of their size. The files are packed when the
  package is built, so the Pi spends no CPU on it.
- When the disk reaches its limit, the oldest recordings without motion go first, then the oldest with motion or a
  person, and the last few minutes last. Before, age alone decided.
- People are cards with picture and quality, and the network overview starts with the numbers that matter.
- The web dashboard and the phone app are on by default.
- The add-on for connected computers is now called Home servers.
- Settings open in a normal view with what most people need; a switch shows everything for experts.

### Added

- The log names dashboard requests that take longer than half a second.
- Quantum-resistant VPN on phones and on Windows too: one key exchange in Rust (`vpn/kem`) with ML-KEM-1024 and HQC-256
  for the Pi, computers and phones, inside the tunnel. Computers use it instead of Rosenpass; the Pi keeps Rosenpass
  until every device has updated. `vpn-server.sh install` must run once more on the Pi.
- Adding a trusted person explains what works best and can take guided photos or a video on the spot, in
  the browser and in the app.
- The app creates its own VPN access when a phone is paired and has a VPN screen behind the shield at the top:
  one button, device, address, data, last contact, reconnects after a restart, works as always-on VPN.
- Four ways home from outside, shown with their state under Network › VPN: IPv4, IPv6, a rented server and
  Tailscale. The app tries them in turn and finds the dashboard at other addresses. `scripts/relay.sh` sets up a
  server that passes the VPN on without being able to read it, optionally with the dashboard through Caddy.
- Agents, desktop apps and the phone app update themselves from your pi-guard. Agents and desktop apps only accept
  releases signed with a key that never comes to the Pi, and never an older version. The phone app needs no tap from the
  second update on (Android 12 and newer).
- The phone app can look like a calculator, notes and more, and can get a shortcut with any name and picture. The
  dashboard's name can be changed under Appearance, also for installed apps.
- VPN in the desktop app on Linux, by a small Rust service: kill switch and lockdown that stay closed if the service
  crashes, split tunneling per program, auto-connect, all ways home.
- Quantum-resistant tunnel with Rosenpass through the VPN itself, on by default, switchable under Network › VPN.
- Windows preview of the desktop VPN, driving the official WireGuard client with its kill switch. Not yet tested on Windows.
- The desktop app as a Flatpak.
- VPN in the phone app: split tunneling per app and connecting automatically.
- Desktop packages for Arch Linux, Debian, Ubuntu, Linux Mint, Fedora and openSUSE.
- Add-on for Slack, Matrix, Rocket.Chat and Mastodon: alerts as cards, send-only and without pictures.
- Add-ons can be removed with their data, which shows how much space that frees.
- The assistant shows its use per day and month, in euros or dollars once you enter prices, and what is left of a budget.
- Docker containers on home servers can be removed.
- The desktop app uses Electron 45, which closes the reported security advisories; `npm audit` is clean.
- Desktop app for Windows, Linux and macOS (`desktop/`, `scripts/build-desktop.sh`): the dashboard in its own window,
  trusting only your pi-guard's certificate authority.
- Connect calendars: besides the phone's calendar, any calendar with an iCal link such as Google, Proton or Nextcloud.
  Repeats and time zones are worked out, and only start and end of busy times are stored.
- Trusted people can have a picture, birthday, address, email and phone number, all optional.
- Each kind of notification can go to chosen ways only, for example only Signal, or Telegram and the app.
- Keep everything for some hours and after that only what is around events (`PI_GUARD_RECORDING_FULL_HOURS`).
- Storage shows what the cameras write per day, how long the space lasts and how to save some; recording quality can
  be set per camera.
- An emergency from the app now comes with a current picture of each camera (up to four). If a picture fails,
  the words still go out.
- Discord messages are embeds with a title, a colour by urgency and the picture inside.
- The Signal page can send a test message, and `pi-guard signal test` really sends one.
- Linking Signal shows steps and the time left, and stores the number from the phone.
- A new login page with a way to show the password.
- Android app PiGuard (`android/`) with the add-on `mobile`: dashboard, alerts without Google, WireGuard
  client, find a phone, SOS, app lock, signed updates, calendar as away times.
- Call a camera from the Live page, also in the browser.
- Modules for Discord, WhatsApp and email that can be switched off, with a Verified badge.
- Email add-on: alerts and data exports, off by default.
- Plugins from a Git link can be checked and updated, also automatically if chosen, following
  `release_branch` from plugin.json or the main branch.
- Settings panels fold; your own add-ons open with the plus beside the search.
- Optional donations under Support.
- A bar shows how well a trusted person is learned and which pictures would help; slightly turned
  faces are accepted now. Viewing direction and brightness are stored with each sample.
- FRITZ!Box user name in the settings.
- Brand icons for messengers and services, and a choice of AI providers with their icons.
- Settings search looks through all tabs and jumps to the setting.
- Login methods and time zone are chosen from lists instead of typed.
- Discord: a bot with commands, buttons, photos, videos and alerts in direct messages.
- Speakers and microphones can be tried out when choosing them.
- Security scan with an optional report by messenger, and a storage analysis with clean-up. System
  checks run in a new root job, `pi-guard-maint`.
- Weekly plan: mark when nobody is usually home, in a calendar you can draw in.
- Wi-Fi QR codes, internet block per device and a new Wi-Fi key through the FRITZ!Box.
- "What others can see of you" on the network page.
- Tooltips, navigation order by drag and drop, camera room as a switch on the Live page.
- One field per contact instead of comma lists, search behind an icon, the map opens large.
- Support contact in the overview, settings, logs and the assistant.
- Signal: `scripts/signal.sh`, linking the phone by QR code, and Note to Self with your own number.
- Several dashboard users with the roles admin and viewer. Viewers only watch; the server enforces
  this with an allowlist. Everyone has their own password and two-factor code.
- Appearance settings: accent colour, font, animations and your own logo, also in the setup.
- Signal in Settings > Messenger and in the setup.
- An icon for the AI assistant.
- `scripts/dashboard-name.sh` gives the dashboard a name through Pi-hole, for hardware keys.
- Guide for hardware keys under Access. A failed attempt now shows the browser's own error.
- Camera room in the tutorial.
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

- VPN on the computer: if the kernel refused the split tunneling rule, it took the kill switch rules down with it,
  because the kernel loads rules all at once. Now split tunneling gives way, the kill switch stays, and the app says
  why. This happened after a kernel update without a restart. The modules the VPN needs now load at boot, so such an
  update no longer gets in the way.
- In the first hour after a restart, unknown senders and emergency contacts got no answer: a limit took the time since
  boot for the time since the last answer.
- In the desktop app, everything that asked a question did nothing: clearing storage, renaming, and every password
  confirmation. It relied on a browser feature the app does not have. A dialog of its own replaces it, which also no
  longer shows passwords in plain text.
- Root jobs no longer write into folders of the unprivileged user: the WireGuard status and the update report could
  have been turned into overwriting any file, by someone who already broke into pi-guard.
- Voice messages to the assistant and talking through the speaker were cut off after the first part of the upload.
- Speech input of the assistant was often nonsense: the language was not given, so short sentences were guessed as
  anything, and silence came out as imagined subtitles. It now names the language, uses a better model with OpenAI, and
  records with noise suppression.
- Changes to notification rules take effect at once, as the page says. They only did after a restart.
- A home server connecting by a local name such as `lytex.system` was refused, because the certificate did not list it.
  Creating a home server now adds the name in use to the certificate.
- Linking Signal now shows success right away. Waiting on a busy signal-cli kept the code on screen, and a busy
  signal-cli was reported as not installed.
- The map of a phone failed with "403 Access blocked": OpenStreetMap refuses browsers without a referrer. Tiles now
  come through the Pi, which also keeps OpenStreetMap from seeing who looks at the map.
- In the More menu a row opens from everywhere, also from the arrow.
- `pi-guard` commands prefer the settings of the service over a `.env` in the current folder, and say so if they
  may not read them. The live picture may go up to 30 frames per second.
- The app's VPN button did nothing when Android refused it, for example with another VPN set to always-on. It now
  says why and leads to the settings. Two phones with the same long name no longer share a VPN name.
- The Signal link code vanished after four seconds and was never renewed, although a code only works for
  two minutes.
- Phones no longer scroll sideways: pages were wider than the screen, the network pages on every phone.
- Borders were missing everywhere, because the line spacing setting overwrote their colour.
- Settings are a list on a phone, tables stack, and tabs wrap, so there is no sideways strip left.
- Icons are part of the page instead of one request per use.
- App: Tabler icons, Material design and an adaptive launcher icon on all native screens.
- Signal no longer crashes in a loop while signal-cli is not running.
- `scripts/signal.sh` installs the native library signal-cli needs on a Raspberry Pi.
- A refused Discord token is shown in the dashboard instead of restarting in a loop.
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

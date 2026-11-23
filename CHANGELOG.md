# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- A valid household code was refused as "not a valid code" when the public address was entered as a URL
  (`https://name`): the code then held `wss://https://…`. The setting keeps only the name now, and a Pi checks each
  code it makes before showing it.
- Messages of password dialogs (wrong password, refused code, saved) were hidden behind the dialog and only showed
  after closing it; they appear above it now.
- Signal with the owner's own number answered its own answers, which came back as Note to Self, in a loop. Copies of
  its own messages are skipped now, and with commands off Signal answers nothing at all, not even a hint.
- *Manage* of a computer in the Server add-on showed only red *unknown command* errors: the agent there was 0.3.0 and
  never updated, because release builds said the crate's version instead of the release's, so each update refused
  itself (*the new version does not run here*). Release builds carry the release version now, and the dashboard says
  that the agent is too old and how to update it, instead of the raw error.
- The support page showed the example lines for the env file next to the real contact; only while nobody is set now.

- *Update now* failed with a server error on freshly installed Pis, and the automatic update with PermissionError:
  `install.sh` made `/var/lib/pi-guard/update` as root before pi-guard ever ran. The update job now makes that folder
  for pi-guard and hands back one made as root; the button says what to do instead of a server error. The update also
  compares the release with the installed commit, so a `git pull` by hand no longer looks up to date.


### Added

- Another VPN (Tailscale, ZeroTier, NetBird) on the Pi is recognised: with pi-guard's WireGuard off, *Network → VPN*
  says so, marks the WireGuard ways, AmneziaWG and post-quantum keys as not needed, and offers to switch AmneziaWG off.
- The phone app follows the dashboard's theme, high contrast included, on its own screens and the system bars.
- Wrong passwords in confirmation dialogs are limited like the login (five a minute per person and address), and
  count towards the ban of an address.
- Alarms in the app only: the phone app and browser push have their own delivery setting, like the messengers, at
  the top of the notification staircase. With messengers on *emergencies only*, they carry just the second line,
  escalations and SOS. Browser push can be switched on there, and SOS and emergency notes reach admins by push too.
- Cloudflare dynamic DNS: a name such as `home.example.org` follows the home's public address (IPv4, optionally the
  Pi's IPv6), checked every five minutes; the VPN and linked households use it unless another address is set. Under
  *Network → VPN*, with *Update now* and a check under *System → Health*.
- *Connect me as admin* for Discord and Signal: a one-time code sent to the bot makes the sender an admin, no ID to
  look up.

### Changed

- *Home* and *Away* on the overview are larger, and the chosen one is filled and ticked.
- The AI assistant answers only in the dashboard, the apps and to the wake phrase, never in a messenger: plain text
  in a chat is no question for it any more, and `/frage` and `/wetter` say where to ask.
- Discord, Signal and WhatsApp no longer need an admin list in the env file when the dashboard is on.

### Fixed

- The second line was skipped on a messenger set to *emergencies only*; it is an emergency now, as are the notes
  about it. An alarm that went only by browser push was not counted as open, so nobody was escalated to.
- Large text squeezed the words of events beside their picture and button; rows wrap now, chart labels are larger,
  the tab bar shortens its labels, and the accessibility text size works together with *large text* (it did nothing).
- Fullscreen of the live camera did nothing on an iPhone and in the phone app; there it fills the window now.
- Without a USB speaker, `pi-guard config init` chose HDMI as the speaker, and pi-guard offered talking and an alarm
  that went nowhere; it now leaves the speaker empty, and listening, recordings with sound and voice control work.

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

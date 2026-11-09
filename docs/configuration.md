# Configuration

pi-guard reads two files:

- **`pi-guard.env`** (`.env` during development): secrets, paths, language and
  the on/off switches. Installed at `/etc/pi-guard/pi-guard.env`. The file is
  found via `--env-file`, `PI_GUARD_ENV_FILE`, `./.env` or the installed path,
  in that order. Real environment variables override the file.
- **`config.yaml`**: structured settings — cameras, zones, trusted devices,
  alert rules, audio devices. Path set by `PI_GUARD_CONFIG_FILE`. Optional; if
  it lists no cameras, every detected camera is used with defaults.

Everything is validated at startup. `pi-guard config check` validates without
starting; `pi-guard config show` prints the effective settings with secrets
masked. Restart the service after changes.

Start from [`.env.example`](../.env.example) and
[`config/config.example.yaml`](../config/config.example.yaml), or generate a
`config.yaml` for the attached hardware with `pi-guard config init -o FILE`.

## Environment settings

All names start with `PI_GUARD_`. Lists are comma-separated.

### General

| Name | Default | |
|---|---|---|
| `LANGUAGE` | `en` | `en` or `de`; default for users who have not chosen one |
| `TIMEZONE` | `Europe/Berlin` | used for messages, quiet hours, schedules |
| `MAP_PROVIDER` | `openstreetmap` | where a phone's place opens: `openstreetmap`, `google`, `apple`, `bing`, `here`, or `geo` for the device's map app |
| `MOBILE_SOS_HOME_AUDIO` | `false` | after an SOS from a phone, 20 seconds from the Pi's microphone for the emergency contacts; needs microphone recording |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | `text` | `json` for one JSON object per line |
| `LOG_FILE` | – | optional rotating log file (journald is used anyway) |
| `DATA_DIR` | `/var/lib/pi-guard` | database, snapshots, models, sounds |
| `RECORDINGS_DIR` | `DATA_DIR/recordings` | |
| `CONFIG_FILE` | `/etc/pi-guard/config.yaml` | |
| `RUNTIME_DIR` | `/run/pi-guard` | tmpfs: latest frames, heartbeat |
| `APP_UPDATES_FROM_GITHUB` | `true` | fetch signed releases of the phone app, desktop app and agent from GitHub for this Pi's devices, see [updates.md](updates.md#releases-on-github) |
| `BACKUP_ENABLED`, `BACKUP_TARGET`, `BACKUP_PASSPHRASE`, … | off | encrypted backups to Nextcloud, rsync or Borg, see [backup.md](backup.md) |
| `SUPPORT_URL` | – | `https://` receiver of the anonymous statistics; sent only after an admin switches them on under Settings → Updates, see [training.md](training.md#anonymous-statistics) |

### Telegram

| Name | |
|---|---|
| `TELEGRAM_ENABLED` | default `true` |
| `TELEGRAM_BOT_TOKEN` | from @BotFather; required when enabled |
| `TELEGRAM_BOT_ID` | optional; derived from the token and checked against it if set |
| `TELEGRAM_ADMIN_USER_IDS` | your personal user IDs (not the bot ID); at least one |
| `TELEGRAM_VIEWER_USER_IDS` | users who may view but not administer |
| `TELEGRAM_ALLOWED_CHAT_IDS` | group chats that may use the bot and receive alerts |
| `TELEGRAM_GROUP_PLAIN_TEXT` | default `false`: in groups only `/commands` and buttons work |

The bot token, bot ID, user IDs and chat IDs are different things: the token
authenticates the bot, the bot ID is its first part, user IDs identify people,
chat IDs identify conversations (in private chats the chat ID equals the user
ID; group IDs are negative).

### Signal

| Name | |
|---|---|
| `SIGNAL_ENABLED` | default `false` |
| `SIGNAL_PHONE_NUMBER` | the account signal-cli runs, `+49…` |
| `SIGNAL_ADMIN_NUMBERS` | admins, international format |
| `SIGNAL_ALLOWED_NUMBERS` | viewers |
| `SIGNAL_CLI_ADDRESS` | default `127.0.0.1:7583` |

### Recording

| Name | Default | |
|---|---|---|
| `RECORDING_ENABLED` | `true` | `false`: nothing is written to disk; `/video` is unavailable |
| `RECORDING_CONTINUOUS` | `true` | `false`: segments without motion are kept only for the buffer time |
| `RECORDING_RETENTION_DAYS` | `7` | per camera override: `retention_days` |
| `RECORDING_FULL_HOURS` | `0` | keep everything this long, then only what is around events; 0 keeps everything until retention |
| `RECORDING_MAX_STORAGE_PERCENT` | `85` | above this, recordings are deleted: oldest without motion first, then oldest with motion, the last few minutes last |
| `RECORDING_MIN_FREE_MB` | `1024` | …or below this much free space |
| `RECORDING_SEGMENT_SECONDS` | `300` | |
| `RECORDING_BUFFER_MINUTES` | `15` | for cameras with `recording: false` and event-only recording |

### Detection, modes, presence

| Name | Default | |
|---|---|---|
| `MOTION_ENABLED` | `true` | |
| `MOTION_SENSITIVITY` | `50` | 0–100; per camera override |
| `DEFAULT_MODE` | `HOME` | mode on first start |
| `PRESENCE_ENABLED` | `false` | |
| `PRESENCE_AUTO_HOME` | `true` | `false`: presence never switches to HOME by itself; a "trusted device is back" message with an *I am at home* button is sent instead |
| `PRESENCE_AUTO_AWAY` | `true` | `false`: presence never switches to AWAY by itself |
| `FRITZBOX_PASSWORD` | – | for the `fritzbox` presence source |
| `PERSON_DETECTION_ENABLED` | `false` | |
| `FACE_RECOGNITION_ENABLED` | `false` | requires person detection |

### Emergency contacts

| Name | Default | |
|---|---|---|
| `EMERGENCY_TELEGRAM_USER_IDS` | – | contacts that must have started the bot once |
| `EMERGENCY_SIGNAL_NUMBERS` | – | |
| `EMERGENCY_AFTER_ALERTS` | `3` | open (unconfirmed) alerts needed… |
| `EMERGENCY_AFTER_MINUTES` | `30` | …the oldest open for at least this long |

An alert (movement, person, unknown person) that was sent stays open until
someone presses *Everything is fine*, sends `ok`, says they are at home
(`/home`, "Ich bin wieder da") or confirms it in the dashboard. Automatic
presence detection does not confirm alerts. When the thresholds are reached,
the contacts get one message with a summary and up to three photos, and the
admins are told; later alerts are added to the same escalation.

### Dashboard, push, network

See [dashboard.md](dashboard.md) and [network.md](network.md): `DASHBOARD_*`,
`WEBPUSH_ENABLED`, `WEBPUSH_CONTACT`, `PIHOLE_ENABLED`, `PIHOLE_URL`,
`PIHOLE_PASSWORD`, `WIREGUARD_ENABLED`, `WIREGUARD_INTERFACE`.
Settings saved in the dashboard go to `DATA_DIR/dashboard.env` and
`DATA_DIR/dashboard.yaml`, which take precedence over the files here.

### Audio, MQTT

| Name | Default | |
|---|---|---|
| `AUDIO_RECORDING_ENABLED` | `false` | microphone |
| `AUDIO_PLAYBACK_ENABLED` | `false` | speaker |
| `AUDIO_MAX_VOLUME` | `80` | hard upper limit for `/volume` |
| `MQTT_ENABLED` | `false` | |
| `MQTT_HOST`, `MQTT_PORT` | –, `1883` | |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | – | |
| `MQTT_TLS` | `false` | verify the broker with the system CA store |
| `MQTT_TOPIC_PREFIX` | `pi-guard` | |
| `MQTT_HOME_ASSISTANT_DISCOVERY` | `false` | |
| `MQTT_COMMANDS_ENABLED` | `false` | accept `PREFIX/command/mode` |

## config.yaml

### cameras

```yaml
cameras:
  - id: living_room          # a-z, 0-9, _ ; used in commands and file names
    name: Living Room        # shown to users
    device: /dev/v4l/by-id/usb-…-video-index0
    enabled: true
    resolution: 1280x720
    fps: 15
    input_format: auto       # auto | mjpeg | yuyv422 | h264
    codec: auto              # auto | copy | h264_v4l2m2m | libx264
    bitrate: 2M
    recording: true          # false: rolling buffer only (for /video)
    retention_days: null
    motion_detection: true
    snapshot_interval_seconds: 1
    microphone: null         # ALSA device recorded into this camera's video
    motion:
      sensitivity: null      # null: global value
      min_area_percent: 0.4  # share of the watched area that must change
      min_duration_seconds: 1.0
      analysis_fps: 4
      analysis_width: 320
      schedule: []           # ["22:00-06:30"]: only detect in these windows
      zones:
        - name: driveway
          kind: include      # include | exclude
          points: [[0.1, 0.5], [0.9, 0.5], [0.9, 1.0], [0.1, 1.0]]
```

- Use `/dev/v4l/by-id/…` paths (`pi-guard cameras` lists them). `/dev/video0`
  can point to a different device after a reboot.
- `input_format: auto` prefers the camera's own H.264, then MJPEG, then raw
  YUYV. `codec: auto` copies camera H.264 or encodes with the Pi's hardware
  encoder (`h264_v4l2m2m`), falling back to `libx264`. A copied stream ignores `bitrate`: such a camera decides itself,
  one wrote 8 Mbit/s, 80 GB a day at 1080p. Lower its resolution, or thin out recordings with `RECORDING_FULL_HOURS`.
  Do not force `h264_v4l2m2m` on such a camera: on a Raspberry Pi 4 the encoder hung the capture until a reboot when it
  was used twice at once, and the first file after a start could not be played.
- Zone points are fractions of the image (0 = left/top, 1 = right/bottom), so
  they keep working when the resolution changes. Without include zones the
  whole image is watched; exclude zones are cut out afterwards.
- A camera's `microphone` is recorded into its video segments. ALSA capture
  devices are exclusive, so that microphone cannot be listened to separately;
  `/listen` then cuts the audio from the recording instead.

### presence

```yaml
presence:
  check_interval_seconds: 30
  away_grace_period_seconds: 600   # nobody seen this long -> AWAY
  home_confirmation_seconds: 30    # seen continuously this long -> HOME
  unknown_mode: AWAY               # mode while presence is UNKNOWN: AWAY | HOME | last
  sources: [neighbours]            # neighbours | fritzbox | pihole
  interface: null                  # e.g. wlan0
  fritzbox: {host: fritz.box, port: 49000, username: ""}
  devices:
    - id: phone
      name: Anna's phone
      mac_address: "AA:BB:CC:DD:EE:FF"
      ip_address: null             # optional; speeds up the first check
      enabled: true
```

See [Presence detection](#how-presence-detection-decides) below.

### notifications

```yaml
notifications:
  quiet_hours: "23:00-06:00"   # low/normal alerts are held back; high/critical still arrive
  max_per_hour: 30             # all non-critical alerts together
  clip_seconds: 10             # length of clips for rules with attach: clip
  rules:
    motion_detected:
      enabled: true
      priority: normal         # low | normal | high | critical
      modes: [AWAY]            # in which effective modes to send
      recipients: all          # all | admins
      providers: [telegram, signal]
      cooldown_seconds: 120    # per event type and camera
      attach: snapshot         # none | snapshot | clip
```

Event types and their defaults:

| Event | Sent | Modes | To | Cooldown | Attach |
|---|---|---|---|---|---|
| `motion_detected` | yes | AWAY | all | 2 min | snapshot |
| `person_detected` | yes | AWAY | all | 2 min | snapshot |
| `unknown_person` | yes | AWAY | all | 2 min | snapshot |
| `known_person` | no | AWAY | admins | 10 min | snapshot |
| `camera_disconnected` | yes | both | admins | 30 min | – |
| `camera_reconnected` | yes | both | admins | – | – |
| `recording_failed` | yes (critical) | both | admins | 1 h | – |
| `storage_low` | yes (critical) | both | admins | 12 h | – |
| `presence_changed` | no | both | admins | – | – |
| `arrival_detected` | yes | AWAY | all | 10 min | – |
| `emergency_escalated` | no (admins are told directly) | both | admins | – | – |
| `mode_changed` | yes | both | all except who changed it | – | – |
| `system_started` | yes | both | admins | – | – |

With person detection enabled, a person walking in produces both a motion and a
person alert (each with its own cooldown). To get only the more specific one,
set `motion_detected: {enabled: false}`.

Users can switch their own notifications off with the **Notifications** button.

### audio

```yaml
audio:
  speaker: plughw:CARD=Device,DEV=0   # `pi-guard audio devices`
  microphone: null                    # a microphone not attached to a camera
  max_clip_seconds: 60
  max_upload_seconds: 60
  max_upload_bytes: 5000000
  quiet_hours: "22:00-07:00"          # no playback
  alert_sound: null                   # doorbell | chime | warning | a saved sound
```

### faces

```yaml
faces:
  match_threshold: 0.40     # cosine similarity for a match
  uncertain_margin: 0.06    # +/- around the threshold reported as "uncertain"
  min_interval_seconds: 5   # per camera
  max_samples_per_person: 40
  compare_body_detectors: false  # also run the old HOG detector, see below
```

## How person detection decides

On motion, up to three pictures two seconds apart are checked while the motion lasts.

- **Bodies:** YOLOX-Nano when `pi-guard models download` has fetched it, otherwise OpenCV's HOG detector. On 120
  motion pictures of a real home, HOG found a person in none, YOLOX in 25.
- **Faces:** YuNet on a 640 px copy of the picture, and again at full resolution on the upper part of each body,
  where a face is often too small for the copy.
- **Who:** a face that matches nobody is a stranger at once only when it is clear: at least 56 pixels, sharp and
  roughly facing the camera. A less clear face that matches nobody must do so in two pictures. A face next to a
  recognised family member that matches nobody is a stranger at once. Everything else is reported as a person.
  `person_detected` is a high alert when away as well, so waiting for a better picture never hides anyone.
- **Corrections:** a face an admin marked as *not X* on the events page makes a later face *uncertain* instead of X
  when it is more similar to that face than to X. See *Learning from the camera* in [dashboard.md](dashboard.md).

With `compare_body_detectors: true`, HOG runs beside YOLOX and the events page shows what each found. It costs about
up to 0.3 s per picture on a Pi 4.

## How presence detection decides

Every `check_interval_seconds` each source reports, per trusted device,
*seen*, *not seen* or *error*:

- **neighbours** sends one empty UDP packet to the device's last known IP
  address, which makes the kernel re-check it with ARP, and then reads the
  neighbour table. Phones answer ARP even when they ignore ping. No other
  addresses are contacted.
- **fritzbox** asks the router (TR-064 `GetSpecificHostEntry`) whether the MAC
  address is an active client. Enable "Allow access for applications" under
  Home Network › Network › Network Settings, create a separate FRITZ!Box user
  for pi-guard and put its password in `PI_GUARD_FRITZBOX_PASSWORD`. Depending
  on the firmware, the user may need the "FRITZ!Box settings" permission.
  `pi-guard presence status` shows whether the query works.

Then:

- HOME when any device has been seen continuously for `home_confirmation_seconds`
- AWAY when no device was seen for `away_grace_period_seconds` *and* all checks
  of the current round succeeded
- UNKNOWN when checks failed for longer than the grace period

A connected phone is evidence of presence, not proof; a missing phone is weak
evidence of absence. Phones with "private Wi-Fi address" keep one random
address per network by default, which is fine; options that rotate the address
regularly break detection.

Manual mode always wins over presence until it expires (`/away 3h`) or
`/mode auto` is sent.

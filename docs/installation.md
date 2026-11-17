# Installation

The native installation is the recommended way to run pi-guard on a Raspberry
Pi. It installs a Python virtual environment under `/opt/pi-guard` and a
systemd service that runs as an unprivileged `pi-guard` user.

## Before you start

1. Flash **Raspberry Pi OS Lite (64-bit)**, Bookworm or newer, and enable SSH.
   Python 3.11 or newer is required.
2. Plug in the USB camera (and audio devices, if you want them).
3. Optional: create a Telegram bot and find your user ID — see
   [telegram.md](telegram.md). You can leave the token empty and set Telegram
   up later in the dashboard assistant.

## Install

```bash
sudo apt install -y git
git clone https://github.com/lytexdev/pi-guard.git
cd pi-guard
sudo ./scripts/install.sh
```

What the installer does, in order:

1. checks the OS (Debian, Raspberry Pi OS, Ubuntu) and architecture
2. installs `ffmpeg`, `v4l-utils`, `alsa-utils`, `iproute2`, Python venv support
   (on 32-bit boards also `python3-opencv`)
3. lists the detected cameras and sound cards
4. creates the `pi-guard` system user (groups `video`, `audio`)
5. installs pi-guard into `/opt/pi-guard/venv` and links `/usr/local/bin/pi-guard`
6. asks for language, time zone, optional bot token and admin user ID, optional
   Signal, optional presence detection, recording location and retention, and
   writes `/etc/pi-guard/pi-guard.env` (mode `0640 root:pi-guard`)
7. turns the dashboard and phone app on by default, prints a one-time first
   login (`admin` / random password) and the address `https://<pi>:8443`
8. writes `/etc/pi-guard/config.yaml` for the detected hardware
   (`pi-guard config init`)
9. installs and starts `pi-guard.service`
10. runs `pi-guard doctor` and waits for the service to report healthy

Running it again is safe: an existing `pi-guard.env`, `config.yaml` and all
recordings are kept. A second run does not invent a new first password when
`pi-guard.db` already exists. `--yes` skips all questions (requires an existing
env file).

## After installation

```bash
pi-guard doctor          # everything green?
pi-guard status
sudo journalctl -u pi-guard -f
```

### First steps (dashboard)

1. On each device, install the certificate from `https://<pi>:8443/ca.crt`
   (see [dashboard.md](dashboard.md#https-and-the-certificate)).
2. Open `https://<pi>:8443` and sign in as **admin** with the password the
   installer printed.
3. Choose your own user name and password (at least 10 characters). The setup
   assistant then walks through cameras, messengers, alerts, emergency
   contacts, trusted people, add-ons and design. Skip any step and reopen it
   under *More → Setup assistant*.
4. Optional: install the [Android](mobile.md) or [desktop](desktop.md) app from
   *Settings → Apps*, or invite household members under *Access*.
5. Open *Help & tutorial* (or F1 in the desktop app) for the guided tour.

If you set a Telegram token, open the bot and press **Start** as well.

To change settings, edit `/etc/pi-guard/pi-guard.env` or
`/etc/pi-guard/config.yaml` and restart:

```bash
sudo nano /etc/pi-guard/config.yaml
sudo pi-guard --env-file /etc/pi-guard/pi-guard.env config check
sudo systemctl restart pi-guard
```

## Storage

Recordings go to `/var/lib/pi-guard/recordings` unless you chose another path.
For long retention, an external USB SSD is more durable than a microSD card:

```bash
# example: SSD mounted at /mnt/recordings (add it to /etc/fstab first)
sudo sed -i 's|^PI_GUARD_RECORDINGS_DIR=.*|PI_GUARD_RECORDINGS_DIR=/mnt/recordings|' /etc/pi-guard/pi-guard.env
sudo ./scripts/install.sh --yes   # creates the directory and the systemd permission drop-in
```

pi-guard deletes recordings when the filesystem is fuller than
`PI_GUARD_RECORDING_MAX_STORAGE_PERCENT` or has less than
`PI_GUARD_RECORDING_MIN_FREE_MB` free, so recordings on the root filesystem
cannot fill it up and there is always room for new ones. The oldest recordings
in which nothing happened go first, then the oldest with motion or a person,
and the last few minutes last.

## Optional features

| Feature | Enable | Also needed |
|---|---|---|
| Presence detection | `PI_GUARD_PRESENCE_ENABLED=true` | `presence.devices` in config.yaml |
| Person detection | `PI_GUARD_PERSON_DETECTION_ENABLED=true` | `pi-guard models download`, without it only a much weaker detector runs |
| Face recognition | `PI_GUARD_FACE_RECOGNITION_ENABLED=true` | `pi-guard models download`; read [privacy.md](privacy.md) |
| Speaker | `PI_GUARD_AUDIO_PLAYBACK_ENABLED=true` | `audio.speaker` in config.yaml |
| Microphone | `PI_GUARD_AUDIO_RECORDING_ENABLED=true` | `audio.microphone` or a camera's `microphone`; read [privacy.md](privacy.md) |
| Signal | `PI_GUARD_SIGNAL_ENABLED=true` | signal-cli, see [signal.md](signal.md) |
| MQTT | `PI_GUARD_MQTT_ENABLED=true` | a broker; `pip install` is already done by the installer |
| Talking to the Pi | `PI_GUARD_VOICE_WAKE_ENABLED=true` or `PI_GUARD_VOICE_BUTTON_GPIO` | `pi-guard voice download`, a microphone and a speaker; see [voice.md](voice.md) |

Models are downloaded only when you run `pi-guard models download`, which shows
the files, their size and source and asks before downloading.

## Update

```bash
cd pi-guard
sudo ./scripts/update.sh
```

This installs the newest signed release, see [Updates](updates.md#updating-the-pi). Do not `git pull` first: the Pi would
then be ahead of the release, and with code nobody signed.

## Uninstall

```bash
sudo ./scripts/uninstall.sh
```

The service and application are removed. You are asked separately whether to
delete the configuration, and recordings are only deleted if you type `DELETE`.

## Manual installation (development)

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env            # fill in token and admin ID
.venv/bin/pi-guard config init -o config.yaml
echo "PI_GUARD_CONFIG_FILE=config.yaml" >> .env
echo "PI_GUARD_DATA_DIR=./data" >> .env
echo "PI_GUARD_RUNTIME_DIR=./data/run" >> .env
.venv/bin/pi-guard run
```

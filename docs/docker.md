# Docker Compose (optional)

The native installation is recommended on a Raspberry Pi. The container image
is useful on machines that already run Docker, or for trying pi-guard out.

## Run

```bash
git clone https://github.com/lytexdev/pi-guard.git && cd pi-guard
cp .env.example .env                        # fill in token and admin ID
mkdir -p config data recordings
cp config/config.example.yaml config/config.yaml   # edit cameras: device paths, names
sudo chown -R 10001:10001 data recordings          # the container runs as uid 10001
```

Point the camera at a stable path:

```bash
ls /dev/v4l/by-id/
echo "CAMERA_DEVICE=/dev/v4l/by-id/usb-…-video-index0" >> .env
```

Inside the container the device keeps the same path, so `device:` in
`config.yaml` uses that path too.

```bash
docker compose up -d --build
docker compose logs -f
docker compose exec pi-guard pi-guard doctor
```

## What the compose file does

- builds a Debian-based Python 3.11 image with FFmpeg (ARM64 and x86_64)
- runs as an unprivileged user, with a read-only root filesystem, all
  capabilities dropped and `no-new-privileges`
- mounts `./config` read-only, `./data` and `./recordings` read-write
- passes only the camera device (and optionally `/dev/snd`) — no privileged
  mode, no Docker socket
- adds the host's `video` and `audio` group IDs (`VIDEO_GID`, `AUDIO_GID` in
  `.env`; find them with `getent group video audio`)
- health check: `pi-guard health` verifies the heartbeat written every 5 s
- restarts automatically unless stopped

Settings in `.env` are read by Compose; the container's own paths
(`/data`, `/recordings`, `/config/config.yaml`) are fixed in `compose.yaml`.

## Limitations

- **Presence detection** reads the neighbour table of the network namespace it
  runs in. On the default bridge network the container only sees its own
  neighbours, so the `neighbours` source does not work. Use the `fritzbox`
  source, or deliberately set `network_mode: host`. pi-guard never switches to
  host networking by itself.
- **Audio**: add `/dev/snd` under `devices`. PulseAudio/PipeWire on the host
  may hold the device; use a dedicated USB sound card.
- **Signal**: signal-cli is not part of the image. Run it on the host (see
  [signal.md](signal.md)) and set `PI_GUARD_SIGNAL_CLI_ADDRESS` to an address
  reachable from the container, or run it in a separate container on a shared
  network. It must be able to read the media files pi-guard sends, so share
  the `data` volume with it.
- **Hardware encoder**: `h264_v4l2m2m` on a Pi needs `/dev/video10`–`12`
  passed into the container as additional devices.
- Hot-plugging a camera that was absent at container start requires a
  restart, because Docker resolves devices when the container is created.

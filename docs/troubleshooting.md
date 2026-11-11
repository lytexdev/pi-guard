# Troubleshooting

Start with:

```bash
pi-guard doctor
pi-guard status
sudo journalctl -u pi-guard -n 100
```

## The service does not start

- `sudo pi-guard --env-file /etc/pi-guard/pi-guard.env config check` shows
  configuration errors with the setting's name.
- "PI_GUARD_TELEGRAM_BOT_TOKEN is missing or malformed": copy the full token
  from @BotFather, without quotes or spaces.
- "the bot's own ID was listed as an admin": the admin list needs *your* user
  ID from @userinfobot, not the number at the start of the token.

## The bot does not answer

- Did you press **Start** in the bot chat? `pi-guard telegram test --send`
  checks whether the bot can write to every admin.
- Is your user ID in `PI_GUARD_TELEGRAM_ADMIN_USER_IDS`? Unknown users get one
  "not authorised" message per hour that shows their ID.
- In a group: is the group's ID in `PI_GUARD_TELEGRAM_ALLOWED_CHAT_IDS`? Plain
  text is ignored in groups; use `/photo` or buttons.
- No internet: pi-guard keeps recording and reconnects by itself. Commands sent
  while offline are dropped when it reconnects (so old `/photo` requests do not
  arrive hours later).

## Camera

- **"device … does not exist"**: `ls /dev/v4l/by-id/`; update `device:` in
  config.yaml. Check the cable and power supply — USB cameras on an
  underpowered Pi disconnect randomly (`dmesg | grep -i usb`, look for
  "under-voltage").
- **"not a capture device"**: you used the metadata node (`video-index1`).
- **Black or very dark picture**: many webcams need a few seconds to adjust
  exposure after starting.
- **Low frame rate**: the camera is delivering raw YUYV. Use a resolution
  available in MJPEG (`pi-guard cameras`) or set `input_format: mjpeg`.
- **High CPU**: `pi-guard doctor` says `libx264`. On a Pi 4 check that
  `/dev/video11` exists (hardware encoder); on a Pi 5 lower resolution/FPS or
  use a camera with H.264.
- "camera not reachable" alerts at night: check `dmesg` for USB resets;
  try another USB port or a powered hub.

## "Video could not be prepared"

- Recording must be enabled (`PI_GUARD_RECORDING_ENABLED=true`).
- The camera must have been running for at least the requested seconds.
- `ls -la /var/lib/pi-guard/recordings/<camera>/` — the newest file should be
  growing. `pi-guard status` shows the time since the last write.

## Too many / too few alerts

- Too many: lower `motion.sensitivity`, raise `min_area_percent` or
  `min_duration_seconds`, add exclusion zones for trees, curtains, TV screens,
  or raise `cooldown_seconds` for `motion_detected`.
- Too few: raise sensitivity; check that the mode is AWAY (`/mode`) and that
  your notifications are on (*Notifications* button). During
  `notifications.quiet_hours` only high-priority alerts arrive.

## Presence detection

`pi-guard presence status` runs one check and prints what each source sees.

- Always "not_seen": check the MAC address. Phones show their per-network
  ("private") address in the Wi-Fi details of the connected network — use that
  one. Disable options that rotate it periodically.
- "error": the `ip` command failed or the FRITZ!Box rejected the login.
- Phone goes to sleep and the mode flips to AWAY: increase
  `away_grace_period_seconds` (e.g. 1200) or use the FRITZ!Box source.

## Faces are never recognized, the picture looks soft

Start with *People → Why recognition works or not*: it shows whether faces are in the pictures at all, how large they
are and how close they come. If they come close but not over the threshold, use *Learn at the camera* on the person's
card. If few moments show a face, the camera sees people from the side or behind, and no training helps there.

- Many cheap USB cameras squeeze their own H.264 hard after the first seconds, and offer no setting for it. A keyframe
  of a few KB at 720p is the sign: `ffprobe -select_streams v -show_entries frame=pict_type,pkt_size <segment>`.
  Recognition reads the same picture, so it loses the detail too.
- Take the picture from the camera uncompressed and encode on the Pi:
  `input_format: yuyv422`, `codec: libx264`, `bitrate: 1M`. On USB 2.0 this often means only 5 FPS at 720p, see
  `v4l2-ctl --list-formats-ext`, and about 11 GB a day. `mjpeg` keeps 30 FPS but gains less, and takes far more CPU,
  because such cameras may ignore the requested frame rate. On one test camera with a Pi 4: H.264 copy 50 % of a core,
  sharpness 22; MJPEG 150 %, 30; YUYV 75 %, 53.
- A camera without a focus control in `v4l2-ctl --list-ctrls` has a fixed lens: software cannot focus it. Some have a
  ring around the lens. Otherwise more light helps most, because it shortens the exposure and so the motion blur.
- Faces too small to recognise are the most common reason. A face needs at least 36 pixels, and 56 for a stranger
  alarm from one picture. People found by a camera across a room are often around 110 of 720 pixels tall, with a face
  of about 15. A camera nearer the door, at head height, helps more than any setting.
- A family member seen clearly but not recognised: *Who is this?* on the event teaches pi-guard that camera's view of
  them, see *Learning from the camera* in [dashboard.md](dashboard.md).
- Without the YOLOX model (`pi-guard models list`), many people are not found at all, so their faces are never
  checked.

## Storage

- Recordings are gone before the retention period is over: the disk reached
  its limit, so pi-guard made room, oldest recordings without motion first.
  This is normal. To keep footage longer, lower the bitrate or use a larger disk.
- "storage almost full": even after deleting recordings the disk is above its
  limit, so something else is filling it. Find out what with the storage page.
- Check with `df -h /var/lib/pi-guard`.

## Audio

- `pi-guard audio devices` lists names; use them exactly in config.yaml.
- "playback failed": is the speaker connected, is the device busy (desktop
  sound server)? `sudo -u pi-guard aplay -D plughw:CARD=…,DEV=0 /usr/share/sounds/alsa/Front_Center.wav`
- The microphone of a camera cannot be listened to separately while the camera
  records with it; `/listen` cuts the audio from the recording in that case.

## Dashboard

- "Your connection is not private": install the CA certificate on that device
  ([dashboard.md](dashboard.md#https-and-the-certificate)). After a new IP
  address the certificate is reissued at the next start.
- "Talk" button greyed out: the page is not on trusted HTTPS.
- 403 on every page: your address was banned (honeypot or too many failed
  logins). Wait `PI_GUARD_DASHBOARD_BAN_MINUTES` or restart pi-guard.
- No push on iPhone: only the installed app (Home Screen) can receive push,
  iOS 16.4 or newer.
- Live picture black: `PI_GUARD_DASHBOARD_LIVE_FPS` is 0, or the camera is
  reconnecting (see Overview).

## Home server does not connect

- *invalid peer certificate: certificate not valid for name*: the agent checks that the certificate lists the name it
  uses. Create the home server again from the dashboard opened by that name, or add it with
  `PI_GUARD_DASHBOARD_TLS_NAMES=name` in `/etc/pi-guard/pi-guard.env` and restart pi-guard. The agent retries by itself.

## Storage fills up quickly

Settings › Storage shows what your cameras write per day and how long the space lasts. A camera at 2 Mbit/s writes about
20 GB a day, but cameras sending H.264 ignore that setting and can write four times as much. Lower their resolution under
Devices, or keep everything for 24 hours and after that only what is around events. Several cameras are best recorded
to a USB SSD.

## VPN in the app

- *Android did not allow the connection*: another VPN app is set to always-on. Turn that off under the phone's VPN
  settings. A dialog in the app leads there.
- *This phone has no access yet*: open the app once at home, the access is created then.
- *Your home does not answer*: check the internet at both ends and the port forwarding for UDP 51820. Under
  Network › VPN, *Check now* shows if your line has no public address of its own; then use another way. If the access
  was removed in the dashboard, disconnect and open the app once at home to get a new one.
- *The Pi does not answer* on a computer: its access is usually gone from Network › VPN. WireGuard gets no reply then,
  not even a refusal, and the kill switch keeps blocking the internet. Disconnect and connect again on the VPN page at
  home: that gives the computer a new access. The Pi stays reachable while *Local devices stay reachable* is on.
- *No internet on a computer after a restart, Wi-Fi is connected*: see [Kill switch and lockdown](vpn.md#kill-switch-and-lockdown).
- *VPN clients have no internet after enabling privacy upstream*: the Pi-side kill switch may be strict without an
  exit. Deactivate under Network › VPN, or `sudo ./scripts/privacy-server.sh unlock`. Management from the LAN should
  still work.
- *The home network or Pi-hole is gone on a computer, the internet works*: an older tunnel may claim the same addresses.
  Run `ip route get` with your router's address; if it names something like `wg0` instead of your Wi-Fi or cable
  interface, that tunnel is the cause. Installing `wireguard-tools` starts `wg-quick@` services that were enabled long ago, and
  `install-vpn.sh` warns about them. Turn one off with `sudo systemctl disable --now wg-quick@wg0`.

## Signal

- `pi-guard signal test` must report a version.
- `sudo systemctl status pi-guard-signal`; `journalctl -u pi-guard-signal`.
- After a long time offline, update signal-cli; outdated clients are rejected.
- Signal says the code is invalid: it was older than two minutes, or scanned with the camera app. Scan the
  current code inside Signal under *Linked devices*.

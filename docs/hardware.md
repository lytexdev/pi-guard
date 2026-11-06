# Hardware

## Boards

| Board | Status | Notes |
|---|---|---|
| Raspberry Pi 4, 2 GB, Pi OS Lite 64-bit | primary target | one or two 720p cameras with the hardware encoder |
| Raspberry Pi 5 | expected to work | **no hardware H.264 encoder**; pi-guard uses libx264 — prefer cameras with built-in H.264 or keep 720p/10–15 FPS |
| Raspberry Pi 3 | expected to work | 1 GB RAM: one camera, 720p or less |
| Pi OS 32-bit (armv7) | expected to work | uses the distribution's `python3-opencv` |
| Debian/Ubuntu ARM64, x86_64 | works | software encoding unless the camera provides H.264 |

"Expected to work" means the code paths are hardware-independent but have not
been tested on that board by the maintainers. Reports are welcome.

### Tested so far

| Hardware | Result |
|---|---|
| Logitech C270 (USB, MJPEG/YUYV, 1280×720) on x86_64, FFmpeg 9 | capture, segments, snapshots, clips, motion pipeline and microphone capture work; libx264 is used |
| C270 in Docker (read-only, unprivileged) | works |
| Generic "HD Web Camera" (USB, H.264/MJPEG/YUYV, fixed focus) on a Raspberry Pi 4 | capture, segments and motion work. Its own H.264 is too soft for face recognition, so `yuyv422` with `libx264` is better, at 5 FPS in 720p, see [troubleshooting](troubleshooting.md) |

Not yet verified on real hardware: Raspberry Pi 4/5 (including the
`h264_v4l2m2m` hardware encoder), CSI cameras, cameras with built-in H.264,
speaker playback, person/face recognition with real people, FRITZ!Box presence
queries, Signal with a real account. See the checklist below.

## Cameras

pi-guard works with **USB Video Class (UVC)** cameras through V4L2. Practically
every USB webcam qualifies.

Formats, best first:

1. **H.264 from the camera** — copied into the recording without re-encoding;
   lowest CPU use. Some Logitech and many "security" USB cameras offer it.
2. **MJPEG** — decoded and re-encoded to H.264. On a Pi 4 the hardware encoder
   handles 1080p15; decoding MJPEG costs roughly a quarter of one core.
3. **YUYV (raw)** — limited by USB 2.0 bandwidth: often only 5–10 FPS at 720p
   and 1–5 FPS at 1080p.

`pi-guard cameras` lists what each camera offers. `pi-guard doctor` shows what
pi-guard will use.

Stable paths: always use `/dev/v4l/by-id/…`. Cameras with two V4L2 nodes
(`video-index0`, `video-index1`) use `index0` for video; the other is metadata.
Two identical cameras without serial numbers get the same by-id name; use
`/dev/v4l/by-path/…` (tied to the USB port) for those.

### CSI cameras (Raspberry Pi Camera Module)

CSI cameras are not V4L2 capture devices on current Raspberry Pi OS (they use
libcamera). They are not supported directly. A workaround is to expose the
camera as a V4L2 loopback device or as a UVC gadget; this is outside pi-guard's
scope for now.

## Audio

Any ALSA device works: USB microphones, USB sound cards, webcams with built-in
microphones, HDMI audio, the Pi's headphone jack. `pi-guard audio devices`
lists them with stable names like `plughw:CARD=Device,DEV=0`.

A microphone can either belong to a camera (recorded into its video) or be a
standalone `audio.microphone` used only for `/listen`. ALSA capture devices
can only be opened by one process.

## Storage

Rough storage use per camera at the default 2 Mbit/s:

| Retention | Space |
|---|---|
| 1 day | ~21 GB |
| 7 days | ~150 GB |

Lower `bitrate` (e.g. `1M` for 720p) halves this; cameras sending H.264 ignore it and need a lower resolution instead. Continuous writing wears out
ordinary SD cards; use a **High Endurance** card or a USB SSD.

## Hardware test checklist

Before relying on an installation:

- [ ] `pi-guard doctor` shows no FAIL
- [ ] the encoder line says `h264_v4l2m2m` (Pi 4) or `copy`
- [ ] `/photo` and `/video 30` work; the video is smooth
- [ ] walking through the picture while AWAY sends an alert with a photo
- [ ] unplugging the camera sends "camera not reachable" within ~1 minute;
      replugging sends "working again" and recording resumes
- [ ] `sudo systemctl restart pi-guard` and a power cut both leave playable
      recordings
- [ ] after a day: `pi-guard status`, CPU temperature and load are fine
- [ ] presence: leave with your phone, mode turns AWAY after the grace period
- [ ] audio (if used): `/sound doorbell`, `/listen 10`

# Performance

## Design decisions

- **One FFmpeg process per camera.** It reads the device once and produces the
  recording, the motion-detection frames and the snapshot image. Nothing else
  opens the camera, so photos and clips never interrupt recording.
- **Encoding stays in FFmpeg.** Camera H.264 is copied; otherwise the Pi's
  hardware encoder is used. Python never touches full-resolution video.
- **Motion detection on tiny frames**: 320 px wide, grayscale, 4 FPS.
- **Person detection and recognition only after motion**, on one snapshot,
  in a worker thread, at most once per `faces.min_interval_seconds` per camera.
- **Clips are cut, not encoded**: `/video` copies keyframe-aligned parts of the
  existing segments.
- **Bounded queues everywhere**: event subscribers, notifications and MQTT
  drop the oldest entries when they fall behind, so memory cannot grow without
  limit while Telegram or the broker is unreachable.

## Dashboard speed

The desktop and phone apps show the dashboard from the Pi, so all of this reaches them without an app update.

- **Something is on screen at once.** A page shows a placeholder until its data is there. It stays hidden for the first
  150 ms, so fast answers do not flash.
- **Few round trips, not few bytes.** Over a VPN each request in a chain costs a full round trip. The session is asked
  for once at the start. Changing pages only waits for it where the answer decides what to show (login, setup);
  otherwise it is checked alongside. Pages start their requests together, and the overview draws without waiting for
  the topology and the connection panel. `index.html` names the first scripts with `modulepreload`, so the browser does
  not have to find them import by import.
- **Compressed without work on the Pi.** `setup.py` adds a `.gz` next to each script, style sheet and SVG when the
  package is built, and aiohttp sends it by itself: all of them together go from 385 KB to 119 KB. A run from the source
  folder has none and sends the plain files. The language texts come from the API and are compressed per request
  (125 KB to 38 KB). Other API answers stay plain: they are small, and `/api/session` holds the CSRF token, which must
  not share a compressed answer with anything an attacker can influence (BREACH). There is no Brotli, because it would
  add a build dependency on every Pi for a few KB more.
- **Slow requests show in the log.** Any API answer that takes 500 ms or more is logged at level `INFO` as `slow request:`
  with its route pattern, under Logs in the dashboard or with `journalctl -u pi-guard | grep "slow request"`. Assistant
  answers are slow by nature. Live streams, WebSockets and file downloads are not counted.

## Measurements

Measured on an x86_64 desktop (16 cores) with a Logitech C270 at 1280×720,
10 FPS, MJPEG input, **libx264** software encoding. A Raspberry Pi 4 has not
been measured yet; numbers there will differ (hardware encoder: much lower
encoder CPU and memory; slower CPU: higher Python and decode cost).

| | Value |
|---|---|
| Motion detection, 320×180 frame | 0.075 ms per frame (0.03 % of a core at 4 FPS) |
| Motion detection, 640×360 frame | 0.23 ms per frame |
| pi-guard Python process | ~90 MB RSS, idle CPU after start-up |
| FFmpeg capture process (libx264) | 155–245 MB RSS, 30–50 % of one core |
| HOG person detector, 640 px image | ~20 ms |
| YuNet face detection, 640 px image | ~15 ms |

On a Raspberry Pi 4 (measured 2026-11-08 on 1280×720 motion pictures, while recording):

| | Value |
|---|---|
| HOG person detector, 640 px image | 313 ms (one thread) |
| YOLOX-Nano person detector, 416 px | 298 ms (one thread), 187 ms (two) |
| YuNet face detection, 640 px / 1280 px | 120 ms / 425 ms |
| SFace face embedding | 276 ms per face |
| Whole person check, before / with YOLOX, head crops and HOG beside it | median 177 ms / 375 ms |
| Live view: parsing the MJPEG stream (60 KB frames) | 0.018 ms per frame (0.01 % of a core at 8 FPS) |
| Image tools on a 1280×720 picture | sharpen 2 ms, zoom 3 ms, brighten 24 ms |

Reproduce the motion benchmark with `python scripts/bench_motion.py [width]`.

The live view is an additional MJPEG output of the capture process. Encoding
it costs FFmpeg CPU time while the dashboard is enabled, whether or not anyone
watches; lower `PI_GUARD_DASHBOARD_LIVE_FPS` or set it to `0` on a busy Pi.

### Rust?

Motion detection was the obvious candidate for a native implementation. At
0.075 ms per frame it uses a negligible share of a core; even if a Pi 4 is
ten times slower, it stays below 1 % CPU. The expensive work (decoding,
encoding, scaling) already happens in FFmpeg's C code. A Rust extension would
add a cross-compilation step for ARM without a measurable benefit, so there is
none.

The same check for the dashboard: splitting the live stream into frames uses
FFmpeg's Content-Length headers, so Python reads each frame exactly instead of
scanning for markers (0.018 ms per frame); fan-out to viewers is network I/O.
Neither benefits measurably from Rust.

## Limits on a Raspberry Pi 4 (2 GB)

Expected, to be confirmed on hardware:

- **1–2 cameras at 720p15** with MJPEG input and the hardware encoder
- **1 camera at 1080p15**; more cameras at 1080p need H.264 from the camera
- libx264 fallback: one camera at 720p10 at most
- Face recognition adds ~40 MB for the model and a few hundred milliseconds
  per check
- The systemd unit limits pi-guard (including FFmpeg) to 1.2 GB

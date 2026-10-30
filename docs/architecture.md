# Architecture

pi-guard is one Python process (asyncio) plus one FFmpeg process per camera.

```
                       ┌────────────── FFmpeg (per camera) ──────────────┐
  /dev/v4l/…  ───────► │ decode ─ split ─┬─ H.264 ─► segments/*.mp4      │
  (ALSA mic)           │                 ├─ 320px gray ─► stdout ─────────┼──► MotionDetector
                       │                 └─ 1 fps JPEG ─► /run/pi-guard   │        │
                       └──────────────────────────────────────────────────┘        ▼
                                                                         MotionAnalysis
                                                                    (snapshot, persons, faces)
                                                                                 │
   PresenceMonitor ─► ModeController ──────────────┐                             │
                                                    ▼                             ▼
                                               ┌─────────────── EventBus ───────────────┐
                                               └──┬──────────┬──────────┬──────────┬────┘
                                                  ▼          ▼          ▼          ▼
                                             event log   Notifier   MqttBridge  alert sound
                                              (SQLite)      │
                                                            ▼
     Telegram / Signal ◄──────────────────────────── Messenger ◄── CommandRegistry
```

## Components

| Package | Responsibility |
|---|---|
| `config` | `.env` settings (pydantic-settings) and `config.yaml` models; validation |
| `core` | events and bus, HOME/AWAY controller, roles and invitations, supervision, time helpers |
| `cameras` | V4L2 discovery, FFmpeg command construction, the capture pipeline and its watchdog |
| `recording` | segment index and clip cutting, retention and disk-pressure handling |
| `detection` | motion, zones, person detection, face recognition, model downloads |
| `presence` | neighbour-table and FRITZ!Box sources, the HOME/AWAY/UNKNOWN resolver |
| `audio` | ALSA discovery, playback, bounded capture, built-in sounds |
| `messaging` | provider-neutral replies, the command registry and handlers, Telegram and Signal |
| `notifications` | alert rules and delivery with retries |
| `integrations/mqtt` | optional MQTT bridge |
| `storage` | SQLite state |
| `security` | file permissions, redaction, rate limiting |
| `diagnostics` | `pi-guard doctor`, host metrics |
| `i18n` | English and German texts |

## Principles

- **Recording does not depend on anything else.** Each component runs as a
  separately supervised task (`core/lifecycle.py`); a crash restarts that
  component with backoff. Messaging, MQTT and presence can fail permanently
  without affecting capture.
- **Events decouple producers from consumers.** Publishers never block; each
  consumer has its own bounded queue and subscribes when it is constructed.
- **One way in for chat.** Every provider hands incoming text to the
  `Messenger`, which authorises, rate-limits, parses through the single
  `Registry` and renders a provider-neutral `Reply`. Aliases, translations and
  argument parsing therefore behave identically on Telegram and Signal.
- **The filesystem is the recording index.** Segment names are their UTC start
  time; retention and clip extraction read the directory. There is no database
  of recordings that could disagree with the disk.
- **State that must survive restarts lives in SQLite** (mode, users, events,
  invitations, audit log). The CLI writes to the same database; the service
  picks up mode changes within five seconds.

## Mode selection

```
                 /home, /away (optionally timed)
   AUTO ───────────────────────────────────────► MANUAL_OVERRIDE
    ▲                                                   │
    └──────────── /mode auto, or timer expires ─────────┘

   effective mode = manual mode                      if MANUAL_OVERRIDE
                  = HOME/AWAY from presence          if AUTO and presence known
                  = presence.unknown_mode            if AUTO and presence UNKNOWN
```

Without presence detection there is no AUTO; a timed override returns to the
mode that was active before it.

## Testing

`pytest` runs without cameras, microphones or network services: hardware is
replaced by synthetic FFmpeg sources (`lavfi`), fake providers and a fake
signal-cli server. Tests that need FFmpeg are marked `ffmpeg` and skipped if it
is missing; tests that need real devices are marked `hardware` and excluded by
default (`pytest -m hardware`).

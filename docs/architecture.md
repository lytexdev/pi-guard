# Architecture

pi-guard is one Python process (asyncio) plus one FFmpeg process per camera. Around it:

| Folder | What it is |
|---|---|
| `vpn/` | VPN service in Rust: tunnel, kill switch and split tunneling on computers, the Pi's side of the key exchange |
| `vpn/kem/` | the post-quantum key exchange, one implementation for the Pi, computers and Android |
| `src/pi_guard/privacy/` | optional Pi-side privacy upstream: multi-hop exit, Mullvad/custom providers, DNS, Tor, profiles |
| `deploy/privacy/` | root jobs for the upstream WireGuard exit, nftables plan and Tor control |
| `agent/` | small Rust program on connected computers |
| `proxy/` | `pgproxy`, the optional reverse proxy in Rust, see [proxy.md](proxy.md) |
| `desktop/` | Electron app: shows the dashboard, drives the VPN service |
| `android/` | phone app: shows the dashboard, has its own WireGuard client |
| `training/` | `pgtrain` for a computer with a GPU: faces, thresholds, training the person detector, receiving statistics, see [training.md](training.md) |

Settings changed in the dashboard live in `DATA_DIR/dashboard.env` and `DATA_DIR/dashboard.yaml`, and win over
`.env` and `config.yaml`. Every change of `dashboard.env` is kept in the `settings_history` table, without secret
values, so it can be undone.

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
| `audio` | ALSA discovery, playback, bounded capture, built-in sounds; `voice.py` talks to the Pi offline (Vosk) |
| `messaging` | provider-neutral replies, the command registry and handlers, Telegram and Signal |
| `notifications` | alert rules and delivery with retries |
| `integrations/mqtt` | optional MQTT bridge |
| `addons` | optional parts: server agents, analytics, smart home, remote commands, AI assistant and commander, settings help, phones (SOS, lost phones), emergency data, reverse proxy, plugins |
| `backup.py` | encrypted backups to Nextcloud, rsync or Borg |
| `storage` | SQLite state |
| `security` | file permissions, redaction, rate limiting |
| `diagnostics` | `pi-guard doctor`, host metrics, the optional anonymous statistics |
| `web` | optional dashboard: aiohttp server, auth, security middleware, JSON API, static single-page app, Web Push |
| `network` | Pi-hole client, LAN inventory, WireGuard status |
| `privacy` | optional upstream multi-hop, providers, DNS and kill-switch plans for root jobs |
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

## Why SQLite, and no job queue

pi-guard is one process on one small computer, with few writes. Measured on a Raspberry Pi with
an SD card: about 250 durable single inserts per second, and 0.2 ms to read the latest 50 of
500,000 events. A real installation had about 3,000 events in under 1 MB after several days.

- **PostgreSQL or MySQL** would add a server that uses memory and writes to the SD card, plus
  passwords and backups to look after, with no gain at this size. They pay off when several
  machines share one database. Full-text search also relies on SQLite FTS5.
- **Celery and Redis** solve work spread over many processes or machines. Here asyncio tasks
  already run the background work; a broker would add two services and another way to fail.

## Where Rust is used

Rust is for small programs that run on their own: the agent on connected computers (`agent/`) has no
runtime to install and handles untrusted network input. The same goes for the VPN service (`vpn/`), which
runs as root, and its key exchange (`vpn/kem/`), which also gives phones the very same code. The reverse
proxy (`proxy/`, `pgproxy`) forwards every byte of its connections and faces the internet; pi-guard only writes its
configuration and starts it.

Pi-guard's own work is not limited by Python. Measured on a Raspberry Pi with one camera: FFmpeg, written
in C, used about 130 % of a core, the Python service 3 to 8 %. Motion analysis and face models already run in
native code through OpenCV. Rewriting the service would change little and would need a Rust toolchain or
prebuilt programs on every Pi. A new service is worth writing in Rust if it is small and stands alone, or if
it processes every frame or packet itself.

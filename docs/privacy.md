# Privacy

pi-guard runs entirely on your device. This page lists what it stores, what
leaves the device, and what to consider before you point a camera at people.
It is not legal advice.

## What is stored on the device

| Data | Where | How long |
|---|---|---|
| Video recordings (and camera audio, if enabled) | `RECORDINGS_DIR/<camera>/` | `RECORDING_RETENTION_DAYS`, earlier if the disk fills up |
| Event snapshots | `DATA_DIR/media/snapshots` | retention days |
| Clips and audio prepared for sending | `DATA_DIR/media/clips`, `…/audio` | 1 day |
| Latest frame per camera | `RUNTIME_DIR` (RAM) | overwritten every second |
| Events, mode, users, invitations, audit log | `DATA_DIR/pi-guard.db` | events and audit: 30 days or the retention period, whichever is longer |
| Face embeddings of enrolled people | `DATA_DIR/biometrics.db` | until deleted |
| Saved sounds | `DATA_DIR/sounds` | until deleted |

Files are owned by the `pi-guard` user and not readable by others (the service
runs with `UMask=0077`; databases are `0600`).

## What leaves the device

| Data | Goes to | When |
|---|---|---|
| Text messages, photos, video clips, audio clips | **Telegram** servers | when a user requests them or an alert rule fires |
| The same | **Signal** (end-to-end encrypted) | only if Signal is enabled |
| Mode, presence state, camera status, motion events (no images, no names) | your **MQTT broker** | only if MQTT is enabled |
| A presence query per trusted device | your **router** (FRITZ!Box source) | only if configured |
| Alert summary and up to three photos | **emergency contacts** via Telegram/Signal | only if alerts stay unconfirmed and contacts are configured |
| Alert text (no photos, no names) | **browser push services** (Google, Apple, Mozilla), end-to-end encrypted | only with `PI_GUARD_WEBPUSH_ENABLED` and devices that subscribed |
| Live picture and sound, files you upload | your own browser, over HTTPS | only while you use the dashboard |
| Your browser's public IP address | `api.ipify.org`, and the Pi asks `ipwho.is` about unknown addresses | when the overview is open (connection panel) |
| Questions to the AI assistant, voice messages sent to it | the provider you chose (OpenAI, Mistral, your own) | add-on on, only what you ask |
| Excerpts from your files, Paperless-ngx and Nextcloud | the AI provider | only if you allow it; never with Ollama |
| Names of devices and cameras | the AI provider | only if "AI may plan actions" is ticked |
| A camera picture | the AI provider | only if allowed, and after you confirm each time |
| Town for the weather | Open-Meteo | add-on on, when you ask for the weather |
| Text to translate | DeepL | add-on on and a DeepL key set |
| Messages and pictures | Meta, if you use WhatsApp | WhatsApp set up |
| Model files (download) | from GitHub | only when you run `pi-guard models download` |

Telegram bot chats are **not end-to-end encrypted**: photos and videos sent by
the bot are stored on Telegram's servers like any cloud chat. Signal messages
are end-to-end encrypted. Nothing else is sent anywhere: no telemetry, no
update checks, no cloud account.

Never sent: face embeddings, recordings in bulk, the database, secrets.

## Defaults

- Microphone recording: **off**
- Person detection and face recognition: **off**
- Presence detection: **off**; only explicitly enrolled devices are checked
- MQTT inbound commands: **off**

## Audio

Audio is far more sensitive than video. In Germany, recording the non-public
spoken word of others without consent is a criminal offence (§ 201 StGB),
independent of data protection law. pi-guard therefore:

- records audio only when `PI_GUARD_AUDIO_RECORDING_ENABLED=true`
- has no continuous remote listening; `/listen` records a bounded clip
  (at most `audio.max_clip_seconds`) and is restricted to admins
- plays a chime on the speaker (if one is configured) before a live recording
- writes every `/listen` to the audit log

Live listening in the dashboard follows the same rules: it needs
`PI_GUARD_AUDIO_RECORDING_ENABLED`, plays the chime on site, ends after ten
minutes and is audited. Talking through the speaker is audited too.

If a camera has a `microphone`, its audio is part of the continuous recording.
Only do this where everyone who can be recorded knows and agrees.

## Face recognition

Face embeddings are biometric data, a special category of personal data under
Art. 9 GDPR. pi-guard:

- only enrolls people an admin enrolls explicitly, after confirming consent
- deletes enrollment photos immediately and stores only embeddings
- takes live pictures in the browser or app only when you press the buttons, without sound; they reach the Pi
  on upload and are treated like any other enrollment photo
  (128 numbers per sample, plus the viewing direction and brightness of the picture) in a separate database readable only by the service
- uses SQLite `secure_delete` and `VACUUM` when a person is deleted
- never sends embeddings anywhere, and never sends names to MQTT
- reports *known*, *uncertain* or *unknown*, never a certainty, and never
  suppresses a motion alert because a known person was recognised

- keeps face data collected during an enrollment session in memory only, until
  the admin confirms consent; cancelled or expired sessions leave nothing behind

`/sharper` finds *where* a person is in the picture to frame it (body or face
detection); it does not identify anyone and works without face recognition.

Sentences written to the bot are matched against keywords on the device; no
text is sent to any language service.

Delete a person with `/faces delete NAME` or `pi-guard faces delete NAME`.
To remove all biometric data, stop the service and delete `biometrics.db`.

## Network data

The dashboard's network page shows what the Pi already knows (its neighbour
table) and, if connected, Pi-hole's client list. Pi-hole itself records every
DNS query of every device in the home; that is a separate product with its
own privacy settings (query logging, anonymisation) which you should review.
pi-guard does not scan the network.

Evidence videos are kept for 90 days, longer than normal footage. They record sound only if
the microphone is switched on, and the microphone is announced with a chime. The checksum
shows whether a file was changed later; it does not replace legal advice on using footage.

## Legal notes for Germany

This is an overview, not legal advice. If you are unsure, ask a lawyer or your
state's data protection authority.

- **Household exemption.** The GDPR does not apply to purely personal or
  household activity (Art. 2(2)(c)). The European Court of Justice decided
  that this exemption ends where a camera also covers public space, such as a
  pavement, street or a neighbour's property (C-212/13, *Ryneš*). Point
  cameras at your own private area only; use exclusion zones as an
  additional safeguard, not as a substitute for camera placement.
- **Other people in the home.** Visitors, family members and especially
  employed carers, cleaners or nurses must be informed. Recording people at
  their workplace is subject to strict limits; hidden recording of employees is
  generally not allowed. A visible sign at the entrance is good practice.
- **Shared buildings.** Cameras in shared hallways, stairwells or gardens need
  the agreement of the other residents or the owners' association.
- **Audio.** See above: § 201 StGB.
- **Biometrics.** Recognising people requires their explicit consent; keep a
  record of it.
- **Retention.** Keep recordings only as long as needed. The default of 7 days
  is a starting point; shorter is better where possible.

## Data subject requests

If someone asks what pi-guard stored about them: recordings and snapshots are
organised by time in `RECORDINGS_DIR` and `DATA_DIR/media`; enrolled people are
listed with `pi-guard faces list`; users with `/users`.

## Phones with the app

The location of a phone is stored in `pi-guard.db` only if its owner switched on *Share location*, and it is
replaced by each new report. Admins can see it, so ask before switching it on for someone else. An SOS sends the
location to the emergency contacts, also through their messengers. It also sends a current picture of each camera (up
to four) to them, so whoever is an emergency contact can see inside the home in that moment. Map tiles are fetched by
the Pi when someone opens a map, which tells OpenStreetMap roughly where the phone is, but not who is looking.

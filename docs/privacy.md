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

If a camera has a `microphone`, its audio is part of the continuous recording.
Only do this where everyone who can be recorded knows and agrees.

## Face recognition

Face embeddings are biometric data, a special category of personal data under
Art. 9 GDPR. pi-guard:

- only enrolls people an admin enrolls explicitly, after confirming consent
- deletes enrollment photos immediately and stores only embeddings
  (128 numbers per sample) in a separate database readable only by the service
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

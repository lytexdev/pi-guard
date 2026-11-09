# Telegram

Telegram is pi-guard's main interface. The bot uses long polling: it connects
out to Telegram, so no port has to be opened and no public address is needed.

## Create the bot

1. In Telegram, open **@BotFather** and send `/newbot`.
2. Choose a display name (e.g. "Haus Müller") and a username ending in `bot`.
3. BotFather replies with a **token** like `123456789:AAH…`. This is a password:
   whoever has it controls the bot. Put it only into `pi-guard.env`.
4. Optional: `/setprivacy` → *Enable* keeps the bot from reading ordinary group
   messages; `/setjoingroups` → *Disable* prevents strangers from adding it to
   groups.

## Find your user ID

Open **@userinfobot** (or any "user ID" bot) and press Start. It replies with a
number such as `987654321`. Put it into `PI_GUARD_TELEGRAM_ADMIN_USER_IDS`.

This is *your* ID. The bot's own ID (the part of the token before the colon) is
derived automatically; pi-guard refuses to start if you list the bot's ID as an
admin.

Then open your bot and press **Start** once — Telegram bots cannot write to
users who have never started them. `pi-guard telegram test --send` checks this.

## Users and roles

| Role | Can |
|---|---|
| **viewer** | photo, video, status, cameras, events, own notifications, own language and view, set HOME/AWAY |
| **admin** | everything, plus users and invitations, presence, sounds, microphone, face enrollment |

Users come from two places:

- `PI_GUARD_TELEGRAM_ADMIN_USER_IDS` and `…_VIEWER_USER_IDS` in the env file.
  These cannot be removed through chat.
- **Invitations.** An admin taps *More functions → Invite someone* (or sends
  `/invite`, `/invite admin`). The bot answers with a link
  `https://t.me/<bot>?start=<code>` and the code itself. The code works once
  and expires after 24 hours. Whoever opens the link and presses Start joins.

Everyone else gets one short "not authorised" reply per hour, showing their
user ID so they can pass it to an admin, and is otherwise ignored.

## Group chats

A family group can receive alerts and use the bot:

1. Add the bot to the group.
2. Find the group's chat ID (negative number, e.g. via `@RawDataBot`, or from
   the pi-guard log line that rejects the group).
3. Add it to `PI_GUARD_TELEGRAM_ALLOWED_CHAT_IDS` and restart.

Only users who are themselves authorised can use the bot in the group. To
avoid accidental triggers, only `/commands` and buttons work in groups unless
`PI_GUARD_TELEGRAM_GROUP_PLAIN_TEXT=true`.

## Views

- **Simple** (default): five large buttons, short sentences, every answer has
  *Back to menu*. Status shows whether the camera works, whether recording is
  on and when movement was last seen.
- **Advanced**: status adds storage, load, temperature, presence and failing
  components; help lists all commands.

Each user switches with *Settings* or `/layout simple|advanced`. Admins always
see the *More functions* button.

## Commands and aliases

Every command works with or without `/`, in any letter case, and with the
listed aliases. `video 30`, `/v 30` and `Video 30` do the same.

| Command | Aliases | Arguments | |
|---|---|---|---|
| `/menu` | start, m, menü, menue, hauptmenü | | main menu |
| `/photo` | p, foto, bild, snapshot, fotos, bilder | [all/alle] [camera] | current photo; `alle` = one per camera |
| `/sharper` | schärfer, schaerfer, scharf, deutlicher | [camera] | sharpest of several pictures, enlarged around a person |
| `/zoom` | vergrößern, näher, zoomen | [area] | enlarge part of the last photo: `links`, `oben rechts`, `mitte`, `1`–`9` |
| `/brighter` | heller, aufhellen, hell | | brighten the last photo |
| `/video` | v, clip, film | [camera] [3–120 s, default 15] | the last seconds |
| `/status` | s, zustand, kamerastatus | | |
| `/cameras` | kameras | | |
| `/camera` | kamera, cam | [camera] | details |
| `/events` | ereignisse, e, verlauf | [count] | |
| `/notifications` | benachrichtigungen, alerts, meldungen | [on/an/ein, off/aus] | own alerts |
| `/settings` | einstellungen | | language, view |
| `/language` | sprache, lang | de / en | |
| `/layout` | ansicht | simple/einfach, advanced/erweitert | |
| `/help` | h, hilfe, ? | | |
| `/mode` | modus | [home/zuhause, away/abwesend, auto] [duration] | |
| `/home` | zuhause, daheim, da | [duration] | |
| `/away` | abwesend, weg, unterwegs | [duration] | |
| `/presence` | anwesenheit | | admin |
| `/invite` | einladen | [viewer, admin] | admin |
| `/users` | benutzer | | admin |
| `/faces` | gesichter, personen, vertraute | [delete NAME] | admin: trusted people |
| `/enroll` | eintragen, anlernen, hinzufügen | [NAME] | admin: enroll a trusted person |
| `/alarm` | sirene, alarmton | [on/an, off/aus] | admin: sound or stop the alarm |
| `/listen` | audio, anhören, anhoeren | [camera] [seconds] | admin |
| `/sound` | ton, klang, abspielen | [name] | admin |
| `/volume` | lautstärke, lautstaerke, vol | [0–100] | admin |

- **Cameras** can be named by ID, name, or a unique beginning (`/photo ent`).
  With a single camera the argument is never needed; with several, the bot
  shows a button per camera.
- **Durations**: `30` (minutes), `45s`, `30m`, `2h`, `2std`, `1d`, `1t`.
  `/away 3h` sets AWAY for three hours, then returns to automatic presence
  detection (or to the previous mode if presence detection is off).
- Only canonical names are registered as Telegram slash commands (the list
  that appears when typing `/`); aliases work as typed text.
- `/zoom` and `/brighter` work on the last photo the chat received — a
  requested photo or an alert — so "zoom in" under an alert enlarges that very
  picture.

## Ordinary sentences

In private chats the bot also understands everyday sentences:

| You write | It does |
|---|---|
| "Kannst du mir ein Foto vom Eingang schicken?" | `/photo entrance` |
| "Schick mir ein Video von 30 Sekunden" | `/video 30` |
| "Das Bild ist unscharf" / "zu dunkel" / "vergrößern" | `/sharper` / `/brighter` / `/zoom` |
| "Ich bin wieder da" / "Ich gehe jetzt weg" | `/home` / `/away` |
| "Funktioniert alles?" / "Was ist passiert?" | `/status` / `/events` |
| "Hallo" / "Danke" | menu / a friendly reply |

This is keyword matching, not a language model; everything stays on the
device. Only harmless commands can be reached this way: never the alarm, the
microphone or user administration. If a sentence fits several commands or
negates a mode change ("ich gehe heute nicht weg"), the bot asks with buttons
instead of guessing. In groups only real commands work.

## Audio messages

Admins can send a voice message or an audio file (OGG/Opus, MP3, M4A, WAV,
FLAC; up to `audio.max_upload_seconds` and `audio.max_upload_bytes`). The bot
checks the file and offers *Play on the speaker now*, *Save as sound* and
*Discard*. Files from other users are never downloaded.

## Trusted people

Requires `PI_GUARD_FACE_RECOGNITION_ENABLED=true` and
`pi-guard models download`. Admins find everything under
*More functions → Trusted people*.

Enrolling is a short session:

1. *Add a person* (or `/enroll Erna`). Without a name the bot asks for it.
2. Send photos, image files or a short video (up to a minute) showing only this
   person, or press *Take a picture with the camera* while they stand in front
   of it. Each is checked for exactly one face; near-duplicates are skipped.
   Photos sent without a caption during the session are added automatically.
3. *Done*, then confirm that the person has consented. Only then is anything
   stored. Photos and video frames are deleted right after their face data has
   been computed.

Alerts about a person that was not recognised carry an *Add as trusted person*
button for admins, which starts a session with that picture.

Recognised people do not silence alerts: motion is still reported in AWAY mode.
Unknown people are reported with a photo (`unknown_person`), and an uncertain
recognition is reported as such. Up to three pictures are judged together; a face too
small or blurred to be sure is a stranger only if it matches nobody twice, see
[configuration.md](configuration.md#how-person-detection-decides). More photos of different angles and lighting
improve recognition (*More photos of …*). See [privacy.md](privacy.md).

## Alarm

With a speaker configured (`PI_GUARD_AUDIO_PLAYBACK_ENABLED=true`), admins can
press *More functions → Alarm* or send `/alarm`. The bot asks once more, then
the alarm sound repeats for 30 seconds at the maximum allowed volume, also
during quiet hours, as one stream without gaps, so a USB speaker does not mute between rounds. `/alarm aus` or *Stop the alarm* stops it immediately.
Any uploaded audio can be made the alarm sound with *Use as alarm sound*.

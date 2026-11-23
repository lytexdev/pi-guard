# Web dashboard

The dashboard runs inside pi-guard. It and the phone app are on by default; the
installer sets them up. It is meant for the home network or for access through
a VPN (WireGuard) — never expose it to the internet with a port forwarding.

## Enable

To switch the dashboard or the phone app off:

```dotenv
PI_GUARD_DASHBOARD_ENABLED=false
PI_GUARD_MOBILE_ENABLED=false
```

Restart pi-guard and open `https://<pi-address>:8443`. Telegram does not need
to be configured beforehand: with the dashboard enabled, pi-guard starts
without a bot token and Telegram can be set up in the assistant.

## First login and setup assistant

The first login is **admin** with the password `install.sh` shows at the end. Without the script,
for example in Docker, it is **admin123** unless you set `PI_GUARD_DASHBOARD_FIRST_PASSWORD`. Before
anything else works, the
dashboard asks for your own user name and password (at least 10 characters,
not the default). It then offers two-factor codes: scan the QR code with an
authenticator app (Google Authenticator, Aegis, 2FAS, …) and confirm one code.

The setup assistant continues with — every step except login can be skipped and
opened again later under *More → Setup assistant*. Steps the installer already
finished show as *Already set up*:

1. Welcome and language
2. Login and two-factor codes (required while the first password is still in use)
3. Cameras, microphone and speaker
4. Messengers: Telegram, and Discord, e-mail, WhatsApp or Signal if you want them
5. Alerts and modes: alerts also while at home, video instead of photo,
   quiet hours, presence detection
6. Emergency contacts
7. Trusted people: switch recognition on (the model files are only downloaded
   after you press the button, with size and source shown), then enroll people
8. Add-ons: which optional parts to switch on
9. Apps: which apps this Pi hands out, a download link with a QR code to scan with the phone (valid for some days),
   how to connect the app, and a note for iPhone (add the dashboard to the home screen)
10. Design: dark, light or high contrast, normal or large text (per device)
11. Finish: restart pi-guard if settings changed

Until the default password has been changed, the default login is the only
way in; keep the dashboard off the internet until then. While pi-guard still
uses it, the log says so at every start.

**Members and viewers** do not get the setup assistant. After their first login
they can change their own password and two-factor codes under *Access*. The
tutorial under *Help & tutorial* shows only the pages their role may open
(overview, live and events for viewers).

## Login methods

`PI_GUARD_DASHBOARD_AUTH` takes one or both methods:

| Method | How |
|---|---|
| `password` | the dashboard account (first login as `admin`, changed right away) |
| `keyfile` | Choose a key file instead of typing a password. Create one under *Access* or with `pi-guard dashboard keyfile tablet -o tablet.key`. Only a hash is stored; delete the key there if a device is lost. |
| `code` | A six-digit code to one of your own ways: the admin accounts linked in Signal, Discord, WhatsApp, Telegram, the phone app or a chat app, or the email address in your account's contact details (never to alert recipients). Valid 5 minutes, 5 tries. Offered only when a code word or two-factor codes are set, and they are asked after it, so a taken-over mailbox or chat alone cannot log in. |
| `none` | No login. Only allowed on `127.0.0.1`, or with `PI_GUARD_DASHBOARD_ALLOW_UNAUTHENTICATED=true`. |

Passkeys and OpenPGP keys need no setting: once one is added under *Access → Login security*, its button appears on
the login page. For OpenPGP, the page shows a short text with a random part; sign it, for example with
`gpg --clearsign`, and paste the result. The Pi checks it with GnuPG against the added keys only.

Two-factor codes apply to every method. Change the login or switch codes on
and off later under *Access → Your account*.

**What is asked after the first step,** for the main account: two-factor codes if set, the code word if set, and one
of up to five **security questions** if any are set, always in addition and never instead of the code word. A question
has a free answer or is multiple choice, can have a hint, and is the same for one login attempt, so asking again does
not bring an easier one. Answers count without upper and lower case and are stored only as hashes.

**Contact details** (*Access → Contact details*, and for admins next to each user): name, email, phone, address and a
note, all optional, with vCard to read from and save to an address book. They move with an account that is renamed,
are deleted with it, and are part of the data export.

### More users

*Settings → Access → Dashboard users* adds people with their own password and role:

| Role | Can do |
|---|---|
| Admin | everything |
| Member | what viewers see, plus what an admin ticks under *Permissions*: areas (home/away and alarm, people, network, logs, contacts, devices, apps), single add-ons and single settings sections |
| Viewer | see the overview, live picture, camera room and events, and change their own password and two-factor code |

Viewers cannot listen in, talk, take photos, change the mode, use the assistant or open
settings. The server enforces this with an allowlist, so new features stay closed to viewers
until they are added to it. Members are checked the same way (`web/permissions.py`): each grant opens whole path
segments, and access, logins, tokens, backups, updates, the security check and own add-ons are never grantable. A
member's settings page shows and saves only the granted groups. The link button next to a person opens their contact
details and links: Telegram, Discord, Signal and WhatsApp accounts, a trusted person the cameras know, and their
emergency card. A changed role counts at once; removing a person ends their
sessions and removes their phones, computers and VPN access. A new password logs out all other
browsers of that person; paired phones log themselves in again the next time they open. Adding a
passkey, an API token or a key file asks for the password; a passkey the owner's. Hardware keys, recovery codes and the code word belong to the main account.
The code word can have a hint. It is shown at login only after the right password, and it must not contain the word.

### Hardware keys and passkeys

Browsers only offer a YubiKey or passkey when both of these are true:

1. **The dashboard is opened by name, not by IP address**, for example
   `https://lytexsystem:8443`. If the name does not resolve on your devices,
   `sudo ./scripts/dashboard-name.sh pi-guard.lan` adds one to Pi-hole and to the certificate.
   This changes Pi-hole's local DNS records.
2. **The browser trusts the certificate.** Clicking through the warning is not enough: Chrome
   and Chromium refuse keys on pages with certificate errors. See below for installing it. On
   Arch Linux: `sudo trust anchor --store pi-guard-ca.crt`, then restart the browser.

*Access → Set up a hardware key* shows these steps, and a failed attempt shows the browser's own
error message.

## Add-ons and modules

Telegram, Signal, Discord, WhatsApp, email and the chat services are modules under *Add-ons*. A module that is off
disappears from *Settings → Messenger* and stops at the next restart; its entries are kept. Modules that are part of
pi-guard carry a *Verified* badge. Each card's menu (three dots) pins it to the navigation, shows help and a way to the
tour, and removes it. The plus beside the search adds your own add-ons, see [plugins](plugins.md). The phone app is no
module: it is on by default, under *System → Updates*, with the versions this Pi hands out and invitation links (seven
days, downloads only, kept as a hash) for people who should get the Android or desktop app. The old *Settings → Apps*
address redirects there.

Panels in the settings fold by clicking their headline, and stay that way in this browser. Messengers that
are off start folded.

## Trusted people

The form explains what makes recognition work. **Take photos or a video now** opens the camera of the
device you are using and guides the person through front and turned views, or films 8 seconds without sound. A small
head beside the text shows where to look. The picture keeps the camera's own shape and its widest zoom, so on a phone
held upright the face is not cut off.
Tap a picture to remove it; nothing leaves the page before **Upload**. This needs the HTTPS address and your
permission for the camera; otherwise the page offers the phone's camera app. You can also choose existing
files or use a camera at home.

A bar shows how well each person is learned, with the pictures per viewing direction and the next
useful step. The score is an estimate from the number of pictures, front and turned views, light and how
well the pictures agree; look-alikes in the family lower it. *Test with a photo* shows the real result.

Faces turned a little to each side help, because people rarely look into a camera. Both eyes must stay
visible: the face models cannot use a pure side profile. Pictures enrolled earlier count as front views.

### Learning from the camera

Photos from a phone and pictures from a camera across the room look different to the face models. **Who is
this?** on a person event, on the overview or the events page, shows the faces pi-guard finds in its picture and
whom it saw in each:

- **This is …** stores the face as one more picture of a person already on the People page, so the camera's angle
  and light are learned. New people are added on the People page, with their consent. The face must be at least
  56 pixels wide, sharp and roughly facing the camera; a smaller one would teach more noise than face. A face that
  hardly resembles the person's stored pictures, or looks more like somebody else, is refused from the person's
  third picture on, which catches a wrong click; the first two are taken as you say. In the person's details these pictures are marked *from the camera*.
- **This is not …** on a recognised face notes it as somebody else. From then on, a face more similar to it than to
  that person is *uncertain*, not that person. It is deleted with the person.

- **Who is this?** at the top of the People page goes through the faces of the last two weeks' person events like a
  deck of cards: swipe right (or →) for *yes, it is the suggested person*, left (←) for *no* or *a stranger*, up (↑)
  to skip, or tap a name below the picture (keys 1–9) to say who it is instead. Yes and no work like *This is …* and
  *This is not …* above, with the same checks; faces too small or blurred to learn are left out. A face once swiped
  does not come back. Only people already enrolled can be chosen.
- **Learn at the camera** on a person's card: the person stands where people usually pass, looks at the camera and
  turns the head a little, for 20 seconds. The Pi takes one picture a second and keeps up to eight clear faces that
  differ from the views it already knows; each picture is deleted as soon as its face is measured. Faces that do not
  match each other or the person are left out, so somebody else walking by is not learned. Repeat it in other light,
  for example in the evening. Only for people already enrolled, with their consent.

A person keeps up to `faces.max_samples_per_person` pictures (default 40, set below the numbers on the People page,
used at once). A picture nearly identical to one already
stored is left out, and when the limit is reached the most similar one goes, not the oldest: varied views are what
recognises someone in another angle and light.

**Why recognition works or not** on the People page shows the last 30 days in numbers: moments with a person, how many
showed a face at all, how large faces were, how close the best one came to the threshold, and per person how many
pictures come from a camera and how many are near copies. It says what would help most. Often it is the camera:
recognition needs a face in the picture, roughly facing the camera and at least 36 pixels wide, 56 to learn from it.

Only the numbers of a face are stored, as with any enrollment. Viewers do not see the buttons.

## Contacts

*Contacts* has its own entry in the navigation, below *Network*. It lists everyone as cards: role badges and chips for
what is linked (face with its number of pictures, dashboard account, messengers, emergency contact, emergency card, VPN
devices), with search and role filters. The picture is drawn from the initials; *Change picture* (tap it, or the menu)
puts in an own photo, cut to a square and made small in the browser, and *Use the default picture* goes back. A
coloured ring shows the rights at a glance: red admin, green member, blue viewer, purple second line, orange
emergency contact, yellow gets alerts; the legend is below the cards.

Each card has *Edit* (the setup below), *Photos & videos* (upload pictures and videos for face recognition, or the live
camera, with the person's consent; only face features are stored) and *Invite*. An invitation goes to one of the
person's own ways to reach them (messenger or email, never a typed address), and you choose what it holds: the link to
the dashboard, the Android app, the desktop apps (download links valid for 7 days), the login details, and an own VPN
access as a WireGuard file. Login details carry a new temporary password, stored only once the message went out; the
old one stops working then. Login details and a VPN access ask for your password, and the dialog says which services
keep the message. If sending fails, a new VPN access is removed again. Members need the *Apps* area for the app links
and the *Network* area for a VPN access, besides *Contacts*; login details are for admins only, and never for the
owner's login. *Not linked yet* offers faces, accounts and emergency contacts that belong to nobody, one tap each. The
detailed face view and the emergency contact list fold away below.

*New person* opens a short setup (who they are, roles, messengers that are running on this Pi, what they get on SOS or
unanswered alerts, optional dashboard account, face samples, and optional emergency medical sheet). A new account gets
a temporary password: made by the Pi (recommended) or typed by you. It is shown once, with *Send an invitation*; at the
first login the person must choose an own password before anything else opens. Saving uses the admin session; medical
sheet sharing still asks for confirmation. Messenger channels are on/off cards for services that are set up.

*Who is this?* at the top assigns faces from recent events to a person or marks a stranger by swiping. Every contact is
offered: those the cameras do not know yet under *Not taught yet*, and their first face is taken only after you confirm
that the person agreed; the face is then linked to the contact. The old *People* and *Settings → Contacts* addresses
redirect here. Who may use Telegram/Signal/… for commands is managed here; the old admin/viewer ID lists under
Messaging stay in expert mode only.

### Roles and who hears what

- **Second line** (role): household people who look after an alarm when no admin reacted. An alarm still open after
  *Tell the second line after (minutes)* (default 10, `emergency_backup_minutes`) goes to their messengers, before the
  emergency contacts; admins are told who was informed.
- **Emergency contact** and **gets alerts**: written to on an SOS or when alerts stay unconfirmed, see [mobile](mobile.md).
- *Settings → Notifications* shows everything as a staircase of four steps, each set up where it is:
  1. **At once to you:** per way what it sends by itself, the phone app and browser push first, then each messenger
     (alerts and emergencies, only one of them, or *commands only*), and each kind of message with when, to whom and
     which ways. Alarms in the app only and the rest also on a messenger: the app on *alerts and emergencies*, the
     messengers on *emergencies only*; they then carry the second line, escalations and SOS. Browser push can be
     switched on right there; an alarm that went only by push still counts as open for the next steps.
  2. **Second line:** after how many minutes an unconfirmed alarm goes to the people with that role.
  3. **Emergency contacts:** after how many open alarms and minutes they are told, who takes part and what comes with
     the message (latest events, pictures from the Pi).
  4. **SOS:** always at once to every admin on every messenger, to linked households and to the contacts that take
     part, with what is ticked for each (place, phone photos and sound, pictures from the Pi, sound from home,
     emergency data). A warning shows when the second line would come after the emergency contacts.
- *Settings → Emergency* shows the emergency contacts and whose emergency data exist, above the escalation settings.

## System

*System* in the navigation has three tabs. **Health** checks the running service every 30 seconds without touching the
camera: pi-guard and its parts, each camera (state, last picture, recording), storage, memory, processor, temperature,
the Pi's power supply (undervoltage and throttling, now or since boot), the database, each messenger, the detection
models, the last backup and the clock; a problem links to where it is fixed. `sudo pi-guard doctor` checks more
(FFmpeg, encoders, cameras). It opens with a ring of how many checks are fine and *Check again*, then tiles for
load, memory, temperature and free space with meters, then one card per check, problems first. **Device** shows model, processor, memory, operating system, kernel, uptime, the data and
recording disks, cameras and sound devices, USB devices and the network addresses. **Updates** is described below.
Members see the System page only for updates, when they may manage apps; health and device stay with admins.

## Updates and versions

*System → Updates* shows the Pi's version (it is also next to the credit in the navigation), the newest release,
where updates come from (repository and channel, see [updates.md](updates.md)), this installation's instance ID,
every app version the Pi hands out (*Check for app updates*), invitations and phones. The anonymous statistics are
off for now. The same is under *Settings → System*.

*Update now* shows the update's real steps as the root job reports them: requested, fetching and checking the signed
release, installing, restarting (the page waits while pi-guard is away, and says so when the old version comes back),
done, with *Reload the page*. A failed step turns red with the job's message. *Check for updates* shows that it is
looking and then, for a moment, *New version found* or *No new version*.

## Everyday tools

- **Trying out devices:** under *Devices & cameras*, **Test speaker** plays three tones on the chosen
  speaker. **Test microphone** records 3 seconds for you to hear and shows how loud it was. The sample is
  not kept; the test is logged and announced with the chime, like listening in.

- **Tooltips** explain buttons and entries on hover and keyboard focus.
- **Navigation:** *Appearance → Navigation* sets your own order by dragging, per account. *Network* is in the
  primary bar by default (with Overview, Live, Events). *Logs* live under *Settings → Logs* (`#/logs` redirects).
  The camera room is a switch at the top of Live.
- **Weekly plan** (*Settings → Home & Away*): drag in a day to mark when nobody is usually home. pi-guard switches
  to Away and back at those times. What you set by hand stays until the next change in the plan. If
  presence detection is on, it stays at Home while someone is detected at home.
- **Support** (*Settings → Support*): optional name, phone, email and address, shown small on the overview, in
  settings, logs and the assistant. The assistant answers with them when asked for help. They are set during
  installation or in the env file (`PI_GUARD_SUPPORT_*`, see [configuration](configuration.md)), not in the
  dashboard: the page only shows them. Values an older version stored from the dashboard show until the env file names
  someone.
- **Logs:** *Settings → Logs* filters the service log by exact level (INFO does not include WARNING). Known noisy
  “SSL connection is closed” lines from aiohttp are rate-limited to once a minute.

## Network tools

- **Wi-Fi QR codes:** the Wi-Fi card on the network overview has two lines, *Home Wi-Fi* and *Guest Wi-Fi*.
  With a FRITZ!Box both come from there and *Share* reads the key from it. Without one, *Save* each once (name and
  key, with your password); the key is sealed with the dashboard's secret key and only opened for the code. The code
  can be saved as a picture; anyone who has it can join.
- **What others can see of you** explains what your provider, others in the network and the operator
  of the network can see, using what this Pi knows about the device you are on.
- **Locking a device out:** TR-064 has no call to throw one device out of the Wi-Fi. There are two
  ways instead, both with your password:
  - *Block internet* in the device details. The device stays in the Wi-Fi but cannot reach the
    internet.
  - *New key* in the Wi-Fi code window. Every device has to join again, also yours. Devices you do
    not give the new key to can only use the guest Wi-Fi. This is refused if the Pi itself uses Wi-Fi.
- **The map** opens large on a click on its background or the enlarge button.

## Security scan and storage

*Settings → Data & backup → Security* checks the login, HTTPS, pi-guard's own files against their checksums, open
ports, system updates, the firewall, SSH, system files against their packages, unknown programs with
root rights, programs in temporary folders, and accounts. It changes nothing by itself and can send a summary to
admins by messenger (also `/securityscan` in chat). A rootkit scan runs if `rkhunter` or `chkrootkit`
is installed; warnings every pi-guard has (Pi-hole's tables in `/dev/shm`, the pi-guard user added to groups) count as
known. A clean result is no guarantee that a system is not compromised.

**Fixes:** below the result, fixes from a fixed list fit what was found: SSH root login off, SSH with keys only
(refused when no key is set up), automatic security updates, accept the rkhunter state, firewall on (everything
reachable today and SSH stay open). Each runs in the root job with a backup, after two confirmations, the fix's name
typed and the password, and *Undo* puts the earlier state back. **AI review** sends statuses and short details to the
assistant's provider after a confirmation. It runs in the background, so you can leave the page, and shows its real
stage. The result stays until the next review: an overall rating, steps sorted by priority (important, medium,
minor) with the check each is about, a button when a listed fix does it, and any other command folded away to copy,
never run. A review older than the last check says so.

The root job reports after each of its steps, so the progress shows the step it is really on. A step that crashes or
whose commands time out marks its checks *unknown* with the reason, instead of *ok*; the other steps still count.
pi-guard waits as long as the job reports progress (30 minutes without any, two hours in all).

*Settings → Data & backup → Storage* shows what uses the disk and can delete old recordings, photos and clips, compact
the database, and clear old system logs and the package cache (also `/storage` in chat). Evidence videos
and face data are not cleaned there.

pi-guard has no root rights. The system checks come from `pi-guard-maint`, a root job started by a
systemd path unit. It accepts only fixed words (the jobs, and `fix-*`/`undo-*` for the listed fixes) and writes its
reports to `/var/lib/pi-guard-scan`, where pi-guard cannot write; backups of fixes stay in `/var/lib/pi-guard-fixes`,
readable by root only.

## If the dashboard feels slow

Open *Logs* and look for `slow request:`. Every answer that took half a second or more is listed there with its route,
and the assistant's answers appear because they take a while anyway. More in [Performance](performance.md).

## Appearance

*Settings → Appearance* sets the accent colour, font, animations and the app's name for everyone, also on the login
page. The logo is pi-guard's own and cannot be changed (the phone app can still disguise itself). Theme (as the
system, dark, light, high contrast) and text size are chosen per device; the light theme keeps every text colour at
4.5:1 or more. *Reset to default* puts all of it back after a confirmation. No fonts are loaded from the internet: the
choices use what the device has. The high-contrast theme keeps its own colours. The phone app (from 1.17) takes the
theme for its own screens and the system bars too. On the overview, the chosen mode (*Home* or *Away*) is filled and
ticked.

## Logs

*Logs* has four tabs: an overview of the last seven days (logins, failed logins, honeypot hits, bans, advice), the
service log with level chips, search, pause, copy and download, the audit trail grouped by day, searchable, and
**AI look**. There the assistant reads the warnings and errors of the service log (repeats folded into one line with a
count, long tokens cut) and the last 80 audit entries, after a confirmation, and answers with a health rating and
findings by severity, each with what it means and what to do. Admins only; it changes nothing.

## Accessibility

The round button at the bottom right opens the accessibility panel on every page, for this browser only: text size,
letter and line spacing, inverted colours, greyscale, filters for red-green and blue-yellow colour blindness, a large
cursor, a reading ruler, a magnifier for the text under the pointer, underlined links, fewer animations, reading
tapped text or the whole page aloud, and a guided tour. The text size works on top of the large text of the design;
rows such as events wrap instead of squeezing the words. The phone app's WebView has no speech output: reading aloud
says so there. If the browser blocks storage (a private window), the settings last for this page only.

## Help and tutorial

*Help & tutorial* in the navigation explains every part of pi-guard in a few sentences, each with a button to it, and
*Start the tour* walks through them one page at a time; viewers see only their pages. The chapters are: overview, live,
events, contacts, network, VPN, Kid Protect, notifications, emergency contacts, emergency data, apps, find devices, Computer-Connect, assistant, moving,
system, and settings.
The same tutorial is everywhere:

- in the **desktop app** under *PiGuard › Help & tutorial* (F1), and in the **phone app** under *More → Help*; both
  open this page with the tour;
- in the **assistant**: typing or saying "Tutorial" or "Rundgang" in the dashboard's chat starts the tour, and questions
  such as "How does Kid Protect work?" are answered from the tutorial, without an AI provider and without cost;
- in **chats**: `/tutorial` (also *Anleitung*, *Rundgang*) sends the overview.

- **For deaf and hard-of-hearing people:** *Show alerts visibly* makes the page glow and the device vibrate for every
  new alert. It pulses slowly and never flashes fast.
- **For people who cannot speak:** *Speak* on the Live page says a typed text through the speaker at home.
- **For blind people:** the dashboard is built for screen readers such as NVDA, JAWS, VoiceOver and TalkBack: controls
  carry labels, changes are announced, and *Skip to content* is the first thing the keyboard reaches. It has not been
  tested with each of these readers yet; please report what does not work. With *Read aloud*
  on, answers of the assistant are read too.
- **By voice or chat:** in the assistant's chat, typed or spoken: "Schrift größer", "Vorlesen an", "Lupe aus",
  "Rot-Grün", "Sichtbare Alarme an", "zurücksetzen" (also in English). These are done in the browser and never sent
  anywhere. Talking to the Pi itself is described in [voice.md](voice.md).

### The secret key

`PI_GUARD_DASHBOARD_SECRET_KEY` (at least 32 characters) is mixed into the
password hash as a pepper and encrypts the stored two-factor secret. A copy of
the database alone is therefore not enough to guess the password offline or to
create login codes. If it is empty, pi-guard creates `DATA_DIR/dashboard.secret`
— that works, but keeping the key in the env file separates it from the data.
If the key changes, the password and two-factor codes stop working.

### Locked out?

```bash
sudo -u pi-guard pi-guard dashboard reset-password
```

resets the login to admin with a new password it shows (to be changed at the next login), turns
two-factor codes off and ends all sessions.

## HTTPS and the certificate

Browsers only allow the microphone (talking through the speaker), installing
the app and push notifications on HTTPS pages they trust. pi-guard creates its
own small certificate authority (CA) in `DATA_DIR/tls` and issues a
certificate for the device's names and addresses (reissued automatically when
the address changes). Install the CA once per device:

1. Open `https://<pi-address>:8443/ca.crt` (the browser warns once; that is
   expected before the CA is installed) or copy `DATA_DIR/tls/ca.crt`.
2. Install it:
   - **Android:** Settings → Security → Encryption & credentials → Install a
     certificate → CA certificate.
   - **iPhone/iPad:** open the file, Settings → Profile downloaded → Install;
     then Settings → General → About → Certificate Trust Settings → enable
     *pi-guard local CA*.
   - **Windows:** double-click → Install certificate → Local machine → Trusted
     Root Certification Authorities.
   - **macOS:** double-click → Keychain *System* → open the certificate →
     Trust → Always trust.
   - **Linux (Chrome/Firefox):** browser settings → Certificates → Authorities
     → Import, trust for websites.
3. Reload the dashboard.

`pi-guard dashboard certs` prints the paths and the CA fingerprint so you can
compare it. The CA key never leaves the device. You can use your own
certificate instead (`PI_GUARD_DASHBOARD_TLS_CERT`, `…_TLS_KEY`), e.g. from a
reverse proxy with a real domain.

## The assistant's chat

With the AI assistant on, a round button at the bottom right opens its chat: a panel beside the page on a computer,
the whole screen on a phone. It starts with three examples to tap, shows dots while the Pi thinks, renders lists and
bold text in answers, asks *Yes* or *No* before every action, and shows errors in the chat itself. *New conversation*
clears it; the microphone records a spoken question where the browser allows it.

## Normal and expert view

Settings are grouped into Basics, Alerts, Devices & access and System (with Add-ons); each section starts with a line on what it
holds, and each setting shows its explanation under its name. Switches are drawn as toggles. *Data & backup* holds
four tabs: *Backup*, *Storage*, *Moving* and, in the expert view, *Export*; the old links (`#/settings/backup`,
`/storage`, `/move`, `/data`) open the right tab.

Since 1.18: *Detection* and the sound switches (record sound, play sound, loudest volume) are part of *Devices &
cameras*, below the devices they belong to. Wake words, the talk button and *Talk to the Pi* are on the AI assistant
add-on's page. **System** shows the Pi's health in one line (details and hardware on the System page) and the updates
of pi-guard and the apps with their settings. **Network** holds the router, Pi-hole, VPN and dynamic DNS settings, with
links to the network pages and the Cloudflare DDNS and Tailscale add-ons. *Integrations* (expert view) keeps MQTT and
the saved keys. Old links (`#/settings/detection`, `/audio`, `/updates`) open the new place.

Settings open in the normal view with what most people need. The switch next to the title and the search shows everything, also the complex
settings, the sections Integrations, Dashboard and Security, and the Export tab. It is remembered per device and works the same
in the phone app. Nothing is lost by switching: hidden settings keep their values.

In the expert view, *Messengers* ends with *Test messages*: one test message through each messenger that is set up,
to every admin there or into the channel of Slack, Matrix, Rocket.Chat or Mastodon. Add-ons that are part of
pi-guard carry a check mark; hovering, focusing or tapping it says *Verified*.

## Asking about settings, and undoing changes

*Settings → General* starts with *Ask about settings*: "What does motion sensitivity do?" or "Keep recordings for 14
days". Without the AI assistant it lists the settings that match your words, with their help text and value. With the
assistant it also explains them and may propose one change, shown as *before → after*. Nothing changes until you tap
*Change* and confirm.

What the AI gets: labels, help texts and the values of switches, numbers and choices. Never secrets, lists such as
chat IDs, addresses, or the time zone. Settings about login, dashboard access, VPN, updates and the assistant itself
are explained but never proposed. A proposal passes the same checks as the form.

*Recent changes* lists every change, from the form, the assistant or an undo, with who made it and the value before and
after. *Undo* puts the earlier values back; a value that was not set before is removed again, so `pi-guard.env` counts.
Changed secrets are marked but never kept, so they cannot be undone. The last 200 changes are kept. Like every
setting, a change takes effect after a restart.

## Where notifications go

Under Settings › Notifications every kind of message has its own ways: tap *All ways* below it and pick, for example,
only Signal, or Telegram and the PiGuard app. Changes work at once. *Where what arrives* at the top of the page sets per
messenger what it sends by itself (*commands only* keeps it for commands) and when the second line hears of an alarm;
see [roles and who hears what](#roles-and-who-hears-what).

## Install as an app (PWA) and push notifications

- **Android / Chrome / Edge:** browser menu → *Install app*.
- **iPhone/iPad (iOS 16.4+):** Share → *Add to Home Screen*. Push
  notifications only work in the installed app.

Enable push with `PI_GUARD_WEBPUSH_ENABLED=true` (and a contact address in
`PI_GUARD_WEBPUSH_CONTACT`), then on each device: *More → This app → Receive
alerts on this device*. Push messages carry the alert text only, end-to-end
encrypted, and are delivered through the browser vendor's push service
(Google, Apple or Mozilla) — see [privacy.md](privacy.md). Admins get two buttons in each notification, *All fine*
and *Sound alarm*. The notification carries a token for one tap, valid for an hour; pi-guard keeps only its hash.

## What it offers

| Page | |
|---|---|
| Overview | mode (home/away/automatic), cameras, open alerts with *Everything is fine*, storage, memory, temperature, the last 24 hours |
| Live | live picture (pinch, mouse wheel or buttons to zoom, full screen), photo, *Sharper*, zoom into nine areas and *Brighter* on the taken picture, listen live, hold-to-talk through the speaker, sounds, alarm, volume; *FPS* shows the frame rate and *Mark* marks people and things, checked at most once a second while it is on |
| Events | filter by type and camera, thumbnails, movement per day and by time of day; a person event says who was recognised, or why nobody could be named |
| People | trusted people: add with photos/videos or a camera picture (consent checkbox), add more pictures, rename, delete |
| Network | Pi-hole statistics and pause, WireGuard peers, devices in the LAN (see [network.md](network.md)) |
| Devices | camera device, resolution, frame rate, motion settings, microphone and speaker as selection fields; trusted presence devices |
| Settings | the `.env` settings, grouped; the Telegram bot token can be entered but is never shown again |
| Access | Dashboard users and roles, Telegram/Signal users and invitations, key files, active sessions, blocked addresses, certificate download |
| Logs | service log and audit trail |

German and English are switched under *More*.

Settings saved in the dashboard are written to `DATA_DIR/dashboard.env` and
`DATA_DIR/dashboard.yaml` (the service may not write to `/etc/pi-guard`). They
take precedence over `pi-guard.env` and `config.yaml`. Delete those files to
return to the files in `/etc/pi-guard`. Most changes need a restart, which the
dashboard offers; recording pauses for a few seconds.

The Telegram bot token can be entered in the dashboard (write-only, stored in
`dashboard.env` with mode 0600); other secrets only in `pi-guard.env`.

### Live view, listening and talking

- **Fullscreen** uses the browser's own fullscreen where it is allowed. On an iPhone, which has fullscreen only for
  videos, and in the phone app, it fills the window instead; the round button, Escape or the back button closes it.

- The live picture is an extra low-rate MJPEG output of the existing capture
  process (`PI_GUARD_DASHBOARD_LIVE_FPS`, default 8, `0` turns it off;
  `…_LIVE_WIDTH`, default 960). The camera is still opened only once.
- **Listening** needs `PI_GUARD_AUDIO_RECORDING_ENABLED`. A chime plays on
  site when a speaker is configured, each session ends after 10 minutes and
  every session is written to the audit log.
- **Talking** needs a speaker and HTTPS: hold the button, speak, release; the
  recording is played on the speaker (also during quiet hours).

## Protection

- Session cookie `HttpOnly`, `Secure`, `SameSite=Strict`; only a hash of the
  session ID is stored; idle timeout 12 h, at most 7 days.
- CSRF token and origin check on every change.
- Content Security Policy without inline scripts, no framing, no referrer,
  `noindex` everywhere and `robots.txt` disallowing all.
- Rate limit per address (`PI_GUARD_DASHBOARD_RATE_LIMIT_PER_MINUTE`), a
  stricter one for login, and a temporary ban after 8 failed logins in 15
  minutes (`PI_GUARD_DASHBOARD_BAN_MINUTES`).
- Honeypots (`PI_GUARD_DASHBOARD_HONEYPOT`): requests for paths that scanners
  probe (`/wp-login.php`, `/.env`, `/phpmyadmin`, …) and a hidden login form
  field lead to a ban; the response is delayed to slow scanners down.
- Behind a reverse proxy, list it in `PI_GUARD_DASHBOARD_TRUSTED_PROXIES` so
  the client address is taken from `X-Forwarded-For`.

## Docker

The compose file publishes port 8443. Certificates and keys live in the
`data` volume. Inside a container pi-guard only sees the container's own
addresses, so add the names and addresses you open the dashboard with:
`PI_GUARD_DASHBOARD_TLS_NAMES=192.168.178.10,raspberrypi.local`. Set
`PI_GUARD_DASHBOARD_ENABLED=true`. Without `PI_GUARD_DASHBOARD_FIRST_PASSWORD`
the first login is **admin** / **admin123**, then the setup assistant. The
Android and desktop apps use this same dashboard; see [mobile.md](mobile.md)
and [desktop.md](desktop.md).

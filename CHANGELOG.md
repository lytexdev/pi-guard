# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Members: a third role that watches like a viewer and uses only what the owner ticks (areas, single add-ons, single
  settings sections). The server checks each request by whole path segments; anything not listed stays with admins.
- Links per account: Telegram, Discord, Signal and WhatsApp accounts, a trusted person the cameras know, and the
  emergency card.
- Settings › Apps: the phone app is part of pi-guard (on by default), with the versions this Pi hands out and
  invitation links to download the Android and desktop app (seven days, downloads only, stored as a hash).
- Add-on cards: a menu with *Pin to navigation*, *Help & guide* (with the tour or the add-on's docs) and *Remove*;
  add-ons from others bring `help` and `docs` in their manifest and appear as the same cards. Telegram and Signal are
  add-ons like WhatsApp and Discord.
- Security check: fixes from a fixed list in the root job, each with a backup and *Undo* (SSH root login off, SSH keys
  only, automatic security updates, accept the rkhunter state, firewall on), after two confirmations and the password;
  and an AI review of the latest result that can point to these fixes and shows other commands only to copy.
- The assistant as an emergency contact (off by default): it looks at alarm pictures with what the Pi's face
  recognition found and sends a short view; reference pictures never leave the Pi.
- Speech: a vocabulary of this home corrects nearly-right names offline on the Pi and in the chat; cloud speech gets
  the names as a hint, never people's names.
- Updates: the repository and channel (stable or beta) to update from, beta tags and pre-releases
  (`ship.sh --beta`), an instance ID made at the first start, app versions with *Check for app updates*, and the
  version in the dashboard, the desktop app's About and the phone app's settings.
- VPN devices show their type (Android, iPhone, Windows, Linux, Mac, …), from the app, the form or the name.
- Appearance: *Reset to default* after a confirmation; theme *as the system*, now the default.
- Logs in three tabs: overview, service log with level chips, search, pause, copy and download, audit trail by day.
- Medication plans as free text, Markdown or a Markdown table.
- One release of everything: `scripts/ship.sh` gives the Pi, dashboard, phone app, desktop app and agent one version,
  chosen from the commit prefixes (`feat` minor, `!` or `BREAKING CHANGE` major, otherwise patch), then builds, signs
  and publishes. A new minor or major version installs by itself on the Pi (`update_auto`), the phone (on Wi-Fi) and the
  desktop; a patch is announced and installed with a tap. Pis look for releases every hour instead of every 6, and
  *Check for updates* in the apps makes the Pi look on GitHub at once. The dashboard shows the release's version.
- A disguised way home with AmneziaWG for networks that block WireGuard: `scripts/amneziawg.sh` builds the kernel
  module (DKMS) and tools from fixed, checksum-verified releases and sets up `awg0` next to `wg0` with the same key,
  devices and addresses; `pi-guard-awg-sync` mirrors devices and routes each through the interface it greeted
  last. The phone app (1.8) gets the disguised ways and tries them after the normal ones, using AmneziaWG's tunnel
  library, which `build-android.sh` builds once. The post-quantum service, the privacy exit and its kill switch
  include `awg0`. *Network › VPN › From outside* shows the new way. Not yet in the desktop app.
- Emergency contacts in the dashboard (*Settings → Emergency*): name, relation, phone, email, a linked account, several
  ways to reach them (Telegram, Signal, Discord, WhatsApp, email), and for an SOS and for unanswered alarms what each
  gets: place, photos and sound from the phone, pictures from the Pi, latest events, sound from home, emergency medical
  data as a rescue link with PIN or as a PDF. A test message checks each contact. The old settings keep working.
- Emergency data: medicines as lines of the federal medication plan (BMP) with doses, prescribed or self-medication
  and regular or as needed; a section for over-the-counter products and supplements; organ donation as a choice;
  Markdown in the text fields; phone, email and a linked dashboard account. Read a BMP text from a scanner app, give the
  medicines back as BMP text, save the sheet as PDF or picture, and send it to an emergency contact.
- Kid Protect add-on: filter profiles for children's devices (adult content, gambling, malware and phishing, scams,
  drugs, piracy, social networks, TikTok) through Pi-hole groups, times without internet with templates, pause and
  extra time, and the FRITZ!Box cutting the internet at those times if it is connected.
- *Who is this?* on the People page: the faces of recent person events as cards to swipe, right for yes, left for no
  or a stranger, up to skip, or a name below; with arrow and number keys. Faces swiped once do not come back.
- *Settings → Moving* for a new home, router, internet provider or Pi: a moving file (encrypted backup to the browser),
  the command to add the new Wi-Fi to the Pi, and afterwards checks of what changed (router, addresses, Wi-Fi) and
  what needs a hand: network cameras, FRITZ!Box, Pi-hole as DNS server, the VPN address (taken over with one button),
  blocks by IP address. `scripts/restore.sh` puts a backup or moving file in place on a Pi.
- *Help & tutorial* page with every part of pi-guard and a guided tour, also in the desktop app (F1) and as a quick
  action in the phone app. The assistant starts the tour on "Tutorial" and answers questions about pi-guard from the
  tutorial without an AI provider; `/tutorial` sends it in chats.
- Messages can carry a file (PDF) on Telegram, Signal, Discord, WhatsApp and email.

- Quick actions in the phone app, in the app bar and on the app icon: all fine, call home, away, at home, alarm on and
  off, SOS. Admins only; the icon shortcuts ask for the app lock and are hidden while the app is disguised.
- *Learn at the camera*: 20 seconds in front of a camera, and the Pi keeps up to eight clear, different views of an
  enrolled person in that camera's angle and light.
- *Why recognition works or not* on the People page: faces seen, their size, the best similarity and the variety of
  each person's pictures over 30 days, with what would help most, and the number of pictures per person to set there.
- Reverse proxy add-on with `pgproxy`, written in Rust: reach services behind NAT through a server you choose, like
  frp. The server decides which ports each client may open, tokens are shown once and kept as SHA-256, clients trust
  the server's certificate by fingerprint only, and the client alone chooses where traffic goes. The Pi can be server
  or client, makes the configuration for other machines, starts pgproxy itself, and shows it on the network map.
  `scripts/install-proxy.sh` installs it elsewhere as a service; app releases carry the binaries.
- Talking to the Pi, off by default: a wake phrase ("Hallo lytex", "Hi piguard") recognised offline with Vosk, a GPIO
  button or *Talk to the Pi now*; the answer comes from the assistant and is spoken. Spoken requests never switch
  anything. `pi-guard voice download` fetches the checked speech model. The navigation shows when the Pi listens.
- *Speak* on the Live page says typed text through the speaker.
- Accessibility: visible alerts (glow and vibration) for deaf people, accessibility commands by chat or voice, chat
  answers read aloud, and a *Skip to content* button for keyboards and screen readers.
- *Ask about settings* explains settings, by search or with the AI assistant, which may propose one change shown as
  before and after; nothing changes without a confirmation. *Recent changes* lists every settings change with an undo.
- After an SOS the phone sends its place every minute for 30 minutes, for admins to follow in the dashboard, until
  *I am safe*. Opt-in: photos from both cameras and 20 seconds of sound from the phone, and 20 seconds from the Pi's
  microphone, for the emergency contacts and admins.
- Phones can be reported lost in the dashboard, with the password: the phone shows it and sends its place every
  minute until *Found*, which deletes the track.
- Optional privacy upstream on the Pi: multi-hop exit (custom WireGuard or Mullvad), DNS privacy
  modes, Pi-side kill switch, Tor when installed, privacy profiles and diagnostics under
  Network › VPN. Off by default; `sudo ./scripts/privacy-server.sh install` once. AmneziaWG,
  Shadowsocks and Xray stay capability-gated and disabled on a stock Pi kernel.

### Changed

- Softer light theme with a grey-blue page, lighter cards and at least 4.5:1 for every text colour; the phone app's
  own screens follow the system's light or dark mode in the dashboard's colours.
- The chat's example questions fit what is set up; prompts keep documents as tagged data and never reveal themselves.
- The emergency card opens without another password when logged in; files, sending and deleting still ask.
- The logo can no longer be changed (the phone app can still disguise itself).
- Settings: sections grouped into Basics, Alerts, Devices & access and System, each with a short description, the
  explanation of every setting under its name, toggles for on and off, and a tidier phone list. *Storage*, *Backup*,
  *Moving* and *Export* are now tabs of *Data & backup*, which shows in the normal view too (Export only in the expert
  view); old links open the right tab. The backup notice has a button to the backup settings.
- The assistant's chat is a panel at the bottom right (the whole screen on a phone) with examples to tap, dots while it
  thinks, Markdown in answers, Yes and No as buttons with the answer in the chat, errors in the chat instead of under
  it, a new-conversation button, and one input line with microphone and send.
- Mullvad: choosing a server is a dialog with country, city and server instead of typing a host name, and offers
  Mullvad's own multi-hop with an entry server. The Pi takes keys, addresses and ports from the relay list, not from
  the browser. Linking refuses an account without paid time and shows until when it is paid.
- `scripts/build-desktop.sh` leaves out `.rpm` or `.pacman` when `rpmbuild` or `bsdtar` is missing instead of failing
  the release, and puts `desktop/package.json` back after the build, so `release-apps.sh` finds a clean tree.
- *Network › Overview* shows the state at a glance, the last 24 hours of name lookups, devices without a name, the
  busiest devices and quick ways. *Network › VPN* has a summary on top and its parts (devices as cards, from
  outside, privacy, quantum-safe) one at a time; adding a device opens a dialog.
- Privacy multi-hop chain shows the Pi VPN hop and is animated; diagnostics cache the exit/public IP and explain
  missing status. The network map draws privacy exits, Tor, reverse-proxy peers and VPN traffic via the upstream.
- A face picture nearly identical to a stored one is no longer stored, and over the limit the most similar picture is
  dropped instead of the oldest, so a person's pictures stay varied.
- List settings refuse line breaks, which could have added another line to the settings file.
- Signal only sends alerts by default and reads no message, also not from the owner: it is often linked to a private
  number. `PI_GUARD_SIGNAL_COMMANDS=true` allows commands from its admins again.
- *Home servers* and *Service watcher* are one add-on, *Server*. Whoever had one switched on has both.
- The add-ons page groups its cards, filters by on and off, shows each card's state and what is missing, and keeps
  *Remove* in a menu instead of next to *Open*.
- The normal and expert switch of the settings sits next to the title and the search.
- Verified add-ons show only a check mark; *Verified* appears on hover, focus or tap.
- People are found far more often: YOLOX-Nano replaces the HOG detector once `pi-guard models download` fetched it.
  On 120 motion pictures of a real home, HOG found a person in none, YOLOX in 25. HOG stays for installations without
  the model, and `faces.compare_body_detectors` runs both to compare them on the events page.
- Faces are also searched at full resolution in the head of each person, where they are often too small for the
  smaller copy that was searched before.
- Up to three pictures of a motion are judged together. Before, the first picture decided: a blurred first picture
  ended the check, and a family member seen badly once raised the stranger alarm. Now an unclear face is a stranger
  only if it matches nobody twice; a clear one or one next to a family member still is at once.
- The dashboard shows a placeholder while a page loads, instead of an empty area.
- The dashboard opens faster: it asks for the session once at the start and no longer waits for it when changing pages,
  the overview no longer waits for its two slowest panels, and the events page asks for everything at once.
- The dashboard's files and texts travel compressed, about a third of their size. The files are packed when the
  package is built, so the Pi spends no CPU on it.
- When the disk reaches its limit, the oldest recordings without motion go first, then the oldest with motion or a
  person, and the last few minutes last. Before, age alone decided.
- People are cards with picture and quality, and the network overview starts with the numbers that matter.
- The web dashboard and the phone app are on by default.
- The add-on for connected computers is now called Home servers.
- Settings open in a normal view with what most people need; a switch shows everything for experts.

### Removed

- Rescue links for emergency data (they only opened at home or over the VPN); contacts that got one now get a note.
- The anonymous statistics, for now: nothing is collected or sent, stored figures are deleted, the app no longer asks.

### Fixed

- The multi-hop chain showed only three blue lines once active: the hops stayed invisible and a live link took the
  whole row.
- The rootkit check said "no scanner installed" whenever rkhunter found something; known harmless warnings (Pi-hole's
  tables in `/dev/shm`, the pi-guard user in groups) now count as known.
- The phone app showed "this browser cannot get push"; the dashboard now knows it runs in the app.
- Bars and progress set through style attributes had no width, because the Content Security Policy drops them.
- Updates from the dashboard now also install the root job for the security check and storage, so new fixes arrive
  without running `install.sh`.
- `build-android.sh` (and so `release-apps.sh`) takes Java 17 when `JAVA_HOME` is not set and refuses other
  versions: under Java 25 the Gradle of AmneziaWG's tunnel library failed with only `25.0.5.0.3` as its error.
- `build-android.sh` stops before creating a signing key when there is no Android NDK, so a run on the Pi leaves no
  second key behind.
- The Pi's privacy exit never came up: a bracket in `pi-guard-exit-apply` was written so that no endpoint matched
  (`invalid exit.peer`), and with the kill switch on, VPN devices had no internet. Even when up, traffic of VPN devices
  was not routed into the exit nor masqueraded there; both are added. The kill switch now drops only VPN devices'
  traffic that would leave another way: the home network stays reachable and other forwarding is left alone. Run
  `sudo ./scripts/privacy-server.sh install` again after updating.
- Mullvad answers a wrong account number with 400 `INVALID_ACCOUNT`, which showed as "API request failed"; it now says
  the number was refused. Two Mullvad hops took the entry's multi-hop port when the exit had none, which leads to
  another exit; a multi-hop that cannot be built is now refused instead of becoming a single hop.
- *Network › Overview* offered Kid Protect while the add-on was off.
- Voice control looked up words with a name the vosk package does not have, so switching on the wake phrase failed.
- Taking pictures on a phone held upright cut the picture to a landscape strip, so the face seemed zoomed in and hardly
  fit the oval. The picture now keeps the camera's own shape, and the camera uses its widest zoom.
- The alarm plays as one stream for its whole time. Starting the sound again for each round left gaps, and the USB
  speaker could mute in them, so the alarm seemed to stop after a moment. The log now says when it started and ended.
- Signal no longer answers people who are not set up. It is often linked to the owner's own number, so friends who wrote
  to the owner got "not allowed" from the bot.
- Renaming a trusted person took effect for recognition only after a restart; until then it reported the old name.
- A computer whose VPN gets no answer keeps its tunnel instead of replacing the interface every 12 seconds, which
  looked like a new network to the system and every program on it. Other ways and keys are now set on the running
  tunnel.
- After a restart of the computer the VPN only comes back with auto-connect. Before, it came back whenever it had been
  on, and the kill switch then blocked the internet.
- The VPN page says when the Pi does not answer for a minute, usually because the computer was removed there.
- `install-vpn.sh` warns about enabled `wg-quick@` services, which installing `wireguard-tools` can wake up.
- A new installation with `install.sh` gets a random first password instead of `admin123`, which anyone in the network
  could have tried first. `pi-guard dashboard reset-password` chooses one too. Docker keeps `admin123` unless
  `PI_GUARD_DASHBOARD_FIRST_PASSWORD` is set.
- The phone app opens only its own address inside the app. A link like `https://pi:8443@example.org` used to pass.
- Someone removed from the dashboard could go on watching with a paired phone or computer. Their devices, logins and VPN
  access now go with them, and devices of people removed earlier are refused. The same goes for a deleted key file.
- Adding a passkey or hardware key, an API token or a key file now asks for the password, so a stolen session cannot
  turn into lasting access. A passkey logs in as the owner, so it takes the owner's password.
- Viewers no longer get a VPN access for their phone or computer: it reaches the whole home network, and they may only
  watch. A person changed to viewer loses theirs at once. Accesses viewers already have stay until you remove them under
  Network › VPN. From outside, viewers need a public address for the dashboard.
- A new password ends all other logins of that person. A stolen session used to survive it, for up to a week.
- Devices paired by the owner stay the owner's after the user name changes. Before, they fell back to viewer.
- Leaving a dashboard page while it was still loading could leave its timer running, or bring the old page back over the
  new one. Storage, language downloads and updates also kept asking the Pi after the page was closed.
- A late answer can no longer show the previous mode on the overview for a moment.
- The dashboard goes to the login page as soon as it notices that the session has ended.

### Added

- The phone app asks at setup, and later in its settings, whether to send crash reports and anonymous usage data;
  both off by default. Crash reports hold only the exception type and code places. They go only to the user's own
  pi-guard, which passes them on in its anonymous report only if its owner agreed too (report version 2).
- docs/mobile.md explains using the app with your own pi-guard: the official APK with updates from GitHub, an
  uploaded one, or one built from the source.
- Emergency data add-on: medical information of the people at home, encrypted in its own database, seen and changed
  only by admins with their password each time, every access audited. A rescue link shows one person's sheet for 15 to
  60 minutes, by default only with a six-digit PIN that five wrong tries end; it puts allergies, illnesses, medicines
  and implants first and can be printed.
- Backups to Nextcloud, over rsync or into Borg, daily or weekly. Configuration, databases (copied with SQLite's backup),
  keys and face prints leave the Pi only encrypted with the backup passphrase (AES-256-GCM in sealed pieces, so a cut or
  changed backup does not decrypt). `pi-guard backup restore` unpacks into a folder without replacing anything.
- The agent looks after its computer: system information, a security check (firewall, SSH, open ports, failed logins,
  waiting updates), installing updates, pulling Docker Compose projects and Git repositories, and logs. Reading is always
  allowed; changes need their switch in `agent.conf` on that computer and the dashboard password, and run one at a time
  in the background. Git runs without hooks. Agent 0.4.0.
- The live picture can show its frame rate and mark people and things, with their names and how sure YOLOX is. The Pi
  checks at most one picture per second for this, and only while it is switched on.
- Evidence videos come with a signed record and a readable report: clock synchronisation, the video's own data, the
  events in that time, device model and serial, and checksums of the video and of every segment it was cut from.
  `pi-guard evidence verify` checks a copy.
- Logging in with a code by messenger or email (`code` in `PI_GUARD_DASHBOARD_AUTH`): to the owner's admin accounts in
  Signal, Discord, WhatsApp, Telegram, the phone app or a chat app, or to the account's own email address. Only with a
  code word or two-factor codes, which are asked after it.
- Security questions, free answer or multiple choice with a hint, asked in addition to the code word at login.
- Logging in with an OpenPGP signature, checked by GnuPG against the added keys only.
- Contact details for dashboard accounts, with vCard in and out, also in the data export.
- *Place link* for a phone: asks it for its place now, without ringing, and shows a link with its accuracy to open,
  copy or share. The map is a setting: OpenStreetMap, Google Maps, Apple Maps, Bing, HERE or the device's map app.
- Alerts have buttons: *All fine* and *Sound alarm*, under each alert in the phone app and in browser push
  notifications, for admins. A push notification carries a token for one tap that is valid for an hour.
- The phone app asks for its PIN on a number pad with dots, and a new PIN is typed twice.
- Taking pictures for face recognition shows a small head with where to look at each step.
- Apps update from GitHub: `scripts/release-apps.sh` builds, signs and publishes the phone app, desktop app and agent as
  one release, and every pi-guard fetches new signed versions every 6 hours for its devices. Files whose signature,
  checksum or version do not match are refused. The keys stay on the owner's computer, so a broken-into GitHub account
  cannot sign anything.
- The code word can have a hint, shown at login after the password.
- Expert view: a test message through every messenger that is set up, not only Discord, email and Signal.
- Person events in the dashboard say who was recognised, or why nobody could be named.
- `training/` with `pgtrain`, for a computer with an NVIDIA GPU. It judges photos with the Pi's own code and models:
  is this me, similar photos in a folder, and where the face thresholds should be. It teaches YOLOX-Nano, the Pi's
  person detector, from YOLOX-L on any folder of pictures without labels, on several GPUs, and exports a file the Pi
  loads as it is. On public COCO pictures it did not beat the official model; on one home camera it found 0.82 of the people instead of 0.56, on days it had not seen. A page on `127.0.0.1` does the face part. Pictures stay on that computer. See `docs/training.md`.
- Anonymous statistics, off by default: once an admin switches them on and a receiver is set, the Pi sends counts
  and histograms of the last week, above all how similar faces were that an admin confirmed or corrected. The
  dashboard shows the exact report first. Never pictures, face prints, names or times. `pgtrain reports receive`
  collects them, and `pgtrain faces calibrate --reports` uses them.
- Person events keep the face sizes and similarity scores as numbers, for the statistics.
- *Who is this?* on a person event in the dashboard: say which enrolled person a face is, and pi-guard learns the
  camera's angle and light; or say that a recognised face is not that person, and similar faces stop counting as them.
  Only people already enrolled can be chosen, only clear faces of at least 56 pixels are learned, and only the
  numbers of a face are stored.
- Pi updates are signed. The update button and `scripts/update.sh` install only release tags signed with a key that stays
  on your computer, so someone who gets into the GitHub account cannot run anything as root on a Pi. Until you make the
  key with `scripts/release.sh key` and install once by hand, the button reports that there is no key. See
  [Updates](docs/updates.md#updating-the-pi).
- The agent reads its certificate with rustls itself; `rustls-pemfile` is no longer maintained.
- The log names dashboard requests that take longer than half a second.
- Quantum-resistant VPN on phones and on Windows too: one key exchange in Rust (`vpn/kem`) with ML-KEM-1024 and HQC-256
  for the Pi, computers and phones, inside the tunnel. Computers use it instead of Rosenpass; the Pi keeps Rosenpass
  until every device has updated. `vpn-server.sh install` must run once more on the Pi.
- Adding a trusted person explains what works best and can take guided photos or a video on the spot, in
  the browser and in the app.
- The app creates its own VPN access when a phone is paired and has a VPN screen behind the shield at the top:
  one button, device, address, data, last contact, reconnects after a restart, works as always-on VPN.
- Four ways home from outside, shown with their state under Network › VPN: IPv4, IPv6, a rented server and
  Tailscale. The app tries them in turn and finds the dashboard at other addresses. `scripts/relay.sh` sets up a
  server that passes the VPN on without being able to read it, optionally with the dashboard through Caddy.
- Agents, desktop apps and the phone app update themselves from your pi-guard. Agents and desktop apps only accept
  releases signed with a key that never comes to the Pi, and never an older version. The phone app needs no tap from the
  second update on (Android 12 and newer).
- The phone app can look like a calculator, notes and more, and can get a shortcut with any name and picture. The
  dashboard's name can be changed under Appearance, also for installed apps.
- VPN in the desktop app on Linux, by a small Rust service: kill switch and lockdown that stay closed if the service
  crashes, split tunneling per program, auto-connect, all ways home.
- Quantum-resistant tunnel with Rosenpass through the VPN itself, on by default, switchable under Network › VPN.
- Windows preview of the desktop VPN, driving the official WireGuard client with its kill switch. Not yet tested on Windows.
- The desktop app as a Flatpak.
- VPN in the phone app: split tunneling per app and connecting automatically.
- Desktop packages for Arch Linux, Debian, Ubuntu, Linux Mint, Fedora and openSUSE.
- Add-on for Slack, Matrix, Rocket.Chat and Mastodon: alerts as cards, send-only and without pictures.
- Add-ons can be removed with their data, which shows how much space that frees.
- The assistant shows its use per day and month, in euros or dollars once you enter prices, and what is left of a budget.
- Docker containers on home servers can be removed.
- The desktop app uses Electron 45, which closes the reported security advisories; `npm audit` is clean.
- Desktop app for Windows, Linux and macOS (`desktop/`, `scripts/build-desktop.sh`): the dashboard in its own window,
  trusting only your pi-guard's certificate authority.
- Connect calendars: besides the phone's calendar, any calendar with an iCal link such as Google, Proton or Nextcloud.
  Repeats and time zones are worked out, and only start and end of busy times are stored.
- Trusted people can have a picture, birthday, address, email and phone number, all optional.
- Each kind of notification can go to chosen ways only, for example only Signal, or Telegram and the app.
- Keep everything for some hours and after that only what is around events (`PI_GUARD_RECORDING_FULL_HOURS`).
- Storage shows what the cameras write per day, how long the space lasts and how to save some; recording quality can
  be set per camera.
- An emergency from the app now comes with a current picture of each camera (up to four). If a picture fails,
  the words still go out.
- Discord messages are embeds with a title, a colour by urgency and the picture inside.
- The Signal page can send a test message, and `pi-guard signal test` really sends one.
- Linking Signal shows steps and the time left, and stores the number from the phone.
- A new login page with a way to show the password.
- Android app PiGuard (`android/`) with the add-on `mobile`: dashboard, alerts without Google, WireGuard
  client, find a phone, SOS, app lock, signed updates, calendar as away times.
- Call a camera from the Live page, also in the browser.
- Modules for Discord, WhatsApp and email that can be switched off, with a Verified badge.
- Email add-on: alerts and data exports, off by default.
- Plugins from a Git link can be checked and updated, also automatically if chosen, following
  `release_branch` from plugin.json or the main branch.
- Settings panels fold; your own add-ons open with the plus beside the search.
- Optional donations under Support.
- A bar shows how well a trusted person is learned and which pictures would help; slightly turned
  faces are accepted now. Viewing direction and brightness are stored with each sample.
- FRITZ!Box user name in the settings.
- Brand icons for messengers and services, and a choice of AI providers with their icons.
- Settings search looks through all tabs and jumps to the setting.
- Login methods and time zone are chosen from lists instead of typed.
- Discord: a bot with commands, buttons, photos, videos and alerts in direct messages.
- Speakers and microphones can be tried out when choosing them.
- Security scan with an optional report by messenger, and a storage analysis with clean-up. System
  checks run in a new root job, `pi-guard-maint`.
- Weekly plan: mark when nobody is usually home, in a calendar you can draw in.
- Wi-Fi QR codes, internet block per device and a new Wi-Fi key through the FRITZ!Box.
- "What others can see of you" on the network page.
- Tooltips, navigation order by drag and drop, camera room as a switch on the Live page.
- One field per contact instead of comma lists, search behind an icon, the map opens large.
- Support contact in the overview, settings, logs and the assistant.
- Signal: `scripts/signal.sh`, linking the phone by QR code, and Note to Self with your own number.
- Several dashboard users with the roles admin and viewer. Viewers only watch; the server enforces
  this with an allowlist. Everyone has their own password and two-factor code.
- Appearance settings: accent colour, font, animations and your own logo, also in the setup.
- Signal in Settings > Messenger and in the setup.
- An icon for the AI assistant.
- `scripts/dashboard-name.sh` gives the dashboard a name through Pi-hole, for hardware keys.
- Guide for hardware keys under Access. A failed attempt now shows the browser's own error.
- Camera room in the tutorial.
- Connected computers: a small agent in Rust (`agent/`) connects out to pi-guard, with status,
  safe shutdown confirmed twice, and Docker management. Shown on the map.
- Add-on ecosystem: manifest with name, icon, version, source and authors; settings forms,
  dashboard panels, events and storage for plugins; install from Git; `pi-guard plugin new`,
  `check` and `pack`.
- Export of all data as JSON, TXT and CSV; config.json to back up and read in settings.
- More languages through DeepL.
- Pictures open large on click; icons on the map; VPN form opens on demand and devices show
  details; link in the credit.

### Fixed

- VPN on the computer: if the kernel refused the split tunneling rule, it took the kill switch rules down with it,
  because the kernel loads rules all at once. Now split tunneling gives way, the kill switch stays, and the app says
  why. This happened after a kernel update without a restart. The modules the VPN needs now load at boot, so such an
  update no longer gets in the way.
- In the first hour after a restart, unknown senders and emergency contacts got no answer: a limit took the time since
  boot for the time since the last answer.
- In the desktop app, everything that asked a question did nothing: clearing storage, renaming, and every password
  confirmation. It relied on a browser feature the app does not have. A dialog of its own replaces it, which also no
  longer shows passwords in plain text.
- Root jobs no longer write into folders of the unprivileged user: the WireGuard status and the update report could
  have been turned into overwriting any file, by someone who already broke into pi-guard.
- Voice messages to the assistant and talking through the speaker were cut off after the first part of the upload.
- Speech input of the assistant was often nonsense: the language was not given, so short sentences were guessed as
  anything, and silence came out as imagined subtitles. It now names the language, uses a better model with OpenAI, and
  records with noise suppression.
- Changes to notification rules take effect at once, as the page says. They only did after a restart.
- A home server connecting by a local name such as `lytex.system` was refused, because the certificate did not list it.
  Creating a home server now adds the name in use to the certificate.
- Linking Signal now shows success right away. Waiting on a busy signal-cli kept the code on screen, and a busy
  signal-cli was reported as not installed.
- The map of a phone failed with "403 Access blocked": OpenStreetMap refuses browsers without a referrer. Tiles now
  come through the Pi, which also keeps OpenStreetMap from seeing who looks at the map.
- In the More menu a row opens from everywhere, also from the arrow.
- `pi-guard` commands prefer the settings of the service over a `.env` in the current folder, and say so if they
  may not read them. The live picture may go up to 30 frames per second.
- The app's VPN button did nothing when Android refused it, for example with another VPN set to always-on. It now
  says why and leads to the settings. Two phones with the same long name no longer share a VPN name.
- The Signal link code vanished after four seconds and was never renewed, although a code only works for
  two minutes.
- Phones no longer scroll sideways: pages were wider than the screen, the network pages on every phone.
- Borders were missing everywhere, because the line spacing setting overwrote their colour.
- Settings are a list on a phone, tables stack, and tabs wrap, so there is no sideways strip left.
- Icons are part of the page instead of one request per use.
- App: Tabler icons, Material design and an adaptive launcher icon on all native screens.
- Signal no longer crashes in a loop while signal-cli is not running.
- `scripts/signal.sh` installs the native library signal-cli needs on a Raspberry Pi.
- A refused Discord token is shown in the dashboard instead of restarting in a loop.
- The lines of the network map were almost invisible.

- Updater: from the dashboard or `scripts/update.sh`, fast-forward only, with health check and
  automatic return to the previous version.
- Network management: Wi-Fi networks and device names from the FRITZ!Box, own device names,
  recognition by domains, blocking websites per device and for a while, block page.
- Doing things by asking, with confirmation: smart home, remote commands, video, VPN access,
  planned sound recordings; chat widget with voice; AI may look at camera pictures if allowed.
- Camera room with all cameras at once.
- API with tokens (`/api/v1`) and OpenAPI description.
- Own add-ons in the dashboard: ZIP upload with plugin.json, create, edit, delete, search, pin.
- Settings in one page with sections, search and save bar; recovery of what is stored per person;
  arrows on the map; links in security recommendations; back to top.

- Login with Telegram (number comparison), passkeys and hardware keys (WebAuthn), TOTP
  recovery codes and an optional code word.
- Dashboard behind a domain and reverse proxy: public URL, host name check, secure cookies.
- Security analysis under Logs; interactive network map; redesigned overview.
- Accessibility panel: text size and spacing, colours, colour blindness filters, reading
  aloud, large pointer, reading ruler, magnifier, tutorial.
- Notification switches per kind of message, effective at once.
- Cameras by RTSP, HTTP/MJPEG (ESP32-CAM) and the Raspberry Pi camera module; rooms.
- Evidence videos with sound, metadata and SHA-256 (`/beweis`).
- Device details (kind, system, open services) and Pi-hole block lists in the dashboard.
- Built-in sounds can be replaced, renamed, deleted and restored.
- AI assistant: Claude, Gemini, Grok and Ollama; knowledge from TXT, MD, JSON and PDF;
  Paperless-ngx and Nextcloud; stored answers to frequent questions.
- Plugin API; `scripts/mqtt.sh` provides a broker.

### Fixed

- Speech through the speaker was inaudible: hardware mixer at -24 dB and quiet recordings.
- The link to Pi-hole used 127.0.0.1 instead of the address of the Pi.

- Add-ons, chosen in the setup assistant and managed in the dashboard, with commands
  in every messenger: service watcher, network analysis (from Pi-hole), smart home
  over MQTT with Home Assistant discovery, remote commands over SSH with two
  confirmations, and an AI assistant (OpenAI, Mistral or any compatible API, DeepL,
  Open-Meteo) with cache, short prompts and a daily token limit.
- WhatsApp through the official Cloud API.
- Overview shows how you are connected: home network, home VPN (with the device's
  name), Tor, another VPN, and the public addresses involved.
- A logo.

- WireGuard VPN managed in the dashboard: `scripts/wireguard.sh setup` prepares
  the server once; devices (family phones, laptops) are added with a name and
  a QR code for the WireGuard app, shown again or removed later. A root path
  unit loads them after strict validation; pi-guard itself stays unprivileged.

- `PI_GUARD_PRESENCE_AUTO_HOME` / `PI_GUARD_PRESENCE_AUTO_AWAY`: limit which way
  presence detection switches the mode; an arriving trusted device can trigger
  an "I am at home" prompt instead of silencing alerts.
- Trusted people: enrollment sessions with photo series, videos and pictures
  taken by the camera, enrollment from alerts, consent before storing;
  `pi-guard faces enroll` accepts videos.
- `/sharper`, `/zoom` (nine areas) and `/brighter`; photo buttons for them;
  `/photo all`.
- `/alarm` with confirmation and stop; uploaded audio as alarm sound; Alarm and
  Listen buttons for admins.
- Everyday sentences in private chats, with buttons when unclear.
- Credit "made by lytex" in help, `pi-guard version` and the startup log.

- Optional web dashboard (see docs/dashboard.md) with password/key-file
  login, optional TOTP, local HTTPS certificate authority, CSRF protection,
  rate limits, bans, honeypots and noindex; live view, listening,
  push-to-talk, image tools, trusted people, sounds and alarm, device
  selection, settings, users, key files, sessions, statistics and logs;
  installable PWA with optional Web Push.
- Emergency contacts for alerts that stay unconfirmed; "Everything is fine"
  button and `ok` command.
- Pi-hole v6 integration (statistics, pause, LAN devices, presence source),
  WireGuard/PiVPN status, `scripts/network.sh` for a fixed address with
  automatic rollback, Pi-hole Docker profile.

- Dashboard login starts as admin / admin123 and must be changed at the first
  login; two-factor codes are set up with a QR code in the dashboard;
  `PI_GUARD_DASHBOARD_SECRET_KEY` peppers the password hash and encrypts the
  stored TOTP secret; `pi-guard dashboard reset-password`.
- Setup assistant in the dashboard (language, login, devices, Telegram,
  alerts, emergency contacts, trusted people with model download, design);
  light, high-contrast and large-text designs; Telegram can be set up in the
  dashboard after a start without bot token.

### Removed

- `PI_GUARD_DASHBOARD_USERNAME`, `…_PASSWORD`, `…_PASSWORD_HASH`, `…_TOTP_SECRET`
  and `pi-guard dashboard password`/`totp`: the login now lives in the
  database and is managed in the dashboard.

### Fixed

- Upload hardening described in an earlier commit was missing in
  `audio/service.py`: uploads are now checked by file signature and FFmpeg only
  reads local files. Stopping playback kills FFmpeg.
- Files too large to download got "I did not understand" instead of a clear
  message.

## [0.1.0] - unreleased

First release.

### Added

- Continuous recording to fragmented MP4 segments from USB (V4L2) cameras,
  using camera H.264, the Raspberry Pi hardware encoder or libx264; one FFmpeg
  process per camera also provides motion frames and snapshots.
- Retention by age, event-only recording, buffer-only cameras and deletion of
  the oldest recordings under disk pressure.
- Camera watchdog with automatic reconnection and disconnect/reconnect alerts.
- Motion detection with zones, schedules, sensitivity, minimum area and
  duration, and light-change suppression.
- Optional person detection (HOG, YuNet) and opt-in face recognition (SFace)
  with consent confirmation and a separate biometric store.
- HOME/AWAY modes with timed manual overrides and automatic presence detection
  (neighbour table, FRITZ!Box TR-064) that reports UNKNOWN instead of guessing.
- Alert rules per event type: modes, priority, recipients, cooldown, quiet
  hours, attachments, hourly cap, retries.
- Telegram bot with Simple and Advanced views, English and German, aliases,
  invitations and roles; optional Signal via signal-cli.
- Optional audio: bounded microphone clips, speaker playback of built-in,
  saved and uploaded sounds, volume limit, quiet hours.
- Optional MQTT bridge with Home Assistant discovery.
- CLI: `doctor`, `status`, `health`, `cameras`, `mode`, `logs`, `config`,
  `audio`, `telegram`, `signal`, `presence`, `models`, `faces`.
- Native installer, update and uninstall scripts, hardened systemd unit,
  optional Docker Compose deployment.

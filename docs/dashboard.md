# Web dashboard (optional)

The dashboard runs inside pi-guard and is off by default. It is meant for the
home network or for access through a VPN (WireGuard) — never expose it to the
internet with a port forwarding.

## Enable

The dashboard and the phone app are on by default, and the installer sets them up. To switch them off:

```dotenv
PI_GUARD_DASHBOARD_ENABLED=false
PI_GUARD_MOBILE_ENABLED=false
```

Restart pi-guard and open `https://<pi-address>:8443`. Telegram does not need
to be configured beforehand: with the dashboard enabled, pi-guard starts
without a bot token and Telegram can be set up in the assistant.

## First login and setup assistant

The first login is **admin / admin123**. Before anything else works, the
dashboard asks for your own user name and password (at least 10 characters,
not the default). It then offers two-factor codes: scan the QR code with an
authenticator app (Google Authenticator, Aegis, 2FAS, …) and confirm one code.

The setup assistant continues with — every step can be skipped and opened
again later under *More → Setup assistant*:

1. Welcome and language
2. Login and two-factor codes
3. Cameras, microphone and speaker (selection fields)
4. Telegram: bot token, administrator and viewer IDs, group chats
5. Alerts and modes: alerts also while at home, video instead of photo,
   quiet hours, presence detection
6. Emergency contacts
7. Trusted people: switch recognition on (the two model files are only
   downloaded after you press the button, with size and source shown), then
   enroll people
8. Design: dark, light or high contrast, normal or large text (per device)
9. Finish: restart pi-guard if settings changed

Until the default password has been changed, the default login is the only
way in; keep the dashboard off the internet until then. While pi-guard still
uses it, the log says so at every start.

## Login methods

`PI_GUARD_DASHBOARD_AUTH` takes one or both methods:

| Method | How |
|---|---|
| `password` | the dashboard account (default `admin` / `admin123`, changed at first login) |
| `keyfile` | Choose a key file instead of typing a password. Create one under *Access* or with `pi-guard dashboard keyfile tablet -o tablet.key`. Only a hash is stored; delete the key there if a device is lost. |
| `none` | No login. Only allowed on `127.0.0.1`, or with `PI_GUARD_DASHBOARD_ALLOW_UNAUTHENTICATED=true`. |

Two-factor codes apply to both methods. Change the login or switch codes on
and off later under *Access → Your account*.

### More users

*Settings → Access → Dashboard users* adds people with their own password and role:

| Role | Can do |
|---|---|
| Admin | everything |
| Viewer | see the overview, live picture, camera room and events, and change their own password and two-factor code |

Viewers cannot listen in, talk, take photos, change the mode, use the assistant or open
settings. The server enforces this with an allowlist, so new features stay closed to viewers
until they are added to it. A changed role counts at once; removing a person ends their
sessions and removes their phones, computers and VPN access. Hardware keys, recovery codes and the code word belong to the main account.

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

Discord, WhatsApp and email are modules under *Add-ons*. A module that is off disappears from *Settings →
Messenger* and stops at the next restart; its entries are kept. Modules that are part of pi-guard carry a
*Verified* badge. The plus beside the search adds your own add-ons, see [plugins](plugins.md).

Panels in the settings fold by clicking their headline, and stay that way in this browser. Messengers that
are off start folded.

## Trusted people

The form explains what makes recognition work. **Take photos or a video now** opens the camera of the
device you are using and guides the person through front and turned views, or films 8 seconds without sound.
Tap a picture to remove it; nothing leaves the page before **Upload**. This needs the HTTPS address and your
permission for the camera; otherwise the page offers the phone's camera app. You can also choose existing
files or use a camera at home.

A bar shows how well each person is learned, with the pictures per viewing direction and the next
useful step. The score is an estimate from the number of pictures, front and turned views, light and how
well the pictures agree; look-alikes in the family lower it. *Test with a photo* shows the real result.

Faces turned a little to each side help, because people rarely look into a camera. Both eyes must stay
visible: the face models cannot use a pure side profile. Pictures enrolled earlier count as front views.

## Everyday tools

- **Trying out devices:** under *Devices & cameras*, **Test speaker** plays three tones on the chosen
  speaker. **Test microphone** records 3 seconds for you to hear and shows how loud it was. The sample is
  not kept; the test is logged and announced with the chime, like listening in.

- **Tooltips** explain buttons and entries on hover and keyboard focus.
- **Navigation:** *Appearance → Navigation* sets your own order by dragging, per account. The camera
  room is a switch at the top of Live.
- **Weekly plan** (*Settings → Home & Away*): drag in a day to mark when nobody is usually home. pi-guard switches
  to Away and back at those times. What you set by hand stays until the next change in the plan. If
  presence detection is on, it stays at Home while someone is detected at home.
- **Support** (*Settings*): optional name, phone, email and address, shown small on the overview, in
  settings, logs and the assistant. The assistant answers with them when asked for help.

## Network tools

- **Wi-Fi QR codes:** the code button beside each Wi-Fi reads its key from the FRITZ!Box. The code
  can be saved as a picture; anyone who has it can join. Without a FRITZ!Box, type the name and key.
  The key is not stored.
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

*Settings → Security scan* checks the login, HTTPS, pi-guard's own files against their checksums, open
ports, system updates, the firewall, SSH, system files against their packages, unknown programs with
root rights, programs in temporary folders, and accounts. It changes nothing and can send a summary to
admins by messenger (also `/securityscan` in chat). A rootkit scan runs if `rkhunter` or `chkrootkit`
is installed. A clean result is no guarantee that a system is not compromised.

*Settings → Storage* shows what uses the disk and can delete old recordings, photos and clips, compact
the database, and clear old system logs and the package cache (also `/storage` in chat). Evidence videos
and face data are not cleaned there.

pi-guard has no root rights. The system checks come from `pi-guard-maint`, a root job started by a
systemd path unit. It accepts only four fixed words and writes its reports to `/var/lib/pi-guard-scan`,
where pi-guard cannot write.

## If the dashboard feels slow

Open *Logs* and look for `slow request:`. Every answer that took half a second or more is listed there with its route,
and the assistant's answers appear because they take a while anyway. More in [Performance](performance.md).

## Appearance

*Settings → Appearance* sets the accent colour, font, animations and your own logo (PNG, JPEG or
WebP up to 1 MB; no SVG, because it can contain scripts) for everyone, also on the login page.
Theme and text size are chosen per device. No fonts are loaded from the internet: the choices use
what the device has. The high-contrast theme keeps its own colours.

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

resets the login to admin / admin123 (to be changed at the next login), turns
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

## Normal and expert view

Settings open in the normal view with what most people need. The switch at the top shows everything, also the complex
settings and the sections Integrations, Dashboard, Security and Data. It is remembered per device and works the same
in the phone app. Nothing is lost by switching: hidden settings keep their values.

## Where notifications go

Under Settings › Notifications every kind of message has its own ways: tap *All ways* below it and pick, for example,
only Signal, or Telegram and the PiGuard app. Changes work at once.

## Install as an app (PWA) and push notifications

- **Android / Chrome / Edge:** browser menu → *Install app*.
- **iPhone/iPad (iOS 16.4+):** Share → *Add to Home Screen*. Push
  notifications only work in the installed app.

Enable push with `PI_GUARD_WEBPUSH_ENABLED=true` (and a contact address in
`PI_GUARD_WEBPUSH_CONTACT`), then on each device: *More → This app → Receive
alerts on this device*. Push messages carry the alert text only, end-to-end
encrypted, and are delivered through the browser vendor's push service
(Google, Apple or Mozilla) — see [privacy.md](privacy.md).

## What it offers

| Page | |
|---|---|
| Overview | mode (home/away/automatic), cameras, open alerts with *Everything is fine*, storage, memory, temperature, the last 24 hours |
| Live | live picture (pinch, mouse wheel or buttons to zoom, full screen), photo, *Sharper*, zoom into nine areas and *Brighter* on the taken picture, listen live, hold-to-talk through the speaker, sounds, alarm, volume |
| Events | filter by type and camera, thumbnails, movement per day and by time of day |
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
`PI_GUARD_DASHBOARD_TLS_NAMES=192.168.178.10,raspberrypi.local`. Set `PI_GUARD_DASHBOARD_ENABLED=true` and the password hash in
`.env`.

## Later

A native app with built-in WireGuard is a possible next step; the dashboard's
JSON API is the interface it would use.

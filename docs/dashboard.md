# Web dashboard (optional)

The dashboard runs inside pi-guard and is off by default. It is meant for the
home network or for access through a VPN (WireGuard) — never expose it to the
internet with a port forwarding.

## Enable

```dotenv
PI_GUARD_DASHBOARD_ENABLED=true
PI_GUARD_DASHBOARD_SECRET_KEY=<openssl rand -hex 32>
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
and off later under *Access → Your account*. All dashboard logins have full
administrator rights.

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
| Access | Telegram/Signal users and invitations, key files, active sessions, blocked addresses, certificate download |
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

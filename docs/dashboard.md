# Web dashboard (optional)

The dashboard runs inside pi-guard and is off by default. It is meant for the
home network or for access through a VPN (WireGuard) — never expose it to the
internet with a port forwarding.

## Enable

```bash
pi-guard dashboard password          # prints PI_GUARD_DASHBOARD_PASSWORD_HASH=…
```

In `pi-guard.env`:

```dotenv
PI_GUARD_DASHBOARD_ENABLED=true
PI_GUARD_DASHBOARD_PASSWORD_HASH=scrypt$…
```

Restart pi-guard and open `https://<pi-address>:8443`. The installer can do
this for you.

## Login

`PI_GUARD_DASHBOARD_AUTH` takes one or both methods:

| Method | How |
|---|---|
| `password` | `PI_GUARD_DASHBOARD_USERNAME` (default `admin`) and the scrypt hash from `pi-guard dashboard password`. A plain `PI_GUARD_DASHBOARD_PASSWORD` also works but is not recommended. |
| `keyfile` | Choose a key file instead of typing a password. Create one with `pi-guard dashboard keyfile tablet -o tablet.key` or in the dashboard under *Access*. Only a hash is stored; delete the key there if a device is lost. |
| `none` | No login. Only allowed on `127.0.0.1`, or with `PI_GUARD_DASHBOARD_ALLOW_UNAUTHENTICATED=true`. |

Optional two-factor login for both methods:

```bash
pi-guard dashboard totp     # shows a QR code for an authenticator app
```

and set `PI_GUARD_DASHBOARD_TOTP_SECRET`. Each code is accepted only once.

All dashboard logins have full administrator rights.

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
| Settings | the non-secret `.env` settings, grouped |
| Access | Telegram/Signal users and invitations, key files, active sessions, blocked addresses, certificate download |
| Logs | service log and audit trail |

German and English are switched under *More*.

Settings saved in the dashboard are written to `DATA_DIR/dashboard.env` and
`DATA_DIR/dashboard.yaml` (the service may not write to `/etc/pi-guard`). They
take precedence over `pi-guard.env` and `config.yaml`. Delete those files to
return to the files in `/etc/pi-guard`. Most changes need a restart, which the
dashboard offers; recording pauses for a few seconds.

Secrets (bot token, passwords) can only be changed in `pi-guard.env`.

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
`data` volume. Set `PI_GUARD_DASHBOARD_ENABLED=true` and the password hash in
`.env`.

## Later

A native app with built-in WireGuard is a possible next step; the dashboard's
JSON API is the interface it would use.

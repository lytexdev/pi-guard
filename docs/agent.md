# Home servers

The pi-guard agent connects a home server, NAS or any other computer to pi-guard: see its status, shut it down safely, and
manage its Docker containers, on the dashboard and on the network map. It is a single small
program written in Rust (1.5 MB).

Like an frp client, the agent connects **out** to your Pi. Nothing has to be opened on the
computer, and it works over the home VPN too.

## Setup

1. Dashboard › **Add-ons** › switch on *Home servers*, then restart pi-guard. Open the dashboard by the name the
   home server should use: creating it adds that name to the certificate.
2. Add the computer and copy the command shown. The token appears once.
3. On the computer, with Rust installed (Arch: `sudo pacman -S rust`):

   ```bash
   git clone https://github.com/lytexdev/pi-guard && cd pi-guard/agent
   cargo build --release
   sudo ./install.sh https://192.168.178.109:8443 pga_...
   ```

The script fetches the certificate authority of your pi-guard once and shows its fingerprint.
Compare it on the Pi with `sudo openssl x509 -in /var/lib/pi-guard/tls/ca.crt -noout -fingerprint -sha256`.
After that the agent trusts only this authority, so nobody can pose as your Pi.

## What it can do

| Command | Notes |
| --- | --- |
| Status | System, memory, disk, load, every 30 seconds |
| Shut down, restart, sleep, lock | Two confirmations by the same person, as for remote commands; also "Fahre meinen PC herunter" |
| Docker | List, start, stop, restart, logs |

To forbid something, edit `/etc/pi-guard-agent/agent.conf` and restart the service:

```ini
ALLOW_POWER=false
ALLOW_DOCKER=false
```

## Security

The agent understands only the commands above. **There is no way to run an arbitrary command**:
even someone who took over the Pi gets no shell on the computer. They could, however, shut it
down or stop containers, so switch off what you do not need.

The agent runs as root, because shutting down and Docker need it. Each computer has its own
token, stored on the Pi as a hash; removing a computer in the dashboard makes it invalid. Wrong
tokens count as failed logins. Shutting down protects a disk only if it is encrypted.

## Why Rust here, and not elsewhere

The agent runs on other people's machines, so one file without Python or dependencies matters.
In the rest of pi-guard the heavy work already happens in FFmpeg, OpenCV and SQLite, and
measurements showed nothing that a rewrite would speed up noticeably.

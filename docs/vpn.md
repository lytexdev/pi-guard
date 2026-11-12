# VPN

The VPN connects phones and computers to your home, like a commercial VPN app, but your Pi is the server.

| | Android | Desktop app (Linux) | Desktop app (Windows) | macOS |
| --- | --- | --- | --- | --- |
| One-button connect, own access | yes | yes | yes | not yet |
| Tries all [ways home](network.md#ways-home-from-outside) | yes | yes | yes | |
| Auto-connect | when pi-guard is out of reach | at start | at start | |
| Kill switch | through Android | yes | always on, by WireGuard | |
| Lockdown | through Android | yes | no | |
| Split tunneling | per app | per program | no | |
| Quantum-resistant tunnel | yes | yes | yes | |
| Disguised way (AmneziaWG) | yes, from 1.8 | not yet | not yet | |

## Privacy upstream (optional)

Devices still connect to your Pi. On the Pi you can optionally send that traffic onward through another
hop before it reaches the internet: a custom WireGuard server, Mullvad, or Tor.

```text
Device  →  Pi (home WireGuard)  →  optional exit  →  Internet
```

Everything stays **off** until you turn it on. Install once as root (does not install Tor or commercial VPN packages):

```bash
sudo ./scripts/privacy-server.sh install
```

Then open **Network › VPN** in the dashboard: profiles, multi-hop chain, providers, DNS, Pi-side kill switch, Tor and
diagnostics. The chain always shows the home VPN hop on the Pi first (devices → Pi-Guard → optional exits → internet).
**Check** under Diagnostics asks for the public/exit IP once and keeps it for a few minutes. The network map shows the
same layers when privacy or the reverse proxy is active.

Emergency unlock if the Pi firewall ever blocks you:

```bash
sudo ./scripts/privacy-server.sh unlock
```

| Feature | Status |
| --- | --- |
| Custom WireGuard / `.conf` import as exit | yes |
| Mullvad account → device → server (public API) | yes |
| Mullvad native multi-hop (entry + exit) | yes: *Choose server* → *Multi-hop through a second server* |
| Independent nested WG tunnels beyond that | refused (not silently faked) |
| DNS modes (system / custom / VPN-routed / provider / DoT) | yes; DoH/DoQ not shipped yet |
| Pi upstream kill switch (nftables) | yes; LAN management stays allowed |
| Tor SOCKS / VPN-then-Tor | only if `tor` is already installed |
| AmneziaWG / Shadowsocks / Xray | capability-gated; off on a stock Pi kernel |
| SSH remote VPS setup | plan/preview only; no auto-apply |

### Mullvad

*Network › VPN › Providers*: link the account with its 16-digit number (it is checked with Mullvad, an account without
paid time is refused, and the number is stored sealed), then *Choose server*: country, city and server for the exit,
and with *Multi-hop through a second server* an entry server. Mullvad's own multi-hop connects to the entry's address
on the exit's port with the exit's key, so the entry sees the home and the exit sees the destination. Keys, addresses
and ports come from Mullvad's relay list on the Pi, never from the browser.

`pi-guard-exit-apply` brings up `pgexit` and routes the home VPN (`wg0`) into it: the main table without its default
route first (home network, the VPN itself), then the exit. The privacy table masquerades what leaves through
`pgexit`, and with the kill switch it drops VPN traffic that would leave another way; other forwarding, such as
containers, is not touched. After an update of these scripts run `sudo ./scripts/privacy-server.sh install` again.

Until 2026-11-12 every exit was refused (`invalid exit.peer`): a bracket in the endpoint check of
`pi-guard-exit-apply` was written so that no address matched. With the kill switch on, VPN devices then had no
internet. Tested with the Mullvad API's answers and the script in test mode; a real tunnel to Mullvad was not tested.

Honest limits: the home VPN endpoint remains identifiable; multi-hop is not correlation-proof; same-provider entry and
exit are not independent trust domains; Tor routing is not Tor Browser; obfuscation does not mean undetectable.

See also [privacy.md](privacy.md) and the compatibility notes under *Things that already went wrong* in `AGENTS.md`.

## Disguised (AmneziaWG)

Some networks recognise WireGuard by the look of its packets and block it: hotels, offices, some countries.
[AmneziaWG](https://github.com/amnezia-vpn) is WireGuard with junk packets before the handshake, padded
handshakes and other message headers; the keys and the encryption stay WireGuard's.

```bash
sudo ./scripts/amneziawg.sh install          # UDP 443, or name a port: install 51821
sudo ./scripts/amneziawg.sh status
sudo ./scripts/amneziawg.sh remove
```

`install` builds the kernel module with DKMS (rebuilt by itself after kernel updates) and the `awg`/`awg-quick`
tools, from fixed releases whose SHA-256 the script checks, and sets up `awg0` next to `wg0` with the Pi's
WireGuard key. The disguise is chosen once at random and kept in `/var/lib/pi-guard-awg/params`, because the
apps carry the same values. Forward the UDP port on the router to the Pi, then restart pi-guard.

Devices stay those of `wg0`. `pi-guard-awg-sync` copies every device's key, preshared key and address to `awg0`
each second and routes a device's `10.8.0.N` through the interface it greeted last, so Pi-hole, presence and the
dashboard see one device whichever way it came. The post-quantum service sets rotating keys on both and counts
a greeting on either as alive; the privacy exit and its kill switch treat `awg0` like `wg0`. After updating, run
`vpn-server.sh install` and `privacy-server.sh install` again so their parts know `awg0`.

The phone app (1.8 and newer) gets the disguised ways next to the normal ones and tries them after those; it
uses AmneziaWG's tunnel library, which also speaks plain WireGuard. `scripts/build-android.sh` builds that
library once from a fixed release (commit checked) into `~/.cache/pi-guard`. The desktop app does not have it
yet: it would need AmneziaWG on each computer.

Tested: the module and tools build on the owner's Pi (kernel 6.18, Debian 13), the sync script with stand-in
tools, the app compiles against the library's API. Not tested yet: a real connection over `awg0`, from a phone
in a network that blocks WireGuard.

## Installing on Linux

The desktop app needs a small service, because tunnels and firewall rules need root. The app itself does not.

```bash
cd vpn && cargo build --release && cd ..
sudo ./scripts/install-vpn.sh
```

Then open **PiGuard › VPN** in the app, and log in to the dashboard first. Only the user who ran this may control the
service.

## Installing on Windows

**Not tested on a real Windows yet.** It is built and its logic runs under Wine, but tunnels and services were never run on
Windows. Treat it as a preview.

The service drives the official, signed WireGuard client instead of bringing a driver of its own. For a tunnel of all
traffic that client blocks everything past it by itself, through the Windows Filtering Platform: that is the kill switch.
It has no lockdown or split tunneling, and Rosenpass has no Windows version, so the app does not offer them.

In an administrator PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File install-vpn.ps1 -Exe .\pi-guard-vpn.exe
```

It installs WireGuard if needed, after checking its signature. Only the user who runs this may control the service.

## Kill switch and lockdown

- **Kill switch** (on by default): while the VPN is wanted, nothing goes past the tunnel, even if it fails. After a
  restart of the computer the VPN is only wanted again with *auto-connect*. A restart of the service alone changes
  nothing.
- **Lockdown:** nothing reaches the internet without the VPN, also while disconnected and after a restart. The service
  starts before the network does.

Both are nftables rules for IPv4 and IPv6 alike, with a default of *drop*. They stay in the kernel by themselves, so if
the service crashes, the system stays closed instead of open. *Local devices stay reachable* keeps the home network and
private addresses open; switch it off in foreign networks.

Stopping the service leaves the rules and the tunnel's routes in place, on purpose. If the kill switch or lockdown ever
locks you out, no restart of the computer is needed:

```bash
sudo systemctl stop pi-guard-vpn
sudo pi-guard-vpn unlock
```

## Split tunneling

*Past the VPN* starts a program outside the tunnel, or on the command line:

```bash
pi-guard-vpn run -- firefox
```

Everything it starts stays outside as well. It uses a cgroup, so it is tied to the running program, not to its name.

If the app says *Split tunneling is off*, the kernel could not load `nft_socket`, usually because it was updated and
the computer has not restarted since. The VPN and the kill switch keep working, and programs simply use the tunnel.
Restart the computer. `install-vpn.sh` also has the modules load at every boot, so later updates cannot cause this.

## Quantum-resistant tunnel

WireGuard alone relies on Curve25519, which a future quantum computer could break, so traffic recorded today could be
read later. pi-guard adds a key from two post-quantum methods to the tunnel, on phones, computers and the Pi alike, from
one piece of Rust code (`vpn/kem`).

Through the running tunnel, the device sends fresh ML-KEM-1024 and HQC-256 keys, and the Pi answers with a secret for
each. Both sides mix these with the device's starting key into a **rotating WireGuard preshared key**. That is hybrid
post-quantum key establishment feeding WireGuard; it is not the same as a static PSK alone, and WireGuard's own
handshake remains Curve25519. The idea comes from how the
[Mullvad app](https://github.com/mullvad/mullvadvpn-app) does it; the code is our own.

- **Two methods** from different mathematics: a break of one leaves the other.
- **The starting key is mixed in**, and each side proves it holds it, so nobody can sit in the middle.
- **Fresh keys every time**: a stolen key says nothing about earlier ones.
- **No extra port on the internet**: it runs inside the tunnel, on `10.8.0.1`, so the router stays untouched and every
  way home works.

Computers renew the key about every two minutes, without interrupting anything. Phones get a new key each time they
connect, because Android cannot swap it in a running tunnel: the tunnel starts again once, right after connecting. Unless
you use Android's *Block connections without VPN*, a little traffic can pass outside the tunnel in that moment.

It is on by default. You can switch it off in the dashboard under **Network › VPN**, or per computer in the app.

```bash
sudo ./scripts/vpn-server.sh install   # on the Pi, once; takes both programs from ~/pi-guard-vpn
```

What to know:

- If a device goes silent for more than 200 seconds, the Pi returns to its starting key, because the device may only know
  that one after a restart. A device tries its last key first, then the starting one. So a phone waking from a long
  sleep can need some seconds more.
- If the exchange fails, the tunnel keeps running on the starting key, and the app shows it as classic encryption.
- One exchange moves about 25 KB and takes around a third of a second of processor time on a Pi 4.
- ML-KEM is a NIST standard. The implementation we use has had no formal audit as far as we know, and HQC is newer.
  Since all of this only adds to WireGuard's own keys, a flaw in it would not make the tunnel weaker than without it.
- Devices that have not updated yet still use [Rosenpass](https://rosenpass.eu), so the Pi keeps it for now.

## Building for the Pi

`scripts/vpn-server.sh install` takes the programs from `~/pi-guard-vpn` on the Pi. `pi-guard-vpn` has C code in it (HQC), so
a build on a computer needs a C compiler for the Pi too, here on Debian with `gcc-aarch64-linux-gnu`:

```bash
rustup target add aarch64-unknown-linux-gnu
cd vpn
CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
    cargo build --release --target aarch64-unknown-linux-gnu
```

Build on the same Debian release as the Pi, or a newer glibc will refuse to start there. For Windows, the same with
`gcc-mingw-w64-x86-64` and `--target x86_64-pc-windows-gnu`. Android: `scripts/build-android.sh` does it by itself with the
NDK. Until Rosenpass is gone, `install` also wants a `rosenpass` built for the Pi in the same folder.

## How it is built

`vpn/` is a small Rust program without a shell in between: every value from the access is checked before it reaches
`ip`, `wg` or `nft`, and secrets go through pipes, not the argument list. The Pi's part treats everything from pi-guard as
untrusted: names must be keys, files are copied with no links followed, and root writes nothing into pi-guard's folders.

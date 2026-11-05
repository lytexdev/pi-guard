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
| Quantum-resistant tunnel | no | yes | no | |

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

- **Kill switch** (on by default): while the VPN is wanted, nothing goes past the tunnel, even if it fails.
- **Lockdown:** nothing reaches the internet without the VPN, also while disconnected and after a restart. The service
  starts before the network does.

Both are nftables rules for IPv4 and IPv6 alike, with a default of *drop*. They stay in the kernel by themselves, so if
the service crashes, the system stays closed instead of open. *Local devices stay reachable* keeps the home network and
private addresses open; switch it off in foreign networks.

If lockdown ever locks you out:

```bash
sudo pi-guard-vpn unlock
```

## Split tunneling

*Past the VPN* starts a program outside the tunnel, or on the command line:

```bash
pi-guard-vpn run -- firefox
```

Everything it starts stays outside as well. It uses a cgroup, so it is tied to the running program, not to its name.

## Quantum-resistant tunnel

WireGuard alone relies on Curve25519, which a future quantum computer could break, so traffic recorded today could be
read later. [Rosenpass](https://rosenpass.eu) adds a key from post-quantum methods (Classic McEliece and Kyber) and
renews it about every two minutes.

It runs through the tunnel itself, so no port opens to the internet, the router stays untouched, and every way home
works. It is on by default. It uses some memory and processor time on the Pi, so you can switch it off in the dashboard
under **Network › VPN** or per computer in the app.

```bash
sudo ./scripts/vpn-server.sh install   # on the Pi, once; takes both programs from ~/pi-guard-vpn
```

What to know:

- It protects the keys of the tunnel. The first connection of a device uses its starting key, which came to it over the
  dashboard's TLS connection. That connection uses X25519MLKEM768 with the desktop app, which is quantum-resistant too.
- The phone app has no Rosenpass client, so phones stay on their fixed extra key.
- If a device goes silent for more than 200 seconds, the Pi returns to its starting key, because the device may only know
  that one after a restart, and Rosenpass runs inside the tunnel. The device tries its last key first, then the starting one.
- Rosenpass is a young project. Its protocol is formally analysed, but not as long proven as WireGuard. Since it only
  adds to WireGuard's own keys, a flaw in it would not make the tunnel weaker than without it.

## How it is built

`vpn/` is a small Rust program without a shell in between: every value from the access is checked before it reaches
`ip`, `wg` or `nft`, and secrets go through pipes, not the argument list. The Pi's part treats everything from pi-guard as
untrusted: names must be keys, files are copied with no links followed, and root writes nothing into pi-guard's folders.

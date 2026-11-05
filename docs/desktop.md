# Desktop app

PiGuard for Windows, Linux and macOS: the dashboard in its own window, with its own icon and without a browser's
certificate warning. It shows what runs on your Pi, so it never needs its own updates to get new dashboard features.

## Install

| System | File | Note |
| --- | --- | --- |
| Linux | `PiGuard-*-linux-x86_64.AppImage` or `.deb` | make the AppImage executable and start it |
| Windows | `PiGuard-*-win-x64.exe` | Windows warns because the file is not signed: *More info › Run anyway* |
| macOS | `PiGuard-*-mac-*.zip` | not signed or notarized, see below |

Build them yourself with:

```bash
./scripts/build-desktop.sh 1.0.0 linux win mac
```

Results land in `desktop/dist`. Windows needs wine on Linux. On macOS, build there for a working Apple Silicon version:
Apple requires a signature, and without a paid developer account macOS blocks the app at first. Open it with a right
click, *Open*, or under *System Settings › Privacy & Security*.

## Performance

Measured on Linux with the dashboard open: about 160 MB of memory in total, unchanged after 280 page changes, so nothing
piles up. There is one window and no timers in the app itself, hardware acceleration stays on, and a hidden window is
throttled by Chromium. Spell checking is off, because it would load dictionaries from Google.

Electron stays the choice over a smaller Rust frame such as Tauri, which uses the system's web view. That would save
disk space, but certificates, camera and microphone would then work differently on every system, and pinning your
pi-guard's certificate is what this app is for.

## First start

Enter the address of your pi-guard, for example `https://192.168.178.109:8443`. The app shows the fingerprint of its
certificate. Compare it if you want to be sure, then press *Trust*. After that, log in as usual.

Away from home you need the VPN, or use your public address if you set one up. See
[ways home](network.md#ways-home-from-outside).

## Security

- The app trusts the certificate authority of your pi-guard, which it remembers at the first start, and public
  certificates. Anything else is refused. The name you connect by must be in the certificate.
- The dashboard runs sandboxed without access to your computer. Only the camera and microphone for the dashboard's own
  features are allowed, and links to other sites open in your browser.
- The address and certificate are stored in the app's folder, readable by your user only.

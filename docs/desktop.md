# Desktop app

PiGuard for Windows, Linux and macOS: the dashboard in its own window, with its own icon and without a browser's
certificate warning. It shows what runs on your Pi, so it never needs its own updates to get new dashboard features.

## Install

| System | File | Note |
| --- | --- | --- |
| Linux, recommended | `PiGuard-*-linux-x64.tar.gz` | `./scripts/install-desktop.sh FILE`: for your user, on Arch too, starts fastest and updates itself |
| Linux | `PiGuard-*-linux-x86_64.AppImage` or `.deb` | no automatic updates |
| Windows | `PiGuard-*-win-x64.exe` | Windows warns because the file is not signed: *More info › Run anyway* |
| macOS | `PiGuard-*-mac-*.zip` | not signed or notarized, see below |

Build them yourself with:

```bash
./scripts/build-desktop.sh 1.0.0 linux win mac
```

Results land in `desktop/dist`. Windows needs wine on Linux. On macOS, build there for a working Apple Silicon version:
Apple requires a signature, and without a paid developer account macOS blocks the app at first. Open it with a right
click, *Open*, or under *System Settings › Privacy & Security*.

## Updates

The app updates itself, signed and verified. See [updates.md](updates.md).

## Performance

Measured on Arch with KDE and an NVIDIA card: about 330 MB of memory in total with the GPU in use, and unchanged after
280 page changes in a test without a GPU, so nothing piles up. There is one window, hardware acceleration stays on, and a
hidden window is throttled by Chromium. Spell checking is off, because it would load dictionaries from Google.

Electron stays the choice over a smaller Rust frame such as Tauri, which uses the system's web view. That would save
disk space, but certificates, camera and microphone would then work differently on every system, and pinning your
pi-guard's certificate is what this app is for.

## Icon and search on Linux

`scripts/install-desktop.sh` adds the entry for your applications menu and search, and the icon. The taskbar finds them
through the app id `pi-guard-desktop`, so the file must be called `pi-guard-desktop.desktop`. An AppImage started by hand
has no such entry, which is why it shows the Electron icon and cannot be found.

## Logging in

All ways of the dashboard work: password, key file, and Telegram with the code word if you set one. Passkeys depend on
the system: Electron has no dialog for them on Linux, so use a browser there.

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

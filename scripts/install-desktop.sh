#!/usr/bin/env bash
# Installs the desktop app for this user on Linux, without root.
#
#   ./scripts/install-desktop.sh desktop/dist/PiGuard-1.1.0-linux-x64.tar.gz
#
# It lives in ~/.local/opt/pi-guard, so it can update itself. Starts faster than an AppImage, which
# would have to be mounted at every start, and works the same on Arch and elsewhere.
set -euo pipefail

ARCHIVE="${1:?usage: $0 PiGuard-VERSION-linux-x64.tar.gz}"
ROOT="${PI_GUARD_DESKTOP_ROOT:-$HOME/.local/opt/pi-guard}"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
VERSION="$(basename "$ARCHIVE" | sed -E 's/^PiGuard-([0-9.]+)-.*/\1/')"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "cannot read the version from $ARCHIVE" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$ROOT/versions"
target="$ROOT/versions/$VERSION"
rm -rf "$target"
mkdir -p "$target"
tar -xzf "$ARCHIVE" -C "$target" --strip-components=1
ln -sfn "versions/$VERSION" "$ROOT/current"

for size in 512 256 128 64 48; do
    folder="$SHARE/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$folder"
    if command -v magick >/dev/null; then
        magick "$HERE/src/pi_guard/web/static/img/icon-512.png" -resize "${size}x${size}" "$folder/pi-guard-desktop.png"
    elif [[ $size == 512 ]]; then
        cp "$HERE/src/pi_guard/web/static/img/icon-512.png" "$folder/pi-guard-desktop.png"
    fi
done

mkdir -p "$SHARE/applications" "$HOME/.local/bin"
# The file name must be the app id: that is how the taskbar finds name and icon.
cat > "$SHARE/applications/pi-guard-desktop.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=PiGuard
Comment=Your home, in your hand
Exec=$ROOT/current/pi-guard-desktop
Icon=pi-guard-desktop
Terminal=false
Categories=Network;System;Security;
StartupWMClass=pi-guard-desktop
StartupNotify=true
DESKTOP
ln -sfn "$ROOT/current/pi-guard-desktop" "$HOME/.local/bin/piguard"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$SHARE/applications" || true
for tool in kbuildsycoca6 kbuildsycoca5; do command -v "$tool" >/dev/null && "$tool" >/dev/null 2>&1 && break; done || true  # KDE search
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$SHARE/icons/hicolor" 2>/dev/null || true
echo "PiGuard $VERSION installed. Start it from your applications menu, or with: piguard"

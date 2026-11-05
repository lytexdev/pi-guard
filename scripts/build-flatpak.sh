#!/usr/bin/env bash
# Builds the desktop app as a Flatpak, for your user, without root.
#
#   ./scripts/build-flatpak.sh desktop/dist/PiGuard-1.5.0-linux-x64.tar.gz
#
# Needs the Flatpak builder once: flatpak install --user flathub org.flatpak.Builder
# Result: desktop/dist/PiGuard.flatpak, to install with: flatpak install --user PiGuard.flatpak
set -euo pipefail
ARCHIVE="$(realpath "${1:?usage: $0 PiGuard-VERSION-linux-x64.tar.gz}")"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d "$ROOT/desktop/.flatpak.XXXXXX")"
trap 'rm -rf "$work"' EXIT
cp "$ROOT"/desktop/flatpak/* "$work/"
cp "$ARCHIVE" "$work/PiGuard.tar.gz"
cp "$ROOT/src/pi_guard/web/static/img/icon-512.png" "$work/icon.png"
cd "$work"
# From here, because the builder runs in a sandbox of its own and cannot install them.
flatpak install --user -y --noninteractive flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08 org.electronjs.Electron2.BaseApp//25.08
flatpak run --command=flatpak-builder --filesystem="$work" --filesystem=xdg-data/flatpak --env=FLATPAK_USER_DIR="$HOME/.local/share/flatpak" org.flatpak.Builder --user --force-clean --repo=repo build de.lytex.PiGuard.yml
flatpak build-bundle repo "$ROOT/desktop/dist/PiGuard.flatpak" de.lytex.PiGuard --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo
ls -la "$ROOT/desktop/dist/PiGuard.flatpak"

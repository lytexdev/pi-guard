#!/usr/bin/env bash
# Installs the desktop app for this user on Linux, without root.
#
#   ./scripts/install-desktop.sh
#   ./scripts/install-desktop.sh desktop/dist/PiGuard-1.1.0-linux-x64.tar.gz
#   ./scripts/install-desktop.sh https://github.com/lytexdev/pi-guard/releases/download/apps-1.1.0/PiGuard-1.1.0-linux-x64.tar.gz
#
# With no argument it downloads the newest apps-* release from GitHub. The app then updates itself
# from your Pi. It lives in ~/.local/opt/pi-guard so it can replace its own files.
set -euo pipefail

ROOT="${PI_GUARD_DESKTOP_ROOT:-$HOME/.local/opt/pi-guard}"
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
REPO="${PI_GUARD_GITHUB:-lytexdev/pi-guard}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP=
cleanup() { [[ -n "${TMP:-}" && -d "$TMP" ]] && rm -rf "$TMP"; }
trap cleanup EXIT

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "$1 is required"; }

arch_name() {
    case "$(uname -m)" in
        x86_64|amd64) echo x64 ;;
        aarch64|arm64) echo arm64 ;;
        *) die "unsupported CPU $(uname -m); build with scripts/build-desktop.sh" ;;
    esac
}

latest_asset() {
    need curl
    need python3
    local arch api
    arch="$(arch_name)"
    api="https://api.github.com/repos/$REPO/releases"
    curl -fsSL "$api" | ARCH="$arch" python3 -c '
import json, os, sys
arch = os.environ["ARCH"]
want = f"-linux-{arch}.tar.gz"
for release in json.load(sys.stdin):
    if not (release.get("tag_name") or "").startswith("apps-"):
        continue
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        if name.startswith("PiGuard-") and name.endswith(want):
            print(asset["browser_download_url"])
            print(name)
            raise SystemExit(0)
sys.stderr.write("no PiGuard linux tar.gz in apps-* releases\n")
raise SystemExit(1)
'
}

resolve_archive() {
    local source="${1:-}" url name
    if [[ -z "$source" ]]; then
        mapfile -t lines < <(latest_asset)
        url="${lines[0]-}" name="${lines[1]-}"
        [[ -n "$url" && -n "$name" ]] || die "could not find a desktop release"
        TMP="$(mktemp -d)"
        echo "Downloading $name …"
        curl -fsSL "$url" -o "$TMP/$name"
        ARCHIVE="$TMP/$name"
        return
    fi
    if [[ "$source" =~ ^https?:// ]]; then
        need curl
        name="$(basename "${source%%\?*}")"
        TMP="$(mktemp -d)"
        echo "Downloading $name …"
        curl -fsSL "$source" -o "$TMP/$name"
        ARCHIVE="$TMP/$name"
        return
    fi
    [[ -f "$source" ]] || die "file not found: $source"
    ARCHIVE="$(cd "$(dirname "$source")" && pwd)/$(basename "$source")"
}

resolve_archive "${1:-}"
VERSION="$(basename "$ARCHIVE" | sed -E 's/^PiGuard-([0-9.]+)-.*/\1/')"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "cannot read the version from $ARCHIVE"

mkdir -p "$ROOT/versions"
target="$ROOT/versions/$VERSION"
rm -rf "$target"
mkdir -p "$target"
tar -xzf "$ARCHIVE" -C "$target" --strip-components=1
ln -sfn "versions/$VERSION" "$ROOT/current"

for size in 512 256 128 64 48; do
    folder="$SHARE/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$folder"
    if command -v magick >/dev/null && [[ -f "$HERE/src/pi_guard/web/static/img/icon-512.png" ]]; then
        magick "$HERE/src/pi_guard/web/static/img/icon-512.png" -resize "${size}x${size}" "$folder/pi-guard-desktop.png"
    elif [[ $size == 512 && -f "$HERE/src/pi_guard/web/static/img/icon-512.png" ]]; then
        cp "$HERE/src/pi_guard/web/static/img/icon-512.png" "$folder/pi-guard-desktop.png"
    elif [[ -f "$target/resources/app/build/icon.png" && $size == 512 ]]; then
        cp "$target/resources/app/build/icon.png" "$folder/pi-guard-desktop.png" 2>/dev/null || true
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
for tool in kbuildsycoca6 kbuildsycoca5; do command -v "$tool" >/dev/null && "$tool" >/dev/null 2>&1 && break; done || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$SHARE/icons/hicolor" 2>/dev/null || true
echo "PiGuard $VERSION installed. Start it from your applications menu, or with: piguard"
echo "Later versions install themselves from your Pi (minor/major) or via Check for updates."

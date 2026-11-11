#!/usr/bin/env bash
# Builds the desktop app.
#
#   ./scripts/build-desktop.sh 1.0.0            # for this system
#   ./scripts/build-desktop.sh 1.0.0 linux win mac
#
# Results land in desktop/dist. Windows needs wine on Linux. Nothing is signed, so Windows and
# macOS warn at the first start; see docs/desktop.md.
set -euo pipefail

VERSION="${1:?usage: $0 VERSION [linux] [win] [mac]}"
shift
cd "$(dirname "${BASH_SOURCE[0]}")/../desktop"

mkdir -p build
cp ../src/pi_guard/web/static/img/icon-512.png build/icon.png
npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund
# The version goes into package.json for the build only, so the tree stays clean for release-apps.sh.
KEEP="$(mktemp -d)"
cp package.json package-lock.json "$KEEP/"
trap 'cp "$KEEP/package.json" "$KEEP/package-lock.json" . && rm -rf "$KEEP"' EXIT
npm version "$VERSION" --no-git-tag-version --allow-same-version >/dev/null
node --test
flags=()
for target in "$@"; do
    flags+=("--$target")
    if [[ "$target" == linux ]]; then
        # .rpm and .pacman need tools many systems lack; leave them out rather than fail the release.
        packages=(tar.gz AppImage deb)
        if command -v rpmbuild >/dev/null; then packages+=(rpm); else echo "no rpmbuild: the .rpm is left out" >&2; fi
        if command -v bsdtar >/dev/null; then packages+=(pacman); else echo "no bsdtar: the .pacman is left out" >&2; fi
        flags+=("${packages[@]}")
    fi
done
npx electron-builder "${flags[@]}" --publish never
ls -la dist | grep -E 'AppImage|deb|exe|zip' || true

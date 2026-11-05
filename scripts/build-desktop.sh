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
npm version "$VERSION" --no-git-tag-version --allow-same-version >/dev/null
node --test
flags=()
for target in "$@"; do flags+=("--$target"); done
npx electron-builder "${flags[@]}" --publish never
ls -la dist | grep -E 'AppImage|deb|exe|zip' || true

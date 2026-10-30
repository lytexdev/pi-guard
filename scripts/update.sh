#!/usr/bin/env bash
# Update an installed pi-guard from this checkout. Configuration and recordings are kept.
#
#   git pull && sudo ./scripts/update.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/pi-guard

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -x "$PREFIX/venv/bin/pip" ]] || { echo "pi-guard is not installed; run install.sh" >&2; exit 1; }

EXTRAS="vision,mqtt"
"$PREFIX/venv/bin/python" -c 'import cv2' 2>/dev/null \
    && ! "$PREFIX/venv/bin/pip" show opencv-python-headless >/dev/null 2>&1 \
    && EXTRAS="mqtt"  # system OpenCV on 32-bit boards

echo "==> installing $(git -C "$REPO" describe --always --dirty 2>/dev/null || echo 'working tree')"
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$REPO[$EXTRAS]"
install -m 0644 "$REPO/deploy/systemd/pi-guard.service" /etc/systemd/system/pi-guard.service
systemctl daemon-reload
pi-guard --env-file /etc/pi-guard/pi-guard.env config check
systemctl restart pi-guard
echo "==> $(pi-guard version) restarted; check with: pi-guard doctor"

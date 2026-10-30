#!/usr/bin/env bash
# Remove the pi-guard service and application.
# Configuration, recordings and face data are only deleted after explicit confirmation.
set -euo pipefail

PREFIX=/opt/pi-guard
CONF=/etc/pi-guard
DATA=/var/lib/pi-guard

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

RECORDINGS="$DATA/recordings"
if [[ -f "$CONF/pi-guard.env" ]]; then
    configured="$(sed -n 's/^PI_GUARD_RECORDINGS_DIR=//p' "$CONF/pi-guard.env" | tail -1)"
    RECORDINGS="${configured:-$RECORDINGS}"
fi

echo "==> stopping and removing the service"
systemctl disable --now pi-guard 2>/dev/null || true
systemctl disable --now pi-guard-signal 2>/dev/null || true
rm -f /etc/systemd/system/pi-guard.service /etc/systemd/system/pi-guard-signal.service
rm -rf /etc/systemd/system/pi-guard.service.d
systemctl daemon-reload
rm -f /usr/local/bin/pi-guard
rm -rf "$PREFIX"
echo "==> application removed"

read -r -p "Delete configuration in $CONF (bot token, settings)? [y/N] " answer
[[ "$answer" =~ ^[Yy]$ ]] && rm -rf "$CONF" && echo "    deleted $CONF"

echo
echo "Recordings: $RECORDINGS"
echo "State, snapshots and face data: $DATA"
read -r -p "Type DELETE to permanently delete all recordings and data, or press Enter to keep them: " answer
if [[ "$answer" == "DELETE" ]]; then
    rm -rf "$RECORDINGS" "$DATA"
    echo "    recordings and data deleted"
    if id pi-guard >/dev/null 2>&1; then
        userdel pi-guard && echo "    user pi-guard removed"
    fi
else
    echo "    kept; the pi-guard user was kept so the files keep their owner"
fi

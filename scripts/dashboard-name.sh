#!/usr/bin/env bash
# A name for the dashboard, needed for passkeys and hardware keys: browsers refuse them on IP
# addresses. Pi-hole answers the name, so it works on every device that uses Pi-hole as DNS.
#
#   sudo ./scripts/dashboard-name.sh pi-guard.lan
set -euo pipefail
cd /
NAME="${1:-pi-guard.lan}"
ENV_FILE=/etc/pi-guard/pi-guard.env
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ "$NAME" =~ ^[a-z0-9-]+(\.[a-z0-9-]+)+$ ]] || { echo "not a name: $NAME" >&2; exit 1; }
command -v pihole-FTL >/dev/null || { echo "Pi-hole not found; add the name to your DNS yourself" >&2; exit 1; }
IP="$(ip -4 route get 1.1.1.1 | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"
current="$(pihole-FTL --config dns.hosts | tr -d '[]' )"
entries="$(python3 -c '
import json, sys
current, entry, name = sys.argv[1:]
items = [e.strip().strip("\"") for e in current.split(",") if e.strip()]
items = [e for e in items if not e.endswith(" " + name)] + [entry]
print(json.dumps(items))' "$current" "$IP $NAME" "$NAME")"
pihole-FTL --config dns.hosts "$entries" >/dev/null
names="$(sed -n 's/^PI_GUARD_DASHBOARD_TLS_NAMES=//p' "$ENV_FILE")"
if [[ ",$names," != *",$NAME,"* ]]; then
    tmp="$(mktemp)"
    grep -v '^PI_GUARD_DASHBOARD_TLS_NAMES=' "$ENV_FILE" > "$tmp" || true
    echo "PI_GUARD_DASHBOARD_TLS_NAMES=${names:+$names,}$NAME" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
fi
systemctl restart pi-guard
echo "https://$NAME:8443 now reaches this Pi on devices that use Pi-hole as DNS."

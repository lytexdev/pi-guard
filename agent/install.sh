#!/usr/bin/env bash
# Install the pi-guard agent on this computer.
#
#   cd agent && cargo build --release
#   sudo ./install.sh https://192.168.178.109:8443 pga_...
#
# Downloads the certificate authority of your pi-guard once and shows its fingerprint,
# so the connection cannot be redirected later. Compare it with the dashboard.
set -euo pipefail
URL="${1:-}" TOKEN="${2:-}"
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -n "$URL" && -n "$TOKEN" ]] || { echo "usage: $0 https://pi:8443 TOKEN" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/target/release/pi-guard-agent"
[[ -x "$BIN" ]] || { echo "build first: cargo build --release" >&2; exit 1; }
CONF=/etc/pi-guard-agent
install -d -m 0700 "$CONF"
curl -fsSk "$URL/ca.crt" -o "$CONF/ca.crt"
echo "Fingerprint of the pi-guard certificate authority:"
openssl x509 -in "$CONF/ca.crt" -noout -fingerprint -sha256
read -r -p "Does it match the dashboard? [y/N] " answer
[[ "$answer" =~ ^[YyJj] ]] || { rm -f "$CONF/ca.crt"; echo "cancelled"; exit 1; }
umask 077
cat > "$CONF/agent.conf" <<CONFIG
URL=$URL
TOKEN=$TOKEN
CA_FILE=$CONF/ca.crt
# Set to false to forbid shutdown and reboot, or Docker control, from pi-guard.
ALLOW_POWER=true
ALLOW_DOCKER=true
# Signed new versions from your pi-guard install themselves. Set to false to update by hand.
AUTO_UPDATE=true
CONFIG
install -m 0755 "$BIN" /usr/local/bin/pi-guard-agent
install -m 0644 "$HERE/pi-guard-agent.service" /etc/systemd/system/pi-guard-agent.service
systemctl daemon-reload
systemctl enable --now pi-guard-agent
systemctl --no-pager status pi-guard-agent | head -4

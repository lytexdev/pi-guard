#!/usr/bin/env bash
# Post-quantum keys for the VPN on your pi-guard (Rosenpass).
#
#   sudo ./scripts/vpn-server.sh install BINARY ROSENPASS   # both for this device's processor
#   sudo ./scripts/vpn-server.sh remove
#
# What install changes on this system:
#   - /usr/local/sbin/pi-guard-vpn, /usr/local/lib/pi-guard-vpn/rosenpass, pi-guard-pq.service
#   - ufw (if active): UDP 9999, only inside the VPN (on wg0). Nothing opens to the internet,
#     and the router stays as it is: Rosenpass runs through the tunnel.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=9999
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"

install_all() {
    local binary="${1:?usage: $0 install BINARY ROSENPASS}" rosenpass="${2:?rosenpass binary}"
    [[ -f /etc/wireguard/wg0.base ]] || die "set up WireGuard first: scripts/wireguard.sh setup"
    install -m 0755 "$binary" /usr/local/sbin/pi-guard-vpn
    install -d -m 0755 /usr/local/lib/pi-guard-vpn
    install -m 0755 "$rosenpass" /usr/local/lib/pi-guard-vpn/rosenpass
    install -m 0644 "$REPO/deploy/vpn/pi-guard-pq.service" /etc/systemd/system/
    if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
        ufw allow in on wg0 to any port "$PORT" proto udp comment 'pi-guard post-quantum keys' >/dev/null
    fi
    systemctl daemon-reload
    systemctl enable --now pi-guard-pq
    echo "running. Switch it on or off in the dashboard under Network > VPN."
}

remove() {
    systemctl disable --now pi-guard-pq 2>/dev/null || true
    rm -f /etc/systemd/system/pi-guard-pq.service /usr/local/sbin/pi-guard-vpn
    rm -rf /usr/local/lib/pi-guard-vpn
    command -v ufw >/dev/null && ufw delete allow in on wg0 to any port "$PORT" proto udp >/dev/null 2>&1 || true
    systemctl daemon-reload
}

case "${1:-}" in
    install) shift; install_all "$@" ;;
    remove) remove ;;
    *) die "usage: $0 install BINARY ROSENPASS | remove" ;;
esac

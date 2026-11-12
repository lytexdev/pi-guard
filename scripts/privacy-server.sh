#!/usr/bin/env bash
# Optional privacy upstream on the Pi (multi-hop exit, DNS fail-closed, Tor control).
#
#   sudo ./scripts/privacy-server.sh install
#   sudo ./scripts/privacy-server.sh remove
#   sudo ./scripts/privacy-server.sh unlock   # emergency: clear privacy nft table
#
# Does not install tor, mullvad, amnezia, xray, or shadowsocks packages.
# WireGuard and nftables must already be available (same as the home VPN).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/deploy/privacy"
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"

install_all() {
    [[ -f /etc/wireguard/wg0.base ]] || die "set up WireGuard first: scripts/wireguard.sh setup"
    command -v wg >/dev/null || die "wireguard-tools (wg) required"
    command -v nft >/dev/null || die "nftables required"
    install -d -m 0755 /usr/local/sbin
    install -m 0755 "$SRC/pi-guard-exit-apply" /usr/local/sbin/
    install -m 0755 "$SRC/pi-guard-privacy-nft" /usr/local/sbin/
    install -m 0755 "$SRC/pi-guard-tor-ctl" /usr/local/sbin/
    install -m 0755 "$SRC/pi-guard-privacy-status" /usr/local/sbin/
    install -d -m 0755 /var/lib/pi-guard-privacy
    install -d -m 0755 /run/pi-guard-status
    # data dir owned by pi-guard; privacy subfolder created by the service
    if id pi-guard >/dev/null 2>&1; then
        install -d -o pi-guard -g pi-guard -m 0700 /var/lib/pi-guard/privacy
    else
        install -d -m 0700 /var/lib/pi-guard/privacy
    fi
    touch /var/lib/pi-guard/privacy/installed
    if id pi-guard >/dev/null 2>&1; then
        chown pi-guard:pi-guard /var/lib/pi-guard/privacy/installed
    fi
    printf '1\n' > /run/pi-guard-status/privacy.installed
    for unit in \
        pi-guard-exit-apply.service pi-guard-exit-apply.path \
        pi-guard-privacy-nft.service pi-guard-privacy-nft.path \
        pi-guard-tor-ctl.service pi-guard-tor-ctl.path \
        pi-guard-privacy-status.service pi-guard-privacy-status.timer
    do
        install -m 0644 "$SRC/$unit" /etc/systemd/system/
    done
    systemctl daemon-reload
    systemctl enable --now pi-guard-exit-apply.path pi-guard-privacy-nft.path \
        pi-guard-tor-ctl.path pi-guard-privacy-status.timer
    systemctl restart pi-guard-privacy-status.timer
    # An exit already in use takes the new rules now, not only at its next change.
    systemctl start pi-guard-exit-apply.service || true
    echo "privacy upstream ready. Configure it under Network > VPN in the dashboard."
    echo "Tor, AmneziaWG, Shadowsocks and Xray are not installed automatically."
}

remove_all() {
    systemctl disable --now pi-guard-exit-apply.path pi-guard-privacy-nft.path \
        pi-guard-tor-ctl.path pi-guard-privacy-status.timer 2>/dev/null || true
    /usr/local/sbin/pi-guard-exit-apply 2>/dev/null || true
    # clear want so tor-ctl stops what we started
    rm -f /var/lib/pi-guard/privacy/tor.want /var/lib/pi-guard/privacy/nft.plan \
        /var/lib/pi-guard/privacy/exit.peer /var/lib/pi-guard/privacy/exit.conf \
        /var/lib/pi-guard/privacy/installed /run/pi-guard-status/privacy.installed
    /usr/local/sbin/pi-guard-tor-ctl 2>/dev/null || true
    nft 'add table inet pi_guard_privacy; delete table inet pi_guard_privacy' 2>/dev/null || true
    rm -f /usr/local/sbin/pi-guard-exit-apply /usr/local/sbin/pi-guard-privacy-nft \
        /usr/local/sbin/pi-guard-tor-ctl /usr/local/sbin/pi-guard-privacy-status
    rm -f /etc/systemd/system/pi-guard-exit-apply.* \
        /etc/systemd/system/pi-guard-privacy-nft.* \
        /etc/systemd/system/pi-guard-tor-ctl.* \
        /etc/systemd/system/pi-guard-privacy-status.*
    systemctl daemon-reload
    echo "privacy upstream removed."
}

unlock() {
    nft 'add table inet pi_guard_privacy; delete table inet pi_guard_privacy' 2>/dev/null || true
    rm -f /var/lib/pi-guard/privacy/nft.plan
    echo "privacy nft table cleared."
}

case "${1:-}" in
    install) install_all ;;
    remove) remove_all ;;
    unlock) unlock ;;
    *) die "usage: $0 install | remove | unlock" ;;
esac

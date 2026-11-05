#!/usr/bin/env bash
# Tailscale as a way home, for connections without a public address. Needs no router change
# and no rented server, but a Tailscale account: it is a company's service in between.
#
#   sudo ./scripts/tailscale.sh setup
#   sudo ./scripts/tailscale.sh status
#
# What setup changes on this system:
#   - installs Tailscale from its own package source, if it is not there
#   - logs this device in; Pi-hole stays its DNS (--accept-dns=false)
#   - ufw (if active): allow the dashboard on tailscale0
set -euo pipefail

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"

setup() {
    if ! command -v tailscale >/dev/null; then
        say "installing Tailscale"
        curl -fsSL https://tailscale.com/install.sh | sh
    fi
    systemctl enable --now --quiet tailscaled
    say "log in with the link that appears now"
    tailscale up --accept-dns=false
    if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
        ufw allow in on tailscale0 to any port 8443 proto tcp comment 'pi-guard dashboard over Tailscale' >/dev/null
    fi
    say "this device has the address $(tailscale ip -4)"
    echo "  Phone: install the Tailscale app, log in with the same account, turn it on."
    echo "  The PiGuard app then finds the dashboard by itself; open it once at home first."
    echo "  Android runs one VPN at a time, so leave the VPN inside PiGuard off."
}

case "${1:-}" in
    setup) setup ;;
    status) tailscale status ;;
    *) die "usage: $0 setup | status" ;;
esac

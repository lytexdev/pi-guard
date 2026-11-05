#!/usr/bin/env bash
# A rented server as a way home, for connections without a public address (DS-Lite, CGNAT).
#
#   sudo ./scripts/relay.sh setup root@vps.example.org
#   sudo ./scripts/relay.sh setup root@vps.example.org --domain guard.example.org
#   sudo ./scripts/relay.sh status
#   sudo ./scripts/relay.sh remove root@vps.example.org
#
# Options for setup: --public-port 51820 (for phones), --tunnel-port 51821 (for this Pi),
# --ssh-port 22, --name vps.example.org (if phones use another name than ssh).
#
# This Pi dials out to the server, so no router has to be changed. The server passes VPN
# packets on and cannot read them. It counts as a stranger here: of everything it could send
# through the tunnel, only the VPN port is accepted (and the dashboard with --domain).
#
# What setup changes on this system:
#   - /etc/wireguard/pgrelay.conf and wg-quick@pgrelay, firewall rules for pgrelay only
#   - PI_GUARD_WIREGUARD_RELAY in the settings, with --domain also the public dashboard address
# On the server: see deploy/relay/pi-guard-relay-server.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd /
IFACE=pgrelay
DIR=/etc/wireguard
ENV_FILE=/etc/pi-guard/pi-guard.env
SERVER=10.9.0.1
PI=10.9.0.2
TLS_NAME=pi-guard.relay

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

set_env() {  # set_env KEY VALUE: replace or append; no VALUE removes it
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
    [[ $# -lt 2 ]] || printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}

get_env() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -1; }

add_to_list() {  # add_to_list KEY VALUE: a comma separated setting
    local now
    now="$(get_env "$1")"
    [[ ",$now," == *",$2,"* ]] || set_env "$1" "${now:+$now,}$2"
}

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found; run scripts/install.sh first"

setup() {
    local target="${1:-}" public=51820 tunnel=51821 ssh_port=22 name="" domain=""
    [[ "$target" == *@* ]] || die "usage: $0 setup USER@SERVER [--domain NAME]"
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --public-port) public="$2"; shift 2 ;;
            --tunnel-port) tunnel="$2"; shift 2 ;;
            --ssh-port) ssh_port="$2"; shift 2 ;;
            --name) name="$2"; shift 2 ;;
            --domain) domain="$2"; shift 2 ;;
            *) die "unknown option $1" ;;
        esac
    done
    name="${name:-${target#*@}}"
    [[ "$name" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || die "the server must have a host name or IPv4 address"
    [[ -f "$DIR/wg0.base" ]] || die "set up WireGuard first: sudo ./scripts/wireguard.sh setup --no-endpoint"
    local wg_port web ca=""
    wg_port="$(sed -n 's/^ListenPort *= *//p' "$DIR/wg0.base")"
    web="$(get_env PI_GUARD_DASHBOARD_PORT)"
    web="${web:-8443}"

    install -d -m 0700 "$DIR"
    [[ -f "$DIR/$IFACE.key" ]] || (umask 077; wg genkey > "$DIR/$IFACE.key")
    local mine
    mine="$(wg pubkey < "$DIR/$IFACE.key")"

    local extra=()
    if [[ -n "$domain" ]]; then
        local data
        data="$(get_env PI_GUARD_DATA_DIR)"
        ca="$(base64 -w0 "${data:-/var/lib/pi-guard}/tls/ca.crt")" || die "no dashboard certificate found"
        extra=("$domain" "$web" "$ca")
    fi

    say "setting up $target"
    local answer key
    answer="$(ssh -p "$ssh_port" "$target" bash -s -- setup "$mine" "$public" "$tunnel" "$wg_port" "${extra[@]}" \
        < "$REPO/deploy/relay/pi-guard-relay-server")" || die "the server could not be set up"
    key="$(printf '%s\n' "$answer" | sed -n 's/^RELAY_KEY=//p' | tail -1)"
    [[ "$key" =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]] || die "no key from the server"

    # Everything from the tunnel is refused except what is listed; it never reaches the LAN.
    local rules=(
        "iptables %s FORWARD -i $IFACE -j DROP"
        "iptables %s INPUT -i $IFACE -j DROP"
        "iptables %s INPUT -i $IFACE -p udp --dport $wg_port -j ACCEPT"
    )
    [[ -z "$domain" ]] || rules+=("iptables %s INPUT -i $IFACE -p tcp --dport $web -j ACCEPT")
    rules+=("iptables %s INPUT -i $IFACE -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT")
    (
        umask 077
        {
            printf '# Written by pi-guard scripts/relay.sh.\n[Interface]\n'
            printf 'Address = %s/30\nPrivateKey = %s\nMTU = 1420\n' "$PI" "$(cat "$DIR/$IFACE.key")"
            for rule in "${rules[@]}"; do
                # shellcheck disable=SC2059
                printf "PostUp = $rule\n" "-I"
            done
            for rule in "${rules[@]}"; do
                # shellcheck disable=SC2059
                printf "PostDown = $rule\n" "-D"
            done
            printf '\n[Peer]\nPublicKey = %s\nEndpoint = %s:%s\nAllowedIPs = %s/32\nPersistentKeepalive = 25\n' \
                "$key" "$name" "$tunnel" "$SERVER"
        } > "$DIR/$IFACE.conf"
    )
    systemctl enable --quiet wg-quick@"$IFACE"
    systemctl restart wg-quick@"$IFACE"
    install -m 0755 "$REPO/deploy/wireguard/pi-guard-wg-status" /usr/local/sbin/pi-guard-wg-status

    set_env PI_GUARD_WIREGUARD_RELAY "$name:$public"
    if [[ -n "$domain" ]]; then
        set_env PI_GUARD_DASHBOARD_PUBLIC_URL "https://$domain"
        add_to_list PI_GUARD_DASHBOARD_TRUSTED_PROXIES "$SERVER"
        add_to_list PI_GUARD_DASHBOARD_TLS_NAMES "$TLS_NAME"
    fi
    systemctl is-enabled --quiet pi-guard 2>/dev/null && systemctl restart pi-guard
    /usr/local/sbin/pi-guard-wg-status wg0 || true

    say "the relay runs on $name, UDP $public"
    echo "  The app picks it up the next time it is opened at home."
    echo "  Devices with a QR code need a new one only if this is their only way home."
    [[ -z "$domain" ]] || echo "  Dashboard: https://$domain. Turn on two-factor login, see docs/public-access.md."
}

remove() {
    local target="${1:-}"
    systemctl disable --now --quiet wg-quick@"$IFACE" 2>/dev/null || true
    rm -f "$DIR/$IFACE.conf" "$DIR/$IFACE.key"
    set_env PI_GUARD_WIREGUARD_RELAY
    if [[ "$(get_env PI_GUARD_DASHBOARD_TRUSTED_PROXIES)" == "$SERVER" ]]; then
        set_env PI_GUARD_DASHBOARD_TRUSTED_PROXIES
        set_env PI_GUARD_DASHBOARD_PUBLIC_URL
    fi
    systemctl is-enabled --quiet pi-guard 2>/dev/null && systemctl restart pi-guard
    /usr/local/sbin/pi-guard-wg-status wg0 || true
    if [[ -n "$target" ]]; then
        ssh "$target" bash -s -- remove < "$REPO/deploy/relay/pi-guard-relay-server" || die "the server kept its part"
    fi
    say "the relay is gone from this Pi"
}

case "${1:-}" in
    setup) shift; setup "$@" ;;
    remove) shift; remove "$@" ;;
    status) wg show "$IFACE" ;;
    *) die "usage: $0 setup USER@SERVER [--domain NAME] | status | remove [USER@SERVER]" ;;
esac

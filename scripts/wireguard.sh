#!/usr/bin/env bash
# WireGuard VPN for pi-guard: reach the home network (and Pi-hole) from anywhere.
#
#   sudo ./scripts/wireguard.sh setup [--endpoint myname.myfritz.net] [--port 51820]
#   sudo ./scripts/wireguard.sh endpoint myname.myfritz.net
#   sudo ./scripts/wireguard.sh status
#
# After setup, devices (phones of the family, laptops) are added and removed in
# the dashboard under Network > WireGuard; each gets a QR code for the
# WireGuard app. The router must forward UDP <port> to this device.
#
# What setup changes on this system:
#   - installs wireguard-tools, creates /etc/wireguard/wg0.{base,conf} (server key)
#   - enables IPv4 forwarding (/etc/sysctl.d/90-pi-guard-wireguard.conf)
#   - NAT for 10.8.0.0/24 towards the LAN while wg0 is up (PostUp/PostDown)
#   - ufw (if active): allow UDP <port>, DNS and the dashboard on wg0, routing wg0 -> LAN
#   - systemd: wg-quick@wg0, pi-guard-wg-status.timer, pi-guard-wg-apply.path
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd /
IFACE=wg0
SUBNET=10.8.0.0/24
SERVER_IP=10.8.0.1/24
ENV_FILE=/etc/pi-guard/pi-guard.env
DIR=/etc/wireguard

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

set_env() {  # set_env KEY VALUE: replace or append; VALUE is taken literally
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}

check_endpoint() {
    [[ "$1" =~ ^([A-Za-z0-9.-]{1,253}|\[[0-9a-fA-F:]+\])(:[0-9]{1,5})?$ ]] \
        || die "endpoint must be a host name or IP address, e.g. myname.myfritz.net"
}

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found; run scripts/install.sh first"

lan_device() { ip -4 route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}'; }

setup() {
    local port=51820 endpoint=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --endpoint) endpoint="$2"; shift 2 ;;
            --port) port="$2"; shift 2 ;;
            *) die "unknown option $1" ;;
        esac
    done
    [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1024 && "$port" -le 65535 ]] || die "--port must be 1024-65535"

    local dev home
    dev="$(lan_device)"
    [[ -n "$dev" ]] || die "no default route; is the network up?"
    home="$(ip -4 route show dev "$dev" scope link proto kernel | awk '{print $1; exit}')"
    say "LAN: $dev ($home)"

    if [[ -z "$endpoint" ]]; then
        endpoint="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
        [[ -n "$endpoint" ]] || die "could not find the public IP address; pass --endpoint"
        warn "using the current public IP $endpoint. It can change; a MyFRITZ!/DynDNS name is better:"
        warn "  sudo $0 endpoint <name>   (before handing out QR codes)"
    fi
    check_endpoint "$endpoint"

    command -v wg >/dev/null || {
        say "installing wireguard-tools"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq wireguard-tools
    }
    command -v iptables >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq iptables

    install -d -m 0700 "$DIR"
    if [[ ! -f "$DIR/$IFACE.base" ]]; then
        say "creating the server key"
        local key
        key="$(umask 077; wg genkey)"
        (umask 077; cat > "$DIR/$IFACE.base" <<CONF
# Written by pi-guard scripts/wireguard.sh. Devices are appended to $IFACE.conf
# by pi-guard-wg-apply; edit this file, not $IFACE.conf.
[Interface]
Address = $SERVER_IP
ListenPort = $port
PrivateKey = $key
PostUp = iptables -t nat -A POSTROUTING -s $SUBNET -o $dev -j MASQUERADE
PostDown = iptables -t nat -D POSTROUTING -s $SUBNET -o $dev -j MASQUERADE
CONF
        )
    else
        say "keeping the existing server key in $DIR/$IFACE.base"
        port="$(sed -n 's/^ListenPort *= *//p' "$DIR/$IFACE.base")"
    fi

    printf 'net.ipv4.ip_forward = 1\n' > /etc/sysctl.d/90-pi-guard-wireguard.conf
    sysctl -q -p /etc/sysctl.d/90-pi-guard-wireguard.conf

    if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
        say "firewall (ufw): VPN port, DNS and dashboard over the VPN, routing to the LAN"
        ufw allow "$port"/udp comment 'WireGuard' >/dev/null
        ufw allow in on "$IFACE" to any port 53 comment 'Pi-hole DNS over VPN' >/dev/null
        ufw allow in on "$IFACE" to any port 8443 proto tcp comment 'pi-guard dashboard over VPN' >/dev/null
        ufw allow in on "$IFACE" to any port 80 proto tcp comment 'Pi-hole web over VPN' >/dev/null
        ufw route allow in on "$IFACE" out "$dev" comment 'VPN to LAN and internet' >/dev/null
    fi

    install -m 0755 "$REPO/deploy/wireguard/pi-guard-wg-status" /usr/local/sbin/pi-guard-wg-status
    install -m 0755 "$REPO/deploy/wireguard/pi-guard-wg-apply" /usr/local/sbin/pi-guard-wg-apply
    install -m 0644 "$REPO"/deploy/wireguard/pi-guard-wg-{status.service,status.timer,apply.service,apply.path} \
        /etc/systemd/system/
    systemctl daemon-reload

    /usr/local/sbin/pi-guard-wg-apply "$IFACE"
    systemctl enable --quiet wg-quick@"$IFACE"
    systemctl restart wg-quick@"$IFACE"
    systemctl enable --now --quiet pi-guard-wg-status.timer pi-guard-wg-apply.path

    set_env PI_GUARD_WIREGUARD_ENABLED true
    set_env PI_GUARD_WIREGUARD_INTERFACE "$IFACE"
    set_env PI_GUARD_WIREGUARD_ENDPOINT "$endpoint"
    [[ -n "$home" ]] && set_env PI_GUARD_WIREGUARD_HOME_NETWORK "$home"
    systemctl is-enabled --quiet pi-guard 2>/dev/null && systemctl restart pi-guard
    /usr/local/sbin/pi-guard-wg-status "$IFACE" || true

    say "WireGuard is running on UDP port $port"
    echo
    echo "Next steps:"
    echo "  1. Router: forward UDP $port to this device (FRITZ!Box: Internet > Freigaben >"
    echo "     Portfreigaben > Gerät hinzufügen > Neue Freigabe > Portfreigabe, UDP $port)."
    echo "  2. Dashboard > Network > WireGuard > Add device; scan the QR code in the WireGuard app."
}

endpoint() {
    [[ $# -eq 1 ]] || die "usage: $0 endpoint <host[:port]>"
    check_endpoint "$1"
    set_env PI_GUARD_WIREGUARD_ENDPOINT "$1"
    systemctl restart pi-guard
    say "endpoint set to $1. Devices added before need their QR code scanned again."
}

case "${1:-}" in
    setup) shift; setup "$@" ;;
    endpoint) shift; endpoint "$@" ;;
    status) wg show "$IFACE" ;;
    *) die "usage: $0 setup [--endpoint HOST] [--port PORT] | endpoint HOST | status" ;;
esac

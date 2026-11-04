#!/usr/bin/env bash
# The block page: a second address for the Pi, whose web ports lead to pi-guard.
#
#   sudo ./scripts/blockpage.sh setup 192.168.178.110
#   sudo ./scripts/blockpage.sh remove
#
# The address must be free and outside the router's DHCP range. What setup changes:
#   - adds that address to the active connection (NetworkManager)
#   - redirects its ports 80/443 to pi-guard (8088/8444) with iptables, kept by a systemd unit
#   - Pi-hole: allows the app password to change settings (webserver.api.app_sudo)
#   - sets PI_GUARD_BLOCKPAGE_IP
set -euo pipefail
cd /
ENV_FILE=/etc/pi-guard/pi-guard.env
UNIT=/etc/systemd/system/pi-guard-blockpage.service
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"

set_env() {
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
    [[ -n "$2" ]] && printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}

connection() { nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2 ~ /ethernet|wireless/ {print $1; exit}'; }

case "${1:-}" in
    setup)
        ip="${2:-}"
        [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "usage: $0 setup <free address>"
        ping -c 1 -W 1 "$ip" >/dev/null 2>&1 && die "$ip is already in use"
        con="$(connection)"
        prefix="$(nmcli -g ipv4.addresses connection show "$con" | head -1 | cut -d/ -f2)"
        [[ -n "$prefix" ]] || die "the Pi needs a fixed address first (scripts/network.sh static)"
        nmcli connection modify "$con" +ipv4.addresses "$ip/$prefix"
        nmcli device reapply "$(nmcli -g GENERAL.DEVICES connection show "$con")" >/dev/null
        cat > "$UNIT" <<UNITFILE
[Unit]
Description=Web ports of the pi-guard block page
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/iptables -t nat -A PREROUTING -d $ip -p tcp --dport 80 -j REDIRECT --to-port 8088
ExecStart=/usr/sbin/iptables -t nat -A PREROUTING -d $ip -p tcp --dport 443 -j REDIRECT --to-port 8444
ExecStop=/usr/sbin/iptables -t nat -D PREROUTING -d $ip -p tcp --dport 80 -j REDIRECT --to-port 8088
ExecStop=/usr/sbin/iptables -t nat -D PREROUTING -d $ip -p tcp --dport 443 -j REDIRECT --to-port 8444

[Install]
WantedBy=multi-user.target
UNITFILE
        systemctl daemon-reload
        systemctl enable --now --quiet pi-guard-blockpage
        if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
            ufw allow proto tcp to any port 8088 comment 'pi-guard block page' >/dev/null
            ufw allow proto tcp to any port 8444 comment 'pi-guard block page' >/dev/null
        fi
        pihole-FTL --config webserver.api.app_sudo true >/dev/null
        set_env PI_GUARD_BLOCKPAGE_IP "$ip"
        systemctl restart pi-guard
        say "block page on $ip. Devices with the pi-guard certificate see it for HTTPS too."
        ;;
    remove)
        ip="$(sed -n 's/^PI_GUARD_BLOCKPAGE_IP=//p' "$ENV_FILE")"
        [[ -n "$ip" ]] || die "not set up"
        systemctl disable --now --quiet pi-guard-blockpage || true
        rm -f "$UNIT"
        systemctl daemon-reload
        con="$(connection)"
        prefix="$(nmcli -g ipv4.addresses connection show "$con" | head -1 | cut -d/ -f2)"
        nmcli connection modify "$con" -ipv4.addresses "$ip/$prefix" || true
        nmcli device reapply "$(nmcli -g GENERAL.DEVICES connection show "$con")" >/dev/null || true
        set_env PI_GUARD_BLOCKPAGE_IP ""
        systemctl restart pi-guard
        say "block page removed"
        ;;
    *) die "usage: $0 setup <address> | remove" ;;
esac

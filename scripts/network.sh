#!/usr/bin/env bash
# Give the Raspberry Pi a fixed local IP address (NetworkManager, Raspberry Pi OS Bookworm+).
#
#   sudo ./scripts/network.sh show
#   sudo ./scripts/network.sh static [--ip 192.168.178.10/24] [--gateway 192.168.178.1] [--dns 127.0.0.1]
#   sudo ./scripts/network.sh dhcp
#
# Safety: before switching, a rollback is scheduled for 120 seconds later. It is
# only cancelled if you can still confirm afterwards, so a lost SSH connection
# restores the previous settings by itself.
#
# A fixed address can also be reserved in the router (FRITZ!Box: Heimnetz ›
# Netzwerk › device › "Diesem Netzwerkgerät immer die gleiche IPv4-Adresse
# zuweisen"), which needs no change on the Pi and is often the better choice.
set -euo pipefail

ROLLBACK_SECONDS=120
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || die "run with sudo"
command -v nmcli >/dev/null || die "NetworkManager (nmcli) not found. On older systems with dhcpcd, edit /etc/dhcpcd.conf instead (see docs/network.md)."

CON="$(nmcli -t -f NAME,DEVICE,TYPE connection show --active | awk -F: '$3 ~ /ethernet|wireless/ {print $1; exit}')"
[[ -n "$CON" ]] || die "no active Ethernet or Wi-Fi connection"
DEV="$(nmcli -g GENERAL.DEVICES connection show "$CON")"
CUR_IP="$(nmcli -g IP4.ADDRESS device show "$DEV" | head -1)"
CUR_GW="$(nmcli -g IP4.GATEWAY device show "$DEV")"
CUR_DNS="$(nmcli -g IP4.DNS device show "$DEV" | paste -sd, -)"
OLD_METHOD="$(nmcli -g ipv4.method connection show "$CON")"
OLD_ADDR="$(nmcli -g ipv4.addresses connection show "$CON")"
OLD_GW="$(nmcli -g ipv4.gateway connection show "$CON")"
OLD_DNS="$(nmcli -g ipv4.dns connection show "$CON")"

show() {
    echo "connection: $CON ($DEV)"
    echo "method:     $OLD_METHOD"
    echo "address:    $CUR_IP"
    echo "gateway:    $CUR_GW"
    echo "dns:        $CUR_DNS"
}

schedule_rollback() {
    local restore="nmcli connection modify '$CON' ipv4.method '$OLD_METHOD' ipv4.addresses '$OLD_ADDR' ipv4.gateway '$OLD_GW' ipv4.dns '$OLD_DNS' && nmcli connection up '$CON'"
    [[ "$OLD_METHOD" == "auto" ]] && restore="nmcli connection modify '$CON' ipv4.method auto ipv4.addresses '' ipv4.gateway '' ipv4.dns '' && nmcli connection up '$CON'"
    systemd-run --quiet --unit=pi-guard-network-rollback --on-active="$ROLLBACK_SECONDS" \
        /bin/sh -c "$restore"
}

apply() {
    schedule_rollback
    say "applying; the connection may drop for a moment"
    "$@"
    nmcli connection up "$CON" >/dev/null
    echo
    echo "If you can read this, the network still works."
    if read -r -t $((ROLLBACK_SECONDS - 10)) -p "Keep the new settings? [y/N] " answer && [[ "$answer" =~ ^[YyJj] ]]; then
        systemctl stop pi-guard-network-rollback.timer 2>/dev/null || true
        say "kept"
        show
    else
        say "not confirmed; the previous settings come back within $ROLLBACK_SECONDS seconds"
    fi
}

case "${1:-show}" in
    show)
        show ;;
    static)
        shift
        IP="${CUR_IP}" GW="${CUR_GW}" DNS="${CUR_DNS:-$CUR_GW}"
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --ip) IP="$2"; shift 2 ;;
                --gateway) GW="$2"; shift 2 ;;
                --dns) DNS="$2"; shift 2 ;;
                *) die "unknown option $1" ;;
            esac
        done
        [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$ ]] || die "--ip must look like 192.168.178.10/24"
        show
        echo
        echo "new address: $IP, gateway $GW, DNS $DNS"
        echo "Make sure the address is outside the router's DHCP range or reserved for this device."
        read -r -p "Continue? [y/N] " answer
        [[ "$answer" =~ ^[YyJj] ]] || exit 1
        apply nmcli connection modify "$CON" ipv4.method manual ipv4.addresses "$IP" \
            ipv4.gateway "$GW" ipv4.dns "${DNS//,/ }" ;;
    dhcp)
        show
        read -r -p "Switch $CON back to an automatic (DHCP) address? [y/N] " answer
        [[ "$answer" =~ ^[YyJj] ]] || exit 1
        apply nmcli connection modify "$CON" ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "" ;;
    *)
        die "usage: $0 show | static [--ip A/NN] [--gateway G] [--dns D] | dhcp" ;;
esac

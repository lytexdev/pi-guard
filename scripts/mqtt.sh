#!/usr/bin/env bash
# MQTT broker for smart home devices: Mosquitto with passwords, for the home network.
#
#   sudo ./scripts/mqtt.sh setup            install, configure, connect pi-guard
#   sudo ./scripts/mqtt.sh user NAME        add a login for a device (asks nothing, prints it)
#   sudo ./scripts/mqtt.sh status
#
# What setup changes: installs mosquitto, writes /etc/mosquitto/conf.d/pi-guard.conf and
# /etc/mosquitto/pi-guard.passwd, and with active ufw allows port 1883 from the home network.
set -euo pipefail
cd /
ENV_FILE=/etc/pi-guard/pi-guard.env
OVERRIDES=/var/lib/pi-guard/dashboard.env
CONF=/etc/mosquitto/conf.d/pi-guard.conf
PASSWD=/etc/mosquitto/pi-guard.passwd

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"

set_env() {
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"
    rm -f "$tmp"
}

add_user() {
    local name="$1" password="$2"
    [[ "$name" =~ ^[A-Za-z0-9_-]{1,32}$ ]] || die "user names: letters, digits, _ and -"
    [[ -f "$PASSWD" ]] || install -m 0640 -o root -g mosquitto /dev/null "$PASSWD"
    mosquitto_passwd -b "$PASSWD" "$name" "$password"
    chown root:mosquitto "$PASSWD"
    chmod 0640 "$PASSWD"
}

setup() {
    [[ -f "$ENV_FILE" ]] || die "$ENV_FILE not found; run scripts/install.sh first"
    command -v mosquitto >/dev/null || {
        say "installing mosquitto"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mosquitto mosquitto-clients
    }
    cat > "$CONF" <<CONFIG
# Written by pi-guard scripts/mqtt.sh
listener 1883
allow_anonymous false
password_file $PASSWD
CONFIG
    local password
    password="$(openssl rand -hex 24)"
    add_user pi-guard "$password"
    set_env PI_GUARD_MQTT_ENABLED true
    set_env PI_GUARD_MQTT_HOST 127.0.0.1
    set_env PI_GUARD_MQTT_PORT 1883
    set_env PI_GUARD_MQTT_USERNAME pi-guard
    set_env PI_GUARD_MQTT_PASSWORD "$password"
    # Values saved in the dashboard would otherwise win over these.
    [[ -f "$OVERRIDES" ]] && sed -i '/^PI_GUARD_MQTT_/d' "$OVERRIDES"
    if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
        local net
        net="$(ip -4 route show scope link proto kernel | awk '$1 !~ /^(172\.|10\.8\.)/ {print $1; exit}')"
        [[ -n "$net" ]] && ufw allow from "$net" to any port 1883 proto tcp comment 'MQTT LAN' >/dev/null
    fi
    systemctl enable --quiet mosquitto
    systemctl restart mosquitto
    systemctl is-enabled --quiet pi-guard 2>/dev/null && systemctl restart pi-guard
    say "MQTT runs on port 1883. Add a login per device: sudo $0 user NAME"
}

case "${1:-}" in
    setup) setup ;;
    user)
        [[ $# -eq 2 ]] || die "usage: $0 user NAME"
        password="$(openssl rand -hex 12)"
        add_user "$2" "$password"
        systemctl reload-or-restart mosquitto
        say "user $2, password $password"
        ;;
    status) systemctl --no-pager status mosquitto | head -5 ;;
    *) die "usage: $0 setup | user NAME | status" ;;
esac

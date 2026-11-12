#!/usr/bin/env bash
# An obfuscated way home: AmneziaWG (awg0) next to WireGuard (wg0) on your pi-guard.
#
#   sudo ./scripts/amneziawg.sh install [PORT]   # default UDP 443, or the port chosen before
#   sudo ./scripts/amneziawg.sh status
#   sudo ./scripts/amneziawg.sh remove
#
# AmneziaWG is WireGuard with its packets disguised (junk packets, padding, other headers), so
# networks that block WireGuard by its look let it through. Phones with app 1.8 or newer try it
# when the normal ways home do not answer. Devices, keys and addresses stay those of wg0.
#
# What install changes on this system:
#   - builds the amneziawg kernel module with DKMS and the awg/awg-quick tools, from fixed
#     versions whose SHA-256 is checked below (packages: dkms, build-essential, curl)
#   - /etc/amnezia/amneziawg/awg0.conf (the Pi's WireGuard key, the port, the disguise),
#     /var/lib/pi-guard-awg/params (the port and the disguise for the apps, no key)
#   - systemd: awg-quick@awg0 and pi-guard-awg-sync (devices and routes from wg0)
#   - ufw (if active): the UDP port from the internet; DNS, dashboard and key exchange on awg0;
#     routing awg0 -> LAN and internet
# Not changed: the router. Forward the UDP port there to this Pi yourself.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IFACE=awg0
CONF_DIR=/etc/amnezia/amneziawg
CONF="$CONF_DIR/$IFACE.conf"
STATE=/var/lib/pi-guard-awg
PARAMS="$STATE/params"
DEFAULT_PORT=443
MODULE_VERSION=1.0.0 # what the module's dkms.conf calls itself, for every release
KMOD_TAG=v3.1.20260906
KMOD_SHA=e32fa46f1b6f9e319c5261f0b0765c3a9261e40cb7c4616fd73500da961a2932
TOOLS_TAG=v3.1.20260812
TOOLS_SHA=dbd8ce0748d835d18f30bb76720246b7bfc80bd09cd17c379b1c59f683a18493
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '==> %s\n' "$*"; }
[[ $EUID -eq 0 ]] || die "run with sudo"

fetch() { # repo tag sha256 folder
    local file="$4/$1.tar.gz"
    curl -fsSL --proto '=https' -o "$file" "https://github.com/amnezia-vpn/$1/archive/refs/tags/$2.tar.gz"
    echo "$3  $file" | sha256sum -c --quiet - || die "$1 $2 does not match its checksum; nothing was installed"
    tar xzf "$file" -C "$4"
}

# A number in [low, high] from the kernel's random source.
random_in() {
    local low="$1" high="$2" raw
    raw="$(od -An -N4 -tu4 /dev/urandom | tr -d ' ')"
    echo $(( low + raw % (high - low + 1) ))
}

make_params() { # port
    local s1 s2 h=() value
    s1="$(random_in 15 150)"
    while :; do s2="$(random_in 15 150)"; (( s1 + 56 != s2 )) && break; done
    while (( ${#h[@]} < 4 )); do
        value="$(random_in 5 2147483647)"
        [[ " ${h[*]} " == *" $value "* ]] || h+=("$value")
    done
    install -d -m 0755 "$STATE"
    {
        echo "port=$1"
        echo "jc=$(random_in 4 12)"
        echo "jmin=8"
        echo "jmax=80"
        echo "s1=$s1"
        echo "s2=$s2"
        echo "h1=${h[0]}"
        echo "h2=${h[1]}"
        echo "h3=${h[2]}"
        echo "h4=${h[3]}"
    } > "$PARAMS.tmp"
    chmod 0644 "$PARAMS.tmp"
    mv "$PARAMS.tmp" "$PARAMS"
}

param() { sed -n "s/^$1=\([0-9]*\)$/\1/p" "$PARAMS"; }

write_conf() {
    local key
    key="$(sed -n 's/^PrivateKey *= *//p' /etc/wireguard/wg0.base | head -1)"
    [[ -n "$key" ]] || die "no PrivateKey in /etc/wireguard/wg0.base"
    install -d -m 0700 "$CONF_DIR"
    umask 077
    # Table = off: the routes come from pi-guard-awg-sync, one device at a time.
    cat > "$CONF.tmp" <<CONF
[Interface]
PrivateKey = $key
ListenPort = $(param port)
Table = off
Jc = $(param jc)
Jmin = $(param jmin)
Jmax = $(param jmax)
S1 = $(param s1)
S2 = $(param s2)
H1 = $(param h1)
H2 = $(param h2)
H3 = $(param h3)
H4 = $(param h4)
CONF
    mv "$CONF.tmp" "$CONF"
    umask 022
}

firewall() { # allow|delete port
    command -v ufw >/dev/null && ufw status | grep -q '^Status: active' || return 0
    local verb=() dev
    [[ "$1" == delete ]] && verb=(delete)
    dev="$(ip route show default | awk '{for (i = 1; i < NF; i++) if ($i == "dev") {print $(i + 1); exit}}')"
    ufw "${verb[@]}" allow "$2"/udp comment 'AmneziaWG' >/dev/null || true
    ufw "${verb[@]}" allow in on "$IFACE" to any port 53 comment 'Pi-hole DNS over VPN' >/dev/null || true
    ufw "${verb[@]}" allow in on "$IFACE" to any port 8443 proto tcp comment 'pi-guard dashboard over VPN' >/dev/null || true
    ufw "${verb[@]}" allow in on "$IFACE" to any port 80 proto tcp comment 'Pi-hole web over VPN' >/dev/null || true
    ufw "${verb[@]}" allow in on "$IFACE" to any port 9999 proto tcp comment 'pi-guard post-quantum keys' >/dev/null || true
    [[ -n "$dev" ]] && { ufw "${verb[@]}" route allow in on "$IFACE" out "$dev" comment 'VPN to LAN and internet' >/dev/null || true; }
}

install_all() {
    local port="${1:-}"
    [[ -f /etc/wireguard/wg0.base ]] || die "set up WireGuard first: scripts/wireguard.sh setup"
    [[ -d "/lib/modules/$(uname -r)/build" ]] || die "kernel headers for $(uname -r) are missing (Raspberry Pi: apt install linux-headers-rpi-v8 or -2712, then reboot)"
    if [[ -z "$port" && -f "$PARAMS" ]]; then port="$(param port)"; fi
    port="${port:-$DEFAULT_PORT}"
    [[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1 && port <= 65535 )) || die "port must be 1-65535"
    if ss -Hlun "sport = :$port" | grep -q . && ! ip link show "$IFACE" >/dev/null 2>&1; then
        die "UDP port $port is in use; choose another: $0 install PORT"
    fi
    say "packages: dkms, build-essential, curl"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq dkms build-essential curl >/dev/null
    WORK="$(mktemp -d)"
    trap 'rm -rf "$WORK"' EXIT
    local work="$WORK"
    say "downloading AmneziaWG $KMOD_TAG (module) and $TOOLS_TAG (tools), checking checksums"
    fetch amneziawg-linux-kernel-module "$KMOD_TAG" "$KMOD_SHA" "$work"
    fetch amneziawg-tools "$TOOLS_TAG" "$TOOLS_SHA" "$work"
    say "building the kernel module (DKMS, rebuilt by itself after kernel updates)"
    if dkms status -m amneziawg -v "$MODULE_VERSION" | grep -q .; then
        dkms remove -m amneziawg -v "$MODULE_VERSION" --all >/dev/null || true
    fi
    rm -rf "/usr/src/amneziawg-$MODULE_VERSION"
    make -s -C "$work"/amneziawg-linux-kernel-module-*/src dkms-install >/dev/null
    dkms add -m amneziawg -v "$MODULE_VERSION" >/dev/null
    dkms build -m amneziawg -v "$MODULE_VERSION" >/dev/null
    dkms install -m amneziawg -v "$MODULE_VERSION" --force >/dev/null
    modprobe amneziawg
    say "building awg and awg-quick"
    make -s -C "$work"/amneziawg-tools-*/src >/dev/null
    make -s -C "$work"/amneziawg-tools-*/src install WITH_WGQUICK=yes WITH_SYSTEMDUNITS=yes WITH_BASHCOMPLETION=no >/dev/null

    if [[ ! -f "$PARAMS" ]]; then
        say "choosing the disguise (kept from now on: the apps carry the same values)"
        make_params "$port"
    elif [[ "$(param port)" != "$port" ]]; then
        firewall delete "$(param port)"
        sed -i "s/^port=.*/port=$port/" "$PARAMS"
    fi
    write_conf
    install -m 0755 "$REPO/deploy/amneziawg/pi-guard-awg-sync" /usr/local/sbin/
    install -m 0644 "$REPO/deploy/amneziawg/pi-guard-awg-sync.service" /etc/systemd/system/
    firewall allow "$port"
    systemctl daemon-reload
    systemctl enable --quiet "awg-quick@$IFACE" pi-guard-awg-sync
    systemctl restart "awg-quick@$IFACE"
    systemctl restart pi-guard-awg-sync # a running one would keep the old program
    # A running privacy exit sets its rules for awg0 now (pi-guard-exit-apply sets them every run).
    systemctl start pi-guard-exit-apply.service 2>/dev/null || true
    say "running on UDP $port. Forward UDP $port on your router to this Pi; nothing else is needed there."
    say "pi-guard offers it to the apps after its next restart (Dashboard > Network > VPN)."
}

status() {
    [[ -f "$PARAMS" ]] && echo "port $(param port)" || echo "not set up"
    systemctl is-active "awg-quick@$IFACE" pi-guard-awg-sync 2>/dev/null | paste -sd' ' | sed 's/^/services: /'
    command -v awg >/dev/null && awg show "$IFACE" latest-handshakes 2>/dev/null | awk '$2 > 0 {n++} END {print n + 0 " device(s) seen on awg0"}'
}

remove() {
    local port=""
    [[ -f "$PARAMS" ]] && port="$(param port)"
    systemctl disable --now pi-guard-awg-sync "awg-quick@$IFACE" 2>/dev/null || true
    rm -f /etc/systemd/system/pi-guard-awg-sync.service /usr/local/sbin/pi-guard-awg-sync "$CONF"
    [[ -n "$port" ]] && firewall delete "$port"
    rm -rf "$STATE"
    dkms remove -m amneziawg -v "$MODULE_VERSION" --all >/dev/null 2>&1 || true
    rm -rf "/usr/src/amneziawg-$MODULE_VERSION"
    modprobe -r amneziawg 2>/dev/null || true
    rm -f /usr/bin/awg /usr/bin/awg-quick /usr/lib/systemd/system/awg-quick@.service /usr/lib/systemd/system/awg-quick.target
    systemctl daemon-reload
    echo "removed. The router's port forwarding for UDP ${port:-?} can go as well."
}

case "${1:-}" in
    install) shift; install_all "$@" ;;
    status) status ;;
    remove) remove ;;
    *) die "usage: $0 install [PORT] | status | remove" ;;
esac

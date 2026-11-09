#!/usr/bin/env bash
# The VPN service for the desktop app on Linux.
#
#   sudo ./scripts/install-vpn.sh            # after: cd vpn && cargo build --release
#   sudo ./scripts/install-vpn.sh remove
#
# What this changes on this system:
#   - installs wireguard-tools and nftables if they are missing
#   - /usr/local/sbin/pi-guard-vpn, /usr/local/lib/pi-guard-vpn/rosenpass, pi-guard-vpn.service
#   - only the user who runs this may control the service
#
# The service runs as root, because tunnels and firewall rules need it. The app itself does not.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION=0.2.3
FULL_SUM=53158a8c339e3be1a9520b91c33a9e83eab05ea49af346cdb7e6eec522f2318b
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"

if [[ "${1:-}" == remove ]]; then
    /usr/local/sbin/pi-guard-vpn unlock 2>/dev/null || true
    systemctl disable --now pi-guard-vpn 2>/dev/null || true
    /usr/local/sbin/pi-guard-vpn unlock 2>/dev/null || true
    rm -rf /etc/modules-load.d/pi-guard-vpn.conf /etc/systemd/system/pi-guard-vpn.service /usr/local/sbin/pi-guard-vpn /usr/local/lib/pi-guard-vpn /etc/pi-guard-vpn /var/lib/pi-guard-vpn
    systemctl daemon-reload
    echo "removed"
    exit 0
fi

need() {
    command -v wg >/dev/null && command -v nft >/dev/null && return
    if command -v pacman >/dev/null; then pacman -S --needed --noconfirm wireguard-tools nftables
    elif command -v apt-get >/dev/null; then apt-get install -y wireguard-tools nftables
    elif command -v dnf >/dev/null; then dnf install -y wireguard-tools nftables
    elif command -v zypper >/dev/null; then zypper -n install wireguard-tools nftables
    else die "install wireguard-tools and nftables first"; fi
}
need
# Installing wg-quick wakes up tunnels that were enabled long ago, and they are not ours to touch.
old_tunnels() { find /etc/systemd/system -name 'wg-quick@*.service' -printf '%f\n' 2>/dev/null | sort -u; }
BIN="$REPO/vpn/target/release/pi-guard-vpn"
[[ -x "$BIN" ]] || die "build first: cd vpn && cargo build --release"
[[ "$(uname -m)" == x86_64 ]] || die "Rosenpass has no ready release for $(uname -m); build it and put it into /usr/local/lib/pi-guard-vpn"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
curl -fsSL -o "$work/rp.tar" "https://github.com/rosenpass/rosenpass/releases/download/v$VERSION/rosenpass-x86_64-linux-$VERSION.tar"
sha256sum "$work/rp.tar" | grep -q "^$FULL_SUM" || die "Rosenpass does not match its checksum, stopped"
tar -xf "$work/rp.tar" -C "$work" bin/rosenpass

install -m 0755 "$BIN" /usr/local/sbin/pi-guard-vpn
install -d -m 0755 /usr/local/lib/pi-guard-vpn
install -m 0755 "$work/bin/rosenpass" /usr/local/lib/pi-guard-vpn/rosenpass
install -d -m 0755 /etc/pi-guard-vpn
id -u "${SUDO_USER:-root}" > /etc/pi-guard-vpn/users
install -m 0644 "$REPO/deploy/vpn/pi-guard-vpn.service" /etc/systemd/system/
# Loaded at boot: a kernel update removes the running kernel's modules from the disk, and what is
# not in memory by then cannot be loaded until the next restart.
install -m 0644 "$REPO/deploy/vpn/pi-guard-vpn-modules.conf" /etc/modules-load.d/pi-guard-vpn.conf
for module in $(grep -v '^#' "$REPO/deploy/vpn/pi-guard-vpn-modules.conf"); do
    modprobe "$module" 2>/dev/null || echo "$module cannot be loaded yet: restart this computer if its kernel was just updated"
done
systemctl daemon-reload
systemctl enable pi-guard-vpn
systemctl restart pi-guard-vpn # a running service would keep the old program
echo "running. Open the VPN page in the desktop app."
for unit in $(old_tunnels); do
    echo "warning: $unit is enabled here and starts with this computer. If it is an old one, it can take over"
    echo "         the home network: sudo systemctl disable --now $unit"
done
echo "If the kill switch or lockdown ever locks you out: sudo systemctl stop pi-guard-vpn && sudo pi-guard-vpn unlock"

#!/usr/bin/env bash
# pgproxy, the reverse proxy of pi-guard, on this machine.
#
#   sudo ./scripts/install-proxy.sh [BINARY]          # installs /usr/local/bin/pgproxy
#   sudo ./scripts/install-proxy.sh service server    # runs /etc/pgproxy/server.json at boot
#   sudo ./scripts/install-proxy.sh service client    # runs /etc/pgproxy/client.json at boot
#   sudo ./scripts/install-proxy.sh remove
#
# BINARY defaults to proxy/target/release/pgproxy (cd proxy && cargo build --release), or take
# pgproxy-linux-ARCH from a GitHub release and check it against SHA256SUMS first.
# On a pi-guard Pi only the first step is needed: pi-guard starts pgproxy itself.
# Nothing here opens a port in a firewall or router.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "run with sudo"

case "${1:-}" in
    remove)
        for role in server client; do systemctl disable --now "pgproxy@$role" 2>/dev/null || true; done
        rm -f /etc/systemd/system/pgproxy@.service /usr/local/bin/pgproxy
        systemctl daemon-reload
        echo "removed; /etc/pgproxy and the pgproxy user are kept"
        ;;
    service)
        role="${2:-}"
        [[ "$role" == server || "$role" == client ]] || die "service server|client"
        [[ -x /usr/local/bin/pgproxy ]] || die "install the binary first"
        config="/etc/pgproxy/$role.json"
        [[ -f "$config" ]] || die "$config is missing: see docs/proxy.md"
        id pgproxy >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin pgproxy
        chown root:pgproxy /etc/pgproxy && chmod 0750 /etc/pgproxy
        find /etc/pgproxy -maxdepth 1 -type f -exec chown root:pgproxy {} + -exec chmod 0640 {} +
        /usr/local/bin/pgproxy check "$role" "$config"
        install -m 0644 "$REPO/deploy/proxy/pgproxy@.service" /etc/systemd/system/pgproxy@.service
        systemctl daemon-reload
        systemctl enable "pgproxy@$role"
        systemctl restart "pgproxy@$role"
        echo "pgproxy@$role is running"
        ;;
    *)
        binary="${1:-$REPO/proxy/target/release/pgproxy}"
        [[ -x "$binary" ]] || die "no pgproxy at $binary: cd proxy && cargo build --release"
        "$binary" token >/dev/null || die "$binary does not run on this machine"
        install -m 0755 -o root -g root "$binary" /usr/local/bin/pgproxy
        install -d -m 0700 -o root -g root /etc/pgproxy
        echo "installed /usr/local/bin/pgproxy"
        ;;
esac

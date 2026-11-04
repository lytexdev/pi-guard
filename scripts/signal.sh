#!/usr/bin/env bash
# signal-cli for pi-guard: installs it and runs it as a local service.
#
#   sudo ./scripts/signal.sh install
#   sudo ./scripts/signal.sh update
#   sudo ./scripts/signal.sh status
#
# After install, link your phone in the dashboard: Settings > Messenger > Signal.
# signal-cli is unofficial software; see docs/signal.md.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd /
DATA=/var/lib/pi-guard/signal-cli
PROJECT=AsamK/signal-cli

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"

java_package() {  # the newest headless Java this system offers
    apt-cache search --names-only '^openjdk-[0-9]+-jre-headless$' | awk '{print $1}' | sort -t- -k2 -n | tail -1
}

fetch() {
    local tag version tmp
    tag="$(curl -fsS "https://api.github.com/repos/$PROJECT/releases/latest" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')"
    version="${tag#v}"
    [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "unexpected version: $tag"
    if [[ ! -x "/opt/signal-cli-$version/bin/signal-cli" ]]; then
        say "downloading signal-cli $version"
        tmp="$(mktemp -d)"
        curl -fsSL -o "$tmp/signal-cli.tar.gz" \
            "https://github.com/$PROJECT/releases/download/$tag/signal-cli-$version.tar.gz"
        tar xzf "$tmp/signal-cli.tar.gz" -C /opt
        rm -r "$tmp"
    fi
    ln -sfn "/opt/signal-cli-$version/bin/signal-cli" /usr/local/bin/signal-cli
}

install_all() {
    id pi-guard >/dev/null 2>&1 || die "run scripts/install.sh first"
    apt-get update -qq
    local java
    java="$(java_package)"
    [[ -n "$java" ]] || die "no Java package found"
    say "installing $java"
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$java"
    fetch
    install -d -o pi-guard -g pi-guard -m 0700 "$DATA"
    install -m 0644 "$REPO/deploy/systemd/pi-guard-signal.service" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --quiet pi-guard-signal
    systemctl restart pi-guard-signal
    say "signal-cli is running. Link your phone in the dashboard: Settings > Messenger > Signal."
}

case "${1:-}" in
    install) install_all ;;
    update) fetch; systemctl restart pi-guard-signal; say "updated" ;;
    status) signal-cli --version; systemctl --no-pager status pi-guard-signal | head -5 ;;
    *) die "usage: $0 install | update | status" ;;
esac

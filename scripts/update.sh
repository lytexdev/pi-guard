#!/usr/bin/env bash
# Update pi-guard from its Git repository. Installed as /usr/local/sbin/pi-guard-update and run
# as root by pi-guard-update.path when the dashboard asks; `sudo ./scripts/update.sh` works too.
#
# Installs only releases signed with the key in SIGNERS, so GitHub alone cannot run anything here
# as root; see docs/updates.md. Only fast-forwards: local changes or a different history stop the
# update. After installing, the service must report healthy within a minute, otherwise the
# previous version comes back.
set -uo pipefail
PREFIX=/opt/pi-guard
SIGNERS="$PREFIX/release-signers"  # root's, and not taken from the repository during an update
CHECK=/usr/local/sbin/pi-guard-release-check
CONF=/etc/pi-guard/update.conf
DATA=/var/lib/pi-guard
STATE="$DATA/update"
REPORTS=/var/lib/pi-guard-scan
ENV_FILE=/etc/pi-guard/pi-guard.env

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -f "$CONF" ]] || { echo "$CONF missing; run scripts/install.sh" >&2; exit 1; }
# shellcheck source=/dev/null
. "$CONF"   # REPO, OWNER
mkdir -p "$STATE"
rm -f "$STATE/request"

report() {  # report STATE MESSAGE
    # In root's own folder: pi-guard must not be able to leave a link where root writes.
    install -d -m 0750 -o root -g pi-guard "$REPORTS"
    printf '{"state": "%s", "message": "%s", "time": "%s"}\n' "$1" "$2" "$(date -u +%FT%TZ)" > "$REPORTS/update.tmp"
    chgrp pi-guard "$REPORTS/update.tmp"
    chmod 0640 "$REPORTS/update.tmp"
    mv -f "$REPORTS/update.tmp" "$REPORTS/update.json"
    echo "$1: $2"
}
git_as_owner() { runuser -u "$OWNER" -- git -C "$REPO" "$@"; }

# Optional root parts already installed by their own scripts stay current (binaries and systemd
# units). First install stays the owner's step (privacy-server.sh, wireguard.sh, amneziawg.sh).
# Tor was removed in 1.14. A Tor that pi-guard started is stopped once, then its job goes.
retire_tor() {
    [[ -e /usr/local/sbin/pi-guard-tor-ctl || -e /etc/systemd/system/pi-guard-tor-ctl.path ]] || return 0
    systemctl disable --now pi-guard-tor-ctl.path 2>/dev/null || true
    rm -f /var/lib/pi-guard/privacy/tor.want /var/lib/pi-guard/privacy/tor.bridges
    [[ -x /usr/local/sbin/pi-guard-tor-ctl ]] && /usr/local/sbin/pi-guard-tor-ctl >/dev/null 2>&1 || true
    rm -f /usr/local/sbin/pi-guard-tor-ctl /etc/systemd/system/pi-guard-tor-ctl.path \
        /etc/systemd/system/pi-guard-tor-ctl.service /run/pi-guard-status/privacy.tor
    rm -f /var/lib/pi-guard-privacy/tor-started-by-us
}

refresh_helpers() {
    local src unit iface

    if [[ -e /usr/local/sbin/pi-guard-exit-apply || -f /var/lib/pi-guard/privacy/installed ]]; then
        src="$REPO/deploy/privacy"
        for unit in pi-guard-exit-apply pi-guard-privacy-nft pi-guard-privacy-status; do
            [[ -f "$src/$unit" ]] && install -m 0755 "$src/$unit" /usr/local/sbin/
        done
        for unit in \
            pi-guard-exit-apply.service pi-guard-exit-apply.path \
            pi-guard-privacy-nft.service pi-guard-privacy-nft.path \
            pi-guard-privacy-status.service pi-guard-privacy-status.timer
        do
            [[ -f "$src/$unit" ]] && install -m 0644 "$src/$unit" /etc/systemd/system/
        done
    fi

    retire_tor

    if [[ -e /usr/local/sbin/pi-guard-wg-apply ]]; then
        src="$REPO/deploy/wireguard"
        install -m 0755 "$src/pi-guard-wg-apply" /usr/local/sbin/pi-guard-wg-apply
        install -m 0755 "$src/pi-guard-wg-status" /usr/local/sbin/pi-guard-wg-status
        install -m 0644 "$src"/pi-guard-wg-{status.service,status.timer,apply.service,apply.path} \
            /etc/systemd/system/
    fi

    if [[ -e /usr/local/sbin/pi-guard-awg-sync ]]; then
        src="$REPO/deploy/amneziawg"
        install -m 0755 "$src/pi-guard-awg-sync" /usr/local/sbin/pi-guard-awg-sync
        install -m 0644 "$src/pi-guard-awg-sync.service" /etc/systemd/system/
    fi

    if [[ -e /etc/systemd/system/pi-guard-pq.service && -f "$REPO/deploy/vpn/pi-guard-pq.service" ]]; then
        install -m 0644 "$REPO/deploy/vpn/pi-guard-pq.service" /etc/systemd/system/
    fi
    if [[ -e /etc/modules-load.d/pi-guard-vpn-modules.conf \
          && -f "$REPO/deploy/vpn/pi-guard-vpn-modules.conf" ]]; then
        install -m 0644 "$REPO/deploy/vpn/pi-guard-vpn-modules.conf" \
            /etc/modules-load.d/pi-guard-vpn-modules.conf
    fi

    if [[ -e /etc/systemd/system/pi-guard-update.path ]]; then
        install -m 0644 "$REPO/deploy/update/pi-guard-update.path" \
            "$REPO/deploy/update/pi-guard-update.service" /etc/systemd/system/
    fi
    if [[ -e /etc/systemd/system/pi-guard-maint.path ]]; then
        install -m 0644 "$REPO/deploy/maint/pi-guard-maint.path" \
            "$REPO/deploy/maint/pi-guard-maint.service" /etc/systemd/system/
    fi

    systemctl daemon-reload

    if [[ -e /usr/local/sbin/pi-guard-exit-apply ]]; then
        # A watch that systemd stopped (the old PathExists loop) must come back.
        systemctl reset-failed pi-guard-exit-apply.path pi-guard-exit-apply.service 2>/dev/null || true
        systemctl enable --now pi-guard-exit-apply.path pi-guard-privacy-nft.path \
            pi-guard-privacy-status.timer 2>/dev/null || true
        systemctl enable --quiet pi-guard-exit-apply.service 2>/dev/null || true
        # New path units and exit rules take effect now, not only on the next peer change.
        systemctl start pi-guard-exit-apply.service 2>/dev/null || true
        systemctl start pi-guard-privacy-nft.service 2>/dev/null || true
    fi
    if [[ -e /usr/local/sbin/pi-guard-wg-apply ]]; then
        systemctl enable --now --quiet pi-guard-wg-status.timer pi-guard-wg-apply.path 2>/dev/null || true
        iface=wg0
        if [[ -f "$ENV_FILE" ]]; then
            iface=$(sed -n 's/^PI_GUARD_WIREGUARD_INTERFACE=//p' "$ENV_FILE" | head -1 | tr -d '"')
            [[ -n "$iface" ]] || iface=wg0
        fi
        /usr/local/sbin/pi-guard-wg-apply "$iface" 2>/dev/null || true
    fi
    if [[ -e /usr/local/sbin/pi-guard-awg-sync ]]; then
        systemctl enable --quiet pi-guard-awg-sync 2>/dev/null || true
        systemctl try-restart pi-guard-awg-sync 2>/dev/null || true
    fi
    if [[ -e /etc/systemd/system/pi-guard-pq.service ]]; then
        systemctl try-restart pi-guard-pq 2>/dev/null || true
    fi
    return 0
}

# `pi-guard-update --refresh-helpers`: only bring installed root parts up to date, for install.sh.
if [[ "${1:-}" == "--refresh-helpers" ]]; then
    refresh_helpers
    exit $?
fi

# 32-bit boards use the distribution's OpenCV, as install.sh chose: no wheel for "vision" there.
EXTRAS="vision,mqtt,web"
case "$(uname -m)" in armv7l|armv6l) EXTRAS="mqtt,web" ;; esac

install_code() {
    "$PREFIX/venv/bin/pip" install --quiet "$REPO[$EXTRAS]" \
        && install -m 0644 "$REPO/deploy/systemd/pi-guard.service" /etc/systemd/system/pi-guard.service \
        && install -m 0755 "$REPO/scripts/update.sh" /usr/local/sbin/pi-guard-update \
        && { [[ ! -e "$REPO/deploy/maint/pi-guard-maint" ]] \
             || install -m 0755 "$REPO/deploy/maint/pi-guard-maint" /usr/local/sbin/pi-guard-maint; } \
        && { [[ ! -e "$REPO/deploy/update/pi-guard-release-check" ]] \
             || install -m 0755 "$REPO/deploy/update/pi-guard-release-check" "$CHECK"; } \
        && refresh_helpers \
        && git_as_owner rev-parse HEAD > "$PREFIX/commit" \
        && systemctl daemon-reload \
        && systemctl restart pi-guard
}

healthy() {
    for _ in $(seq 1 30); do
        runuser -u pi-guard -- env PI_GUARD_ENV_FILE="$ENV_FILE" "$PREFIX/venv/bin/pi-guard" health >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

# Where updates come from, as chosen in the dashboard: "owner/name stable|beta". Written by
# pi-guard, so read like anything from it: no links, one short line, checked word by word.
SOURCE="$DATA/update/source"
CHANNEL=stable
if [[ -f "$SOURCE" && ! -L "$SOURCE" ]]; then
    read -r wanted channel < <(head -c 200 "$SOURCE") || true
    if [[ "${wanted:-}" =~ ^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,38}/[A-Za-z0-9_.-]{1,100}$ ]]; then
        url="https://github.com/${wanted%.git}.git"
        current="$(git_as_owner remote get-url origin 2>/dev/null || true)"
        [[ "${current%.git}.git" == "$url" ]] || git_as_owner remote set-url origin "$url"
    fi
    [[ "${channel:-}" == beta ]] && CHANNEL=beta
fi

report running "checking"
[[ -s "$SIGNERS" && -x "$CHECK" ]] || { report failed "no release key on this device; see docs/updates.md"; exit 1; }
git_as_owner fetch --quiet --force --tags origin || { report failed "download failed"; exit 1; }
old="$(git_as_owner rev-parse HEAD)"
new="$(runuser -u "$OWNER" -- "$CHECK" "$REPO" "$SIGNERS" "$CHANNEL")" \
    || { report failed "no signed release found"; exit 1; }
if [[ "$old" == "$new" ]]; then report done "already up to date"; exit 0; fi
if git_as_owner merge-base --is-ancestor "$new" "$old"; then
    report done "this device is ahead of the repository"; exit 0
fi
git_as_owner merge-base --is-ancestor "$old" "$new" \
    || { report failed "history differs; update by hand"; exit 1; }
git_as_owner merge --quiet --ff-only "$new" || { report failed "local changes in the way"; exit 1; }
report running "installing"
if install_code && healthy; then
    report done "updated to ${new:0:10}"
    exit 0
fi
report running "rolling back"
git_as_owner reset --quiet --keep "$old"
install_code
healthy && report failed "new version did not start; previous version restored" \
        || report failed "new version did not start; rollback needs attention"
exit 1

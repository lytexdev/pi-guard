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

install_code() {
    "$PREFIX/venv/bin/pip" install --quiet "$REPO[vision,mqtt,web]" \
        && install -m 0644 "$REPO/deploy/systemd/pi-guard.service" /etc/systemd/system/pi-guard.service \
        && install -m 0755 "$REPO/scripts/update.sh" /usr/local/sbin/pi-guard-update \
        && { [[ ! -e "$REPO/deploy/maint/pi-guard-maint" ]] \
             || install -m 0755 "$REPO/deploy/maint/pi-guard-maint" /usr/local/sbin/pi-guard-maint; } \
        && { [[ ! -e "$REPO/deploy/update/pi-guard-release-check" ]] \
             || install -m 0755 "$REPO/deploy/update/pi-guard-release-check" "$CHECK"; } \
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

#!/usr/bin/env bash
# Update pi-guard from its Git repository. Installed as /usr/local/sbin/pi-guard-update and run
# as root by pi-guard-update.path when the dashboard asks; `sudo ./scripts/update.sh` works too.
#
# Only fast-forwards: local changes or a different history stop the update. After installing,
# the service must report healthy within a minute, otherwise the previous version comes back.
set -uo pipefail
PREFIX=/opt/pi-guard
CONF=/etc/pi-guard/update.conf
DATA=/var/lib/pi-guard
STATE="$DATA/update"
ENV_FILE=/etc/pi-guard/pi-guard.env

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -f "$CONF" ]] || { echo "$CONF missing; run scripts/install.sh" >&2; exit 1; }
# shellcheck source=/dev/null
. "$CONF"   # REPO, OWNER
mkdir -p "$STATE"
rm -f "$STATE/request"

report() {  # report STATE MESSAGE
    printf '{"state": "%s", "message": "%s", "time": "%s"}\n' "$1" "$2" "$(date -u +%FT%TZ)" > "$STATE/status.json"
    chown -R pi-guard:pi-guard "$STATE"
    echo "$1: $2"
}
git_as_owner() { runuser -u "$OWNER" -- git -C "$REPO" "$@"; }

install_code() {
    "$PREFIX/venv/bin/pip" install --quiet "$REPO[vision,mqtt,web]" \
        && install -m 0644 "$REPO/deploy/systemd/pi-guard.service" /etc/systemd/system/pi-guard.service \
        && install -m 0755 "$REPO/scripts/update.sh" /usr/local/sbin/pi-guard-update \
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

report running "checking"
git_as_owner fetch --quiet origin main || { report failed "download failed"; exit 1; }
old="$(git_as_owner rev-parse HEAD)"
new="$(git_as_owner rev-parse origin/main)"
if [[ "$old" == "$new" ]]; then report done "already up to date"; exit 0; fi
if git_as_owner merge-base --is-ancestor "$new" "$old"; then
    report done "this device is ahead of the repository"; exit 0
fi
git_as_owner merge-base --is-ancestor "$old" "$new" \
    || { report failed "history differs; update by hand"; exit 1; }
git_as_owner merge --quiet --ff-only origin/main || { report failed "local changes in the way"; exit 1; }
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

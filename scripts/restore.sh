#!/usr/bin/env bash
# Take over a moving file or backup (.pgbk) on a Pi where pi-guard is installed.
#
#   sudo ./scripts/restore.sh pi-guard-backup-20261111T120000Z.pgbk
#
# Asks for the backup passphrase, stops pi-guard, keeps the current configuration and data
# next to them (*.before-restore-DATE), puts the backup's in their place and starts pi-guard.
# Run ./scripts/install.sh first on a new Pi. Root never writes into a folder the pi-guard
# user owns: the data folder is built new, owned by root, and handed over at the end.
set -euo pipefail
PREFIX=/opt/pi-guard
CONF=/etc/pi-guard
DATA=/var/lib/pi-guard
USER_NAME=pi-guard
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
say() { printf '\033[1m==>\033[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || die "run with sudo"
[[ $# -eq 1 && -f "$1" ]] || die "usage: sudo $0 FILE.pgbk"
[[ -x "$PREFIX/venv/bin/pi-guard" ]] || die "pi-guard is not installed here; run ./scripts/install.sh first"
id "$USER_NAME" >/dev/null 2>&1 || die "user $USER_NAME missing; run ./scripts/install.sh first"

WORK="$(mktemp -d /root/pg-restore.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
say "decrypting (asks for the backup passphrase)"
"$PREFIX/venv/bin/pi-guard" backup restore "$1" --into "$WORK" </dev/tty
SRC="$WORK/pi-guard-backup"
[[ -d "$SRC/data" ]] || die "this is not a pi-guard backup"

STAMP="$(date +%Y%m%d-%H%M%S)"
say "stopping pi-guard"
systemctl stop pi-guard

if [[ -d "$SRC/config" ]]; then
    say "configuration -> $CONF (the old one stays in $CONF.before-restore-$STAMP)"
    [[ -d "$CONF" ]] && cp -a "$CONF" "$CONF.before-restore-$STAMP"
    install -d -m 0750 -o root -g "$USER_NAME" "$CONF"
    for file in "$SRC/config"/*; do
        [[ -f "$file" && ! -L "$file" ]] || continue
        # update.conf belongs to this machine (its repository and owner) and root reads it.
        [[ "$(basename "$file")" == update.conf ]] && continue
        install -m 0640 -o root -g "$USER_NAME" "$file" "$CONF/$(basename "$file")"
    done
fi

say "data -> $DATA (the old data stay in $DATA.before-restore-$STAMP)"
NEW="$(mktemp -d "$(dirname "$DATA")/pi-guard.new.XXXXXX")"
chmod 0700 "$NEW"
cp -a --no-preserve=ownership "$SRC/data/." "$NEW/"
find "$NEW" -type l -delete  # a backup holds no links; none may lead out
[[ -e "$DATA" ]] && mv "$DATA" "$DATA.before-restore-$STAMP"
mv "$NEW" "$DATA"
chown -R --no-dereference "$USER_NAME:$USER_NAME" "$DATA"
chmod 0700 "$DATA"

# Recordings outside the data folder (a USB disk on the old Pi): the folder must exist here and
# the service may write there, as install.sh arranges for its own choice.
RECORDINGS="$(sed -n 's/^PI_GUARD_RECORDINGS_DIR=//p' "$CONF/pi-guard.env" 2>/dev/null | tail -1 | tr -d '"')"
if [[ -n "$RECORDINGS" && "$RECORDINGS" == /* && "$RECORDINGS" != "$DATA"* ]]; then
    install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$RECORDINGS" \
        || say "warning: $RECORDINGS cannot be made; recording fails until it exists"
    install -d -m 0755 /etc/systemd/system/pi-guard.service.d
    printf '[Service]\nReadWritePaths=%s\n' "$RECORDINGS" > /etc/systemd/system/pi-guard.service.d/recordings.conf
    systemctl daemon-reload
fi

say "starting pi-guard"
systemctl start pi-guard
sleep 3
systemctl is-active --quiet pi-guard && say "done. Open the dashboard and go through Settings → Data & backup → Moving." \
    || die "pi-guard did not start: journalctl -u pi-guard -n 50"

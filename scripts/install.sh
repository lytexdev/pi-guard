#!/usr/bin/env bash
# Install or reinstall pi-guard as a systemd service.
#
#   sudo ./scripts/install.sh               interactive
#   sudo ./scripts/install.sh --yes         keep existing configuration, no questions
#
# Safe to run again: existing configuration and recordings are kept.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/pi-guard
CONF=/etc/pi-guard
DATA=/var/lib/pi-guard
USER_NAME=pi-guard
ASSUME_YES=0
[[ "${1:-}" == "--yes" ]] && ASSUME_YES=1

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
ask() {  # ask "question" default -> REPLY
    local prompt="$1" default="${2:-}"
    if (( ASSUME_YES )); then REPLY="$default"; return; fi
    read -r -p "$prompt${default:+ [$default]}: " REPLY
    REPLY="${REPLY:-$default}"
}
ask_secret() {
    if (( ASSUME_YES )); then REPLY=""; return; fi
    read -r -s -p "$1: " REPLY; echo
}
yes_no() {  # yes_no "question" y|n
    ask "$1 (y/n)" "$2"
    [[ "$REPLY" =~ ^[YyJj] ]]
}

# 1-2: platform -------------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "run with sudo"
[[ -r /etc/os-release ]] || die "unsupported system (no /etc/os-release)"
. /etc/os-release
case "${ID:-} ${ID_LIKE:-}" in
    *debian*|*raspbian*|*ubuntu*) ;;
    *) die "this installer supports Debian, Raspberry Pi OS and Ubuntu (found ${PRETTY_NAME:-unknown})" ;;
esac
ARCH="$(uname -m)"
case "$ARCH" in
    aarch64|x86_64) SYSTEM_OPENCV=0 ;;
    armv7l|armv6l)  SYSTEM_OPENCV=1; warn "32-bit system: using the distribution's OpenCV" ;;
    *) die "unsupported architecture $ARCH" ;;
esac
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
say "$PRETTY_NAME on $ARCH, ${MEM_MB} MB RAM"
(( MEM_MB < 900 )) && warn "less than 1 GB RAM: use one camera at 720p or lower"

# 3: packages ----------------------------------------------------------------------
PACKAGES=(python3 python3-venv python3-pip ffmpeg v4l-utils alsa-utils iproute2)
(( SYSTEM_OPENCV )) && PACKAGES+=(python3-opencv python3-numpy)
MISSING=()
for pkg in "${PACKAGES[@]}"; do
    dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed" || MISSING+=("$pkg")
done
if (( ${#MISSING[@]} )); then
    say "installing packages: ${MISSING[*]}"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || die "Python 3.11 or newer is required (Raspberry Pi OS Bookworm or later)"

# 4-5: hardware ----------------------------------------------------------------------
say "cameras:"
if compgen -G "/dev/v4l/by-id/*" >/dev/null; then
    ls -1 /dev/v4l/by-id/ | sed 's/^/    /'
else
    warn "no camera found under /dev/v4l/by-id; plug it in and run 'pi-guard cameras' later"
fi
say "sound cards:"
sed -n 's/^ *\([0-9]*\) \[\([^ ]*\) *\]: \(.*\)/    \2: \3/p' /proc/asound/cards 2>/dev/null || true

# 6: user, application, directories ------------------------------------------------
if ! id "$USER_NAME" >/dev/null 2>&1; then
    say "creating system user $USER_NAME"
    useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$USER_NAME"
fi
usermod -a -G video,audio "$USER_NAME"

say "installing pi-guard into $PREFIX"
install -d -m 0755 "$PREFIX"
VENV_ARGS=()
(( SYSTEM_OPENCV )) && VENV_ARGS+=(--system-site-packages)
[[ -x "$PREFIX/venv/bin/python" ]] || python3 -m venv "${VENV_ARGS[@]}" "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
EXTRAS="vision,mqtt"
(( SYSTEM_OPENCV )) && EXTRAS="mqtt"
"$PREFIX/venv/bin/pip" install --quiet "$REPO[$EXTRAS]"
ln -sf "$PREFIX/venv/bin/pi-guard" /usr/local/bin/pi-guard

install -d -m 0750 -o root -g "$USER_NAME" "$CONF"
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$DATA"

# 7-12: configuration ----------------------------------------------------------------
ENV_FILE="$CONF/pi-guard.env"
if [[ -f "$ENV_FILE" ]]; then
    say "keeping existing $ENV_FILE (edit it and run: sudo systemctl restart pi-guard)"
else
    (( ASSUME_YES )) && die "$ENV_FILE does not exist; run without --yes once"
    say "basic settings"
    ask "Language for messages (de/en)" "de"; LANGUAGE="$REPLY"
    TZ_DEFAULT="$(timedatectl show -p Timezone --value 2>/dev/null || echo Europe/Berlin)"
    ask "Time zone" "$TZ_DEFAULT"; TIMEZONE="$REPLY"

    say "Telegram (create a bot with @BotFather, find your user ID with @userinfobot)"
    ask_secret "Bot token"; TOKEN="$REPLY"
    ask "Your Telegram user ID (admin, several separated by commas)" ""; ADMINS="$REPLY"

    SIGNAL_ENABLED=false SIGNAL_NUMBER="" SIGNAL_ADMINS=""
    if yes_no "Set up Signal as well? (needs signal-cli, see docs/signal.md)" n; then
        SIGNAL_ENABLED=true
        ask "Phone number of the pi-guard Signal account (+49...)" ""; SIGNAL_NUMBER="$REPLY"
        ask "Your own Signal number (+49...)" ""; SIGNAL_ADMINS="$REPLY"
    fi

    PRESENCE=false
    yes_no "Detect automatically whether you are at home via your phone's Wi-Fi?" n && PRESENCE=true

    ask "Where should recordings be stored?" "$DATA/recordings"; RECORDINGS="$REPLY"
    ask "Keep recordings for how many days?" "7"; RETENTION="$REPLY"

    umask 027
    cat > "$ENV_FILE" <<ENV
# Written by install.sh on $(date -I). See .env.example for all options.
PI_GUARD_LANGUAGE=$LANGUAGE
PI_GUARD_TIMEZONE=$TIMEZONE
PI_GUARD_CONFIG_FILE=$CONF/config.yaml
PI_GUARD_DATA_DIR=$DATA
PI_GUARD_RECORDINGS_DIR=$RECORDINGS
PI_GUARD_RECORDING_RETENTION_DAYS=$RETENTION
PI_GUARD_TELEGRAM_ENABLED=true
PI_GUARD_TELEGRAM_BOT_TOKEN=$TOKEN
PI_GUARD_TELEGRAM_ADMIN_USER_IDS=$ADMINS
PI_GUARD_SIGNAL_ENABLED=$SIGNAL_ENABLED
PI_GUARD_SIGNAL_PHONE_NUMBER=$SIGNAL_NUMBER
PI_GUARD_SIGNAL_ADMIN_NUMBERS=$SIGNAL_ADMINS
PI_GUARD_PRESENCE_ENABLED=$PRESENCE
PI_GUARD_MOTION_ENABLED=true
PI_GUARD_DEFAULT_MODE=HOME
ENV
    umask 022
fi
chown root:"$USER_NAME" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

if [[ ! -f "$CONF/config.yaml" ]]; then
    say "writing $CONF/config.yaml for the detected hardware"
    "$PREFIX/venv/bin/pi-guard" config init -o "$CONF/config.yaml"
    chown root:"$USER_NAME" "$CONF/config.yaml"
    chmod 0640 "$CONF/config.yaml"
    if grep -q '^PI_GUARD_PRESENCE_ENABLED=true' "$ENV_FILE"; then
        warn "add your phone's Wi-Fi MAC address under presence.devices in $CONF/config.yaml"
    fi
fi

RECORDINGS="$(sed -n 's/^PI_GUARD_RECORDINGS_DIR=//p' "$ENV_FILE" | tail -1)"
RECORDINGS="${RECORDINGS:-$DATA/recordings}"
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$RECORDINGS"

# 13: systemd ------------------------------------------------------------------------
say "installing systemd service"
install -m 0644 "$REPO/deploy/systemd/pi-guard.service" /etc/systemd/system/pi-guard.service
DROPIN=/etc/systemd/system/pi-guard.service.d
if [[ "$RECORDINGS" != "$DATA"* ]]; then
    install -d -m 0755 "$DROPIN"
    printf '[Service]\nReadWritePaths=%s\n' "$RECORDINGS" > "$DROPIN/recordings.conf"
fi
systemctl daemon-reload
"$PREFIX/venv/bin/pi-guard" --env-file "$ENV_FILE" config check \
    || die "configuration is invalid; fix $ENV_FILE and run the installer again"
systemctl enable --quiet pi-guard
systemctl restart pi-guard

# 14: diagnostics --------------------------------------------------------------------
say "diagnostics"
runuser -u "$USER_NAME" -- env PI_GUARD_ENV_FILE="$ENV_FILE" "$PREFIX/venv/bin/pi-guard" doctor || true

# 15: verify startup ---------------------------------------------------------------
say "waiting for the service to start"
for _ in $(seq 1 30); do
    if runuser -u "$USER_NAME" -- env PI_GUARD_ENV_FILE="$ENV_FILE" \
        "$PREFIX/venv/bin/pi-guard" health >/dev/null 2>&1; then
        say "pi-guard is running. Open your bot in Telegram and press Start."
        exit 0
    fi
    sleep 2
done
warn "the service did not report healthy within 60 s; check: sudo journalctl -u pi-guard -n 50"
exit 1

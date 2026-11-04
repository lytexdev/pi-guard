#!/usr/bin/env bash
# Install or reinstall pi-guard as a systemd service.
#
#   sudo ./scripts/install.sh               interactive
#   sudo ./scripts/install.sh --yes         keep existing configuration, no questions
#
# Safe to run again: existing configuration and recordings are kept.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Run from / so commands executed as other users (pi-guard, pihole) never trip over
# a home directory they cannot read, and no stray ./.env is picked up.
cd /
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
PACKAGES=(python3 python3-venv python3-pip ffmpeg v4l-utils alsa-utils iproute2 iputils-ping openssh-client)
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
EXTRAS="vision,mqtt,web"
(( SYSTEM_OPENCV )) && EXTRAS="mqtt,web"
"$PREFIX/venv/bin/pip" install --quiet "$REPO[$EXTRAS]"
ln -sf "$PREFIX/venv/bin/pi-guard" /usr/local/bin/pi-guard

install -d -m 0750 -o root -g "$USER_NAME" "$CONF"
install -d -m 0700 -o "$USER_NAME" -g "$USER_NAME" "$DATA"

# 7-12: configuration ----------------------------------------------------------------
ENV_FILE="$CONF/pi-guard.env"
set_env() {  # set_env KEY VALUE: replace or append a line; VALUE is taken literally
    local tmp
    tmp="$(mktemp)"
    grep -v "^$1=" "$ENV_FILE" > "$tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$tmp"
    cat "$tmp" > "$ENV_FILE"  # keeps owner and mode of the env file
    rm -f "$tmp"
}

TOKEN_RE='^[0-9]{5,15}:[A-Za-z0-9_-]{30,}$'
PHONE_RE='^\+[1-9][0-9]{6,14}$'

if [[ -f "$ENV_FILE" ]]; then
    say "keeping existing $ENV_FILE (edit it and run: sudo systemctl restart pi-guard)"
elif [[ -f "$REPO/.env" ]] && yes_no "Use $REPO/.env as configuration?" y; then
    install -m 0640 -o root -g "$USER_NAME" "$REPO/.env" "$ENV_FILE"
    # Paths in a development .env do not fit the installed service.
    set_env PI_GUARD_CONFIG_FILE "$CONF/config.yaml"
    set_env PI_GUARD_DATA_DIR "$DATA"
    sed -i '/^PI_GUARD_RUNTIME_DIR=/d; /^PI_GUARD_RECORDINGS_DIR=[^/]/d; /^PI_GUARD_ENV_FILE=/d' "$ENV_FILE"
    say "copied to $ENV_FILE"
else
    (( ASSUME_YES )) && die "$ENV_FILE does not exist; run without --yes once"
    say "basic settings"
    ask "Language for messages (de/en)" "de"; LANGUAGE="$REPLY"
    TZ_DEFAULT="$(timedatectl show -p Timezone --value 2>/dev/null || echo Europe/Berlin)"
    ask "Time zone" "$TZ_DEFAULT"; TIMEZONE="$REPLY"

    say "Telegram (create a bot with @BotFather, find your user ID with @userinfobot)"
    while true; do
        ask_secret "Bot token (empty: set up later in the dashboard)"; TOKEN="$REPLY"
        [[ -z "$TOKEN" || "$TOKEN" =~ $TOKEN_RE ]] && break
        warn "that does not look like a bot token (123456789:AA...); paste the whole token"
    done
    ADMINS=""
    if [[ -n "$TOKEN" ]]; then
        ask "Your Telegram user ID (admin, several separated by commas)" ""; ADMINS="$REPLY"
    fi

    SIGNAL_ENABLED=false SIGNAL_NUMBER="" SIGNAL_ADMINS=""
    if yes_no "Set up Signal as well? (needs signal-cli, see docs/signal.md)" n; then
        SIGNAL_ENABLED=true
        echo "pi-guard needs its own Signal account (a second number). Messages from the"
        echo "account to itself cannot be used as commands."
        while true; do
            ask "Phone number of the pi-guard Signal account (+49...)" ""; SIGNAL_NUMBER="${REPLY// /}"
            ask "Your own Signal number (+49...)" ""; SIGNAL_ADMINS="${REPLY// /}"
            if [[ ! "$SIGNAL_NUMBER" =~ $PHONE_RE || ! "$SIGNAL_ADMINS" =~ $PHONE_RE ]]; then
                warn "use the international format, e.g. +4915112345678"
            elif [[ "$SIGNAL_NUMBER" == "$SIGNAL_ADMINS" ]]; then
                warn "these must be two different numbers"
            else
                break
            fi
        done
        command -v signal-cli >/dev/null \
            || warn "signal-cli is not installed yet; Signal stays offline until it runs (docs/signal.md)"
    fi

    PRESENCE=false
    yes_no "Detect automatically whether you are at home via your phone's Wi-Fi?" n && PRESENCE=true

    MIC=false
    echo "Microphone: lets admins listen in live and record sound clips. Read docs/privacy.md;"
    echo "everyone in the home should know about it."
    yes_no "Enable the microphone?" n && MIC=true

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
PI_GUARD_AUDIO_PLAYBACK_ENABLED=true
PI_GUARD_AUDIO_RECORDING_ENABLED=$MIC
PI_GUARD_MOTION_ENABLED=true
PI_GUARD_DEFAULT_MODE=HOME
ENV
    umask 022
fi
chown root:"$USER_NAME" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

if [[ -f "$CONF/config.yaml" ]] && ! "$PREFIX/venv/bin/python" -c '
import sys
from pathlib import Path
from pi_guard.config.files import load_file_config
load_file_config(Path(sys.argv[1]))' "$CONF/config.yaml" 2>/dev/null; then
    BROKEN="$CONF/config.yaml.broken-$(date +%Y%m%d%H%M%S)"
    warn "$CONF/config.yaml is invalid; kept as $BROKEN and writing a new one"
    mv "$CONF/config.yaml" "$BROKEN"
fi
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

# optional: dashboard, Pi-hole, WireGuard status, fixed address ----------------------

if ! grep -q '^PI_GUARD_DASHBOARD_ENABLED=true' "$ENV_FILE" \
    && yes_no "Enable the web dashboard (https://<pi>:8443)?" n; then
    set_env PI_GUARD_DASHBOARD_ENABLED true
    grep -q '^PI_GUARD_DASHBOARD_SECRET_KEY=..' "$ENV_FILE" \
        || set_env PI_GUARD_DASHBOARD_SECRET_KEY "$(openssl rand -hex 32)"
    say "first login: admin / admin123 - the dashboard then asks for your own password"
    say "dashboard enabled; install the certificate from https://<pi>:8443/ca.crt on your devices"
fi

if ! command -v pihole >/dev/null && yes_no "Install Pi-hole (network-wide ad blocking, official installer from pi-hole.net)?" n; then
    echo "This downloads and runs https://install.pi-hole.net as root. It asks its own questions."
    if yes_no "Run the official Pi-hole installer now?" n; then
        # Downloaded first and run with the terminal attached, so its setup dialogs appear
        # (piped into bash it silently uses defaults).
        PIHOLE_SCRIPT="$(mktemp)"
        curl -fsSL https://install.pi-hole.net -o "$PIHOLE_SCRIPT"
        bash "$PIHOLE_SCRIPT" </dev/tty || warn "the Pi-hole installer did not finish"
        rm -f "$PIHOLE_SCRIPT"
    fi
fi
if command -v pihole >/dev/null && ! grep -q '^PI_GUARD_PIHOLE_ENABLED=true' "$ENV_FILE" \
    && yes_no "Connect pi-guard to the local Pi-hole?" y; then
    echo "Create an app password in Pi-hole: Settings > Web interface / API > Configure app password."
    ask_secret "Pi-hole app password"
    set_env PI_GUARD_PIHOLE_ENABLED true
    set_env PI_GUARD_PIHOLE_URL "http://127.0.0.1"
    set_env PI_GUARD_PIHOLE_PASSWORD "$REPLY"
fi

if grep -q '^PI_GUARD_DASHBOARD_ENABLED=true' "$ENV_FILE" \
    && ! grep -q '^PI_GUARD_WIREGUARD_ENABLED=true' "$ENV_FILE" \
    && yes_no "Set up a WireGuard VPN (reach home from anywhere; devices are managed in the dashboard)?" n; then
    ask "Address of your home from the internet (MyFRITZ!/DynDNS name, empty: current public IP)" ""
    if [[ -n "$REPLY" ]]; then
        "$REPO/scripts/wireguard.sh" setup --endpoint "$REPLY" || warn "WireGuard not set up; see docs/network.md"
    else
        "$REPO/scripts/wireguard.sh" setup || warn "WireGuard not set up; see docs/network.md"
    fi
fi

if ! command -v mosquitto >/dev/null \
    && yes_no "Install an MQTT broker for smart home devices (Mosquitto, with a password)?" n; then
    "$REPO/scripts/mqtt.sh" setup || warn "MQTT not set up; see docs/addons.md"
fi

if command -v nmcli >/dev/null && [[ "$(nmcli -g ipv4.method connection show "$(nmcli -t -f NAME connection show --active | head -1)" 2>/dev/null)" == "auto" ]] \
    && yes_no "Give this device a fixed IP address (recommended with Pi-hole)?" n; then
    "$REPO/scripts/network.sh" static || warn "fixed address not set; see docs/network.md"
fi

# Telegram without a token only works when it can be set up in the dashboard.
if ! grep -qE '^PI_GUARD_TELEGRAM_BOT_TOKEN=.+' "$ENV_FILE" \
    && ! grep -q '^PI_GUARD_DASHBOARD_ENABLED=true' "$ENV_FILE"; then
    warn "no Telegram bot token: Telegram is switched off (set the token in $ENV_FILE later)"
    set_env PI_GUARD_TELEGRAM_ENABLED false
fi

# 13: systemd ------------------------------------------------------------------------
say "installing systemd service"
OWNER="$(stat -c %U "$REPO")"
printf 'REPO=%q\nOWNER=%q\n' "$REPO" "$OWNER" > "$CONF/update.conf"
runuser -u "$OWNER" -- git -C "$REPO" rev-parse HEAD > "$PREFIX/commit" 2>/dev/null || true
install -m 0755 "$REPO/scripts/update.sh" /usr/local/sbin/pi-guard-update
install -m 0644 "$REPO/deploy/update/pi-guard-update.path" "$REPO/deploy/update/pi-guard-update.service" /etc/systemd/system/
install -m 0755 "$REPO/deploy/maint/pi-guard-maint" /usr/local/sbin/pi-guard-maint
install -m 0644 "$REPO/deploy/maint/pi-guard-maint.path" "$REPO/deploy/maint/pi-guard-maint.service" /etc/systemd/system/
install -d -o pi-guard -g pi-guard -m 0700 "$DATA/maint"
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
systemctl enable --now --quiet pi-guard-update.path pi-guard-maint.path
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

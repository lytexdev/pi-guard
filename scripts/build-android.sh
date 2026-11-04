#!/usr/bin/env bash
# Builds and signs the PiGuard app. Runs on a computer with the Android SDK, not on the Pi.
#
#   ./scripts/build-android.sh 1.0.0            # build signed APK
#   ./scripts/build-android.sh 1.0.0 --publish system   # also put it on that ssh host
#
# The first run creates the signing key in ~/.config/pi-guard/android. Back it up: without it,
# phones cannot be updated any more. It must never enter the repository.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS="${PIGUARD_SIGNING_DIR:-$HOME/.config/pi-guard/android}"
STORE="$SECRETS/release.jks"
PROPERTIES="$SECRETS/keystore.properties"
VERSION="${1:?usage: $0 VERSION [--publish SSH_HOST]}"
CODE="$(git -C "$REPO" rev-list --count HEAD)"
[[ "$VERSION" =~ ^[0-9A-Za-z.+-]+$ ]] || { echo "not a version: $VERSION" >&2; exit 1; }

if [[ ! -f "$PROPERTIES" ]]; then
    install -d -m 0700 "$SECRETS"
    password="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
    keytool -genkeypair -keystore "$STORE" -storepass "$password" -keypass "$password" \
        -alias piguard -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=PiGuard" >/dev/null 2>&1
    (umask 077; printf 'storeFile=%s\nstorePassword=%s\nkeyAlias=piguard\nkeyPassword=%s\n' \
        "$STORE" "$password" "$password" > "$PROPERTIES")
    chmod 600 "$STORE"
    echo "created the signing key in $SECRETS; back it up"
fi

cd "$REPO/android"
PIGUARD_KEYSTORE_PROPERTIES="$PROPERTIES" PIGUARD_VERSION_CODE="$CODE" PIGUARD_VERSION_NAME="$VERSION" \
    ./gradlew --quiet assembleRelease
APK="$REPO/android/app/build/outputs/apk/release/app-release.apk"
"${ANDROID_HOME:-$HOME/Android/Sdk}"/build-tools/*/apksigner verify "$APK" 2>/dev/null || apksigner verify "$APK"
echo "built $APK ($VERSION, code $CODE)"

if [[ "${2:-}" == "--publish" ]]; then
    host="${3:?--publish needs an ssh host}"
    sum="$(sha256sum "$APK" | cut -d' ' -f1)"
    size="$(stat -c %s "$APK")"
    scp -q "$APK" "$host:/tmp/PiGuard.apk"
    printf '{"version_code": %s, "version_name": "%s", "sha256": "%s", "size": %s}\n' \
        "$CODE" "$VERSION" "$sum" "$size" | ssh "$host" 'cat > /tmp/PiGuard.json'
    ssh "$host" 'sudo install -d -o pi-guard -g pi-guard -m 0700 /var/lib/pi-guard/apps /var/lib/pi-guard/apps/android &&
        sudo install -o pi-guard -g pi-guard -m 0600 /tmp/PiGuard.apk /var/lib/pi-guard/apps/android/latest.apk &&
        sudo install -o pi-guard -g pi-guard -m 0600 /tmp/PiGuard.json /var/lib/pi-guard/apps/android/latest.json &&
        rm /tmp/PiGuard.apk /tmp/PiGuard.json'
    echo "published to $host"
fi

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

# Before anything else, so a run on the wrong machine (the Pi) leaves no signing key behind.
SDK="${ANDROID_HOME:-$HOME/Android/Sdk}"
NDK="$(ls -d "$SDK"/ndk/* 2>/dev/null | sort -V | tail -1 || true)"
[[ -n "$NDK" ]] || { echo "no Android NDK in $SDK/ndk: run this on the computer with the Android SDK, not on the Pi" >&2; exit 1; }

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

# The post-quantum key exchange, the same Rust code as on the Pi and on computers.
TOOLS="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
API=29
for entry in arm64-v8a:aarch64-linux-android:aarch64-linux-android \
             armeabi-v7a:armv7-linux-androideabi:armv7a-linux-androideabi \
             x86_64:x86_64-linux-android:x86_64-linux-android; do
    IFS=: read -r abi target compiler <<< "$entry"
    name="${target//-/_}"
    upper="${name^^}"
    env "CC_$name=$TOOLS/$compiler$API-clang" "AR_$name=$TOOLS/llvm-ar" \
        "CARGO_TARGET_${upper}_LINKER=$TOOLS/$compiler$API-clang" \
        "CARGO_TARGET_${upper}_RUSTFLAGS=-C link-arg=-Wl,-z,max-page-size=16384" \
        cargo build --quiet --release --manifest-path "$REPO/vpn/Cargo.toml" -p pi-guard-kem --target "$target"
    install -D -m 0644 "$REPO/vpn/target/$target/release/libpi_guard_kem.so" \
        "$REPO/android/app/build/rust-jni/$abi/libpi_guard_kem.so"
done

# The tunnel: AmneziaWG's Android library, which speaks plain WireGuard as well as its disguised
# form. Not on Maven, so it is built here once from a fixed release; the commit is checked, and
# Go modules are checked by their go.sum. The result is kept in the cache, never in the repository.
AWG_TAG=v3.1.20260814
AWG_COMMIT=5c16489e2cd9ed3a0a7a27c7445bba5238132f86
AWG_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/pi-guard/amneziawg-android-$AWG_TAG.aar"
if [[ ! -f "$AWG_CACHE" ]]; then
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' EXIT
    echo "building the AmneziaWG tunnel library $AWG_TAG (once; needs a few minutes and the internet)"
    git clone --quiet --depth 1 --branch "$AWG_TAG" --recurse-submodules --shallow-submodules \
        https://github.com/amnezia-vpn/amneziawg-android.git "$work/awg" 2>/dev/null
    [[ "$(git -C "$work/awg" rev-parse HEAD)" == "$AWG_COMMIT" ]] || { echo "amneziawg-android $AWG_TAG is not commit $AWG_COMMIT; stopping" >&2; exit 1; }
    # Their build names an NDK; take the one installed here instead of downloading another.
    sed -i "s/ndkVersion = \"[^\"]*\"/ndkVersion = \"$(basename "$NDK")\"/" "$work/awg/settings.gradle.kts"
    (cd "$work/awg" && ./gradlew --quiet :tunnel:assembleRelease)
    install -D -m 0644 "$work/awg/tunnel/build/outputs/aar/tunnel-release.aar" "$AWG_CACHE"
fi
install -D -m 0644 "$AWG_CACHE" "$REPO/android/app/build/awg/amneziawg-tunnel.aar"

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

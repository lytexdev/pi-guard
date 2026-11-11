#!/usr/bin/env bash
# Builds, signs and publishes the agent, the desktop app and the phone app as one GitHub release.
#
#   ./scripts/release-apps.sh 1.4.0                     # everything this computer can build
#   ./scripts/release-apps.sh 1.4.0 android desktop     # only these
#   ./scripts/release-apps.sh 1.4.0 --no-upload         # build and sign, keep it in dist/
#
# Every pi-guard looks for the newest apps-* release every 6 hours, takes only files whose
# signature checks out, and hands them to its devices, which check again. The keys stay on this
# computer: that is why this runs here and not on GitHub. With the signing key on GitHub, a
# broken-into account could sign updates for every device.
#
# Needs: the update key (~/.config/pi-guard/update-ed25519.pem), the app's keystore for Android
# (see docs/mobile.md), the GitHub CLI logged in (`gh auth login`), and the build tools of each part.
set -euo pipefail

VERSION="${1:?usage: $0 VERSION [android] [desktop] [agent] [proxy] [--no-upload]}"
shift
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like 1.2.3" >&2; exit 1; }
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPLOAD=true
PARTS=()
for arg in "$@"; do
    case "$arg" in
        --no-upload) UPLOAD=false ;;
        android|desktop|agent|proxy) PARTS+=("$arg") ;;
        *) echo "unknown: $arg" >&2; exit 1 ;;
    esac
done
[[ ${#PARTS[@]} -gt 0 ]] || PARTS=(android desktop agent proxy)
has() { [[ " ${PARTS[*]} " == *" $1 "* ]]; }

KEY="${PI_GUARD_UPDATE_KEY:-$HOME/.config/pi-guard/update-ed25519.pem}"
[[ -f "$KEY" ]] || { echo "no update key at $KEY: see docs/updates.md" >&2; exit 1; }
if $UPLOAD; then
    command -v gh >/dev/null || { echo "the GitHub CLI is missing: https://cli.github.com" >&2; exit 1; }
    [[ -z "$(git -C "$REPO" status --porcelain)" ]] || { echo "commit or stash your changes first" >&2; exit 1; }
    gh release view "apps-$VERSION" >/dev/null 2>&1 && { echo "apps-$VERSION exists already" >&2; exit 1; }
fi

OUT="$REPO/dist/apps-$VERSION"
rm -rf "$OUT"
mkdir -p "$OUT"
sign() { "$REPO/scripts/sign-update.sh" "$@" "$OUT"; }

if has android; then
    "$REPO/scripts/build-android.sh" "$VERSION"
    code="$(git -C "$REPO" rev-list --count HEAD)"  # as build-android.sh counts it
    cp "$REPO/android/app/build/outputs/apk/release/app-release.apk" "$OUT/PiGuard-$VERSION.apk"
    sign android apk "$VERSION+$code" "$OUT/PiGuard-$VERSION.apk"
fi

if has desktop; then
    targets=(linux)
    command -v wine >/dev/null && targets+=(win)
    "$REPO/scripts/build-desktop.sh" "$VERSION" "${targets[@]}"
    for file in "$REPO"/desktop/dist/PiGuard-"$VERSION"-*; do
        [[ -f "$file" ]] && cp "$file" "$OUT/"
    done
    # The files the desktop app updates itself from; the others are for people to download.
    [[ -f "$OUT/PiGuard-$VERSION-linux-x64.tar.gz" ]] && sign desktop linux-x64 "$VERSION" "$OUT/PiGuard-$VERSION-linux-x64.tar.gz"
    [[ -f "$OUT/PiGuard-$VERSION-win-x64.exe" ]] && sign desktop win-x64 "$VERSION" "$OUT/PiGuard-$VERSION-win-x64.exe"
fi

if has agent; then
    arch="$(uname -m)"
    cargo build --quiet --release --manifest-path "$REPO/agent/Cargo.toml"
    cp "$REPO/agent/target/release/pi-guard-agent" "$OUT/pi-guard-agent-linux-$arch"
    sign agent "linux-$arch" "$VERSION" "$OUT/pi-guard-agent-linux-$arch"
fi

if has proxy; then
    # pgproxy is installed by hand (scripts/install-proxy.sh), checked against SHA256SUMS.
    cargo build --quiet --release --manifest-path "$REPO/proxy/Cargo.toml"
    cp "$REPO/proxy/target/release/pgproxy" "$OUT/pgproxy-linux-$(uname -m)"
    if command -v aarch64-linux-gnu-gcc >/dev/null && rustup target list --installed 2>/dev/null | grep -q aarch64-unknown-linux-gnu; then
        CC_aarch64_unknown_linux_gnu=aarch64-linux-gnu-gcc \
        CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
            cargo build --quiet --release --target aarch64-unknown-linux-gnu --manifest-path "$REPO/proxy/Cargo.toml"
        cp "$REPO/proxy/target/aarch64-unknown-linux-gnu/release/pgproxy" "$OUT/pgproxy-linux-aarch64"
    else
        echo "no aarch64 cross compiler: the Pi build of pgproxy is left out (see docs/vpn.md)" >&2
    fi
fi

(cd "$OUT" && sha256sum -- * > SHA256SUMS)
echo "release files in $OUT:"
ls -1 "$OUT"

if $UPLOAD; then
    git -C "$REPO" tag -a "apps-$VERSION" -m "Apps $VERSION"
    git -C "$REPO" push origin "apps-$VERSION"
    gh release create "apps-$VERSION" "$OUT"/* --title "Apps $VERSION" --notes \
"Phone app, desktop app, agent and pgproxy $VERSION; the apps and the agent are signed. Every pi-guard fetches these by itself and hands them to its
devices; each file is checked against the update key first. To download by hand, check the file against SHA256SUMS."
    echo "published apps-$VERSION"
fi

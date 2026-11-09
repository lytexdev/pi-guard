#!/usr/bin/env bash
# Signs one release file and writes its manifest, PRODUCT-PLATFORM.json, into OUTDIR.
#
#   ./scripts/sign-update.sh agent linux-x86_64 1.4.0 agent/target/release/pi-guard-agent out/
#   ./scripts/sign-update.sh android apk 1.4.0+812 PiGuard-1.4.0.apk out/
#
# The manifest is what a Pi takes from a GitHub release (src/pi_guard/core/app_updates.py) and
# what agents and desktop apps check (agent/src/update.rs, desktop/updates.js). The signature
# covers product, platform, version and checksum, so no signed file can pass as another.
# The phone app's version carries its Android version code after "+", so that is signed too.
set -euo pipefail

PRODUCT="${1:?usage: $0 PRODUCT PLATFORM VERSION FILE OUTDIR}"
PLATFORM="${2:?platform}" VERSION="${3:?version}" FILE="${4:?file}" OUT="${5:?outdir}"
KEY="${PI_GUARD_UPDATE_KEY:-$HOME/.config/pi-guard/update-ed25519.pem}"
[[ -f "$KEY" ]] || { echo "no signing key at $KEY" >&2; exit 1; }
[[ "$PRODUCT" =~ ^(agent|desktop|android)$ && "$PLATFORM" =~ ^[a-z0-9_-]{1,32}$ ]] || { echo "bad product or platform" >&2; exit 1; }
if [[ "$PRODUCT" == android ]]; then
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+\+[0-9]{1,9}$ ]] || { echo "the phone app needs 1.2.3+CODE" >&2; exit 1; }
else
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like 1.2.3" >&2; exit 1; }
fi
name="$(basename "$FILE")"
[[ "$name" =~ ^[A-Za-z0-9._-]{1,120}$ ]] || { echo "file name may only hold letters, digits, . _ -" >&2; exit 1; }

mkdir -p "$OUT"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
sum="$(sha256sum "$FILE" | cut -d' ' -f1)"
printf 'pi-guard-update|%s|%s|%s|%s' "$PRODUCT" "$PLATFORM" "$VERSION" "$sum" > "$work/message"
openssl pkeyutl -sign -rawin -inkey "$KEY" -in "$work/message" -out "$work/signature"
signature="$(od -An -tx1 -v "$work/signature" | tr -d ' \n')"
printf '{"version": "%s", "sha256": "%s", "size": %s, "signature": "%s", "file": "%s"}\n' \
    "$VERSION" "$sum" "$(stat -c %s "$FILE")" "$signature" "$name" > "$OUT/$PRODUCT-$PLATFORM.json"
echo "signed $PRODUCT $PLATFORM $VERSION ($name)"

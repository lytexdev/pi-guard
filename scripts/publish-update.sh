#!/usr/bin/env bash
# Signs a release and puts it on your pi-guard, which hands it to agents and desktop apps.
#
#   ./scripts/publish-update.sh agent linux-x86_64 0.2.0 agent/target/release/pi-guard-agent system
#   ./scripts/publish-update.sh desktop linux-x64 1.1.0 desktop/dist/PiGuard-1.1.0-linux-x64.tar.gz system
#
# The private key stays on this computer. A pi-guard only passes files on and cannot make its
# own, so someone breaking into it cannot send anything to your other machines. Back this key up:
# without it, devices refuse every new version.
set -euo pipefail

PRODUCT="${1:?usage: $0 PRODUCT PLATFORM VERSION FILE SSH_HOST}"
PLATFORM="${2:?platform}" VERSION="${3:?version}" FILE="${4:?file}" HOST="${5:?ssh host}"
KEY="${PI_GUARD_UPDATE_KEY:-$HOME/.config/pi-guard/update-ed25519.pem}"
[[ -f "$KEY" ]] || { echo "no signing key at $KEY" >&2; exit 1; }
[[ "$PRODUCT" =~ ^(agent|desktop)$ && "$PLATFORM" =~ ^[a-z0-9_-]{1,32}$ ]] || { echo "bad product or platform" >&2; exit 1; }
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like 1.2.3" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
sum="$(sha256sum "$FILE" | cut -d' ' -f1)"
# Everything that must not be swapped: a signed agent is no desktop app, a signed 1.0 no 2.0.
printf 'pi-guard-update|%s|%s|%s|%s' "$PRODUCT" "$PLATFORM" "$VERSION" "$sum" > "$work/message"
openssl pkeyutl -sign -rawin -inkey "$KEY" -in "$work/message" -out "$work/signature"
signature="$(od -An -tx1 -v "$work/signature" | tr -d ' \n')"
printf '{"version": "%s", "sha256": "%s", "size": %s, "signature": "%s"}\n' \
    "$VERSION" "$sum" "$(stat -c %s "$FILE")" "$signature" > "$work/latest.json"

if [[ "$HOST" == local:* ]]; then  # a folder, for tests
    target="${HOST#local:}/apps/$PRODUCT/$PLATFORM"
    mkdir -p "$target"
    cp "$FILE" "$target/latest.bin"
    cp "$work/latest.json" "$target/latest.json"
else
    target="/var/lib/pi-guard/apps/$PRODUCT/$PLATFORM"
    scp -q "$FILE" "$HOST:/tmp/pg-update.bin"
    scp -q "$work/latest.json" "$HOST:/tmp/pg-update.json"
    ssh "$HOST" "sudo install -d -o pi-guard -g pi-guard -m 0700 /var/lib/pi-guard/apps /var/lib/pi-guard/apps/$PRODUCT '$target' &&
        sudo install -o pi-guard -g pi-guard -m 0600 /tmp/pg-update.bin '$target/latest.bin' &&
        sudo install -o pi-guard -g pi-guard -m 0600 /tmp/pg-update.json '$target/latest.json' &&
        rm /tmp/pg-update.bin /tmp/pg-update.json"
fi
echo "published $PRODUCT $PLATFORM $VERSION to $HOST"

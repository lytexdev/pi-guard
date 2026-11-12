#!/usr/bin/env bash
# Signs a version of pi-guard for the Pis. Run it on your own computer, never on a Pi.
#
#   ./scripts/release.sh key    once: makes the signing key and puts its public half in the repo
#   ./scripts/release.sh tag    signs the current main and pushes the release tag
#   ./scripts/release.sh tag beta   the same as a beta: only Pis on the beta channel take it
#
# A Pi installs only what this key signed, so someone who gets into the GitHub account cannot run
# anything on it. Back the key up: without it, Pis accept no new version until a new key is set
# on each of them by hand.
set -euo pipefail

KEY="${PI_GUARD_RELEASE_KEY:-$HOME/.config/pi-guard/release-ed25519}"
cd "$(dirname "$0")/.."
SIGNERS=deploy/update/release-signers
die() { echo "$*" >&2; exit 1; }

make_key() {
    [[ ! -e "$KEY" ]] || die "$KEY exists already"
    install -d -m 0700 "$(dirname "$KEY")"
    echo "Choose a passphrase: it protects the key if this computer is stolen."
    ssh-keygen -q -t ed25519 -C pi-guard-release -f "$KEY"
    printf 'pi-guard-release namespaces="git" %s\n' "$(cut -d' ' -f1,2 "$KEY.pub")" > "$SIGNERS"
    echo "Key: $KEY. Back it up now."
    echo "Next: commit $SIGNERS, then run sudo ./scripts/install.sh on each Pi once."
}

make_tag() {
    [[ -f "$KEY" ]] || die "no key at $KEY: run $0 key"
    grep -qF "$(cut -d' ' -f2 "$KEY.pub")" "$SIGNERS" 2>/dev/null || die "$SIGNERS has another key"
    [[ "$(git branch --show-current)" == main ]] || die "not on main"
    [[ -z "$(git status --porcelain)" ]] || die "there are uncommitted changes"
    git fetch --quiet origin main
    [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || die "main differs from origin/main"
    local name kind="release"
    [[ "${1:-}" == beta ]] && kind="beta"
    name="$kind-$(date -u +%Y%m%d-%H%M%S)"
    git -c gpg.format=ssh -c user.signingkey="$KEY" tag -s -m "pi-guard $name" "$name"
    local channel=stable
    [[ "$kind" == beta ]] && channel=beta
    [[ "$(deploy/update/pi-guard-release-check . "$SIGNERS" "$channel")" == "$(git rev-parse HEAD)" ]] \
        || { git tag -d "$name" >/dev/null; die "the new tag does not pass the check"; }
    git push --quiet origin "refs/tags/$name"
    echo "signed and pushed $name"
}

case "${1:-}" in
    key) make_key ;;
    tag) make_tag "${2:-}" ;;
    *) die "usage: $0 key|tag [beta]" ;;
esac

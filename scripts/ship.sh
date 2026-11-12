#!/usr/bin/env bash
# One release of everything: the Pi with its dashboard, the phone app, the desktop app, the agent.
# Run it on your own computer, on main, with everything pushed.
#
#   ./scripts/ship.sh               # the version follows from the commits since the last release
#   ./scripts/ship.sh minor         # or say it: major, minor or patch
#   ./scripts/ship.sh --next        # only print the version it would make
#   ./scripts/ship.sh --beta [STEP] # a beta: only Pis and devices on the beta channel take it
#
# Since the last apps-* release, a commit "feat: ..." makes a minor version (1.9.0), "fix!: ..."
# or a "BREAKING CHANGE" line a major one (2.0.0), anything else a patch (1.8.1). Devices install
# a new minor or major version by themselves and only offer a patch; see docs/updates.md.
#
# What it does: writes the version into pyproject.toml and src/pi_guard/__init__.py and commits
# that as "release: X.Y.Z"; builds, signs and publishes the apps (release-apps.sh); pushes main;
# signs the Pi release (release.sh tag, which asks for the key's passphrase). Every pi-guard finds
# both within an hour. If a step fails, fix it and run the script again: it goes on from there.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
die() { echo "error: $*" >&2; exit 1; }
say() { printf '==> %s\n' "$*"; }

last_release() { git tag --list 'apps-*' --sort=-version:refname | sed -n 's/^apps-\([0-9]*\.[0-9]*\.[0-9]*\)$/\1/p' | head -1; }

# major, minor or patch from the commit messages in a range.
step_of() {
    local log
    log="$(git log --format='%s%n%b' "$1")"
    if grep -qE '^[a-z]+(\([^)]*\))?!:|^BREAKING[ -]CHANGE' <<<"$log"; then echo major
    elif grep -qE '^feat(\([^)]*\))?:' <<<"$log"; then echo minor
    else echo patch
    fi
}

bump() { # version step
    local major minor patch
    IFS=. read -r major minor patch <<<"$1"
    case "$2" in
        major) echo "$((major + 1)).0.0" ;;
        minor) echo "$major.$((minor + 1)).0" ;;
        patch) echo "$major.$minor.$((patch + 1))" ;;
    esac
}

next_version() { # [step]
    local last range
    last="$(last_release)"
    [[ -n "$last" ]] || die "no apps-* release yet: run scripts/release-apps.sh VERSION once"
    range="apps-$last..HEAD"
    if [[ "$(git log -1 --format=%s)" == "release: "* ]]; then
        range="apps-$last..HEAD~1"  # a release commit from an earlier, unfinished run
    fi
    [[ -n "$(git log --format=%H "$range")" ]] || die "nothing new since apps-$last"
    bump "$last" "${1:-$(step_of "$range")}"
}

set_version() {
    sed -i "s/^version = \"[0-9.]*\"$/version = \"$1\"/" pyproject.toml
    sed -i "s/^__version__ = \"[0-9.]*\"$/__version__ = \"$1\"/" src/pi_guard/__init__.py
    grep -q "^version = \"$1\"$" pyproject.toml && grep -q "^__version__ = \"$1\"$" src/pi_guard/__init__.py \
        || die "could not write the version"
}

STEP=""
BETA=false
if [[ "${1:-}" == --beta ]]; then BETA=true; shift; fi
case "${1:-}" in
    --next) next_version "${2:-}"; exit ;;
    major|minor|patch) STEP="$1" ;;
    "") ;;
    *) die "usage: $0 [major|minor|patch] | --next" ;;
esac

[[ "$(git branch --show-current)" == main ]] || die "not on main"
[[ -z "$(git status --porcelain)" ]] || die "commit or stash your changes first"
git fetch --quiet origin main
git merge-base --is-ancestor origin/main HEAD || die "origin/main has commits you do not have: git pull"

if [[ "$(git log -1 --format=%s)" =~ ^release:\ ([0-9]+\.[0-9]+\.[0-9]+)$ ]] && ! git rev-parse -q --verify "refs/tags/apps-${BASH_REMATCH[1]}" >/dev/null; then
    VERSION="${BASH_REMATCH[1]}"
    say "going on with $VERSION"
else
    VERSION="$(next_version "$STEP")"
    say "release $VERSION (last was $(last_release))"
    set_version "$VERSION"
    git add pyproject.toml src/pi_guard/__init__.py
    git commit --quiet -m "release: $VERSION"
fi

if ! git rev-parse -q --verify "refs/tags/apps-$VERSION" >/dev/null; then
    say "apps: build, sign, publish apps-$VERSION"
    if $BETA; then "$REPO/scripts/release-apps.sh" "$VERSION" --prerelease; else "$REPO/scripts/release-apps.sh" "$VERSION"; fi
fi
say "pushing main"
git push --quiet origin main
if [[ "$(git tag --points-at HEAD --list 'release-*' 'beta-*')" == "" ]]; then
    say "Pi: signing the release (asks for the passphrase of your release key)"
    if $BETA; then "$REPO/scripts/release.sh" tag beta; else "$REPO/scripts/release.sh" tag; fi
fi
say "done: $VERSION. Every pi-guard takes the apps and itself within an hour."

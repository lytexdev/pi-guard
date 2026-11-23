# Updates

Agents, desktop apps and the phone app update themselves from your pi-guard, and every pi-guard fetches new versions of
them from GitHub by itself. The Pi itself installs signed releases, see [below](#updating-the-pi).

## One release of everything

```bash
./scripts/ship.sh            # on main, with everything pushed
```

This is the whole release: the Pi with its dashboard, the phone app, the desktop app and the agent get one version
number. It follows from the commits since the last release: `feat: ...` makes a minor version (1.9.0), `fix!: ...` or a
`BREAKING CHANGE` line a major one (2.0.0), anything else a patch (1.8.1). `./scripts/ship.sh minor` chooses it,
`--next` only shows it. The script writes the version into `pyproject.toml` and `src/pi_guard/__init__.py`, commits it
as `release: X.Y.Z`, runs `release-apps.sh` and `release.sh tag` (which asks for your release key's passphrase), and
pushes. If a step fails, run it again: it goes on where it stopped.

What happens then:

| | New minor or major (1.9.0, 2.0.0) | New patch (1.8.1) |
| --- | --- | --- |
| Pi and dashboard | installs itself within an hour and tells you (`update_auto`, *Install new versions by itself*) | tells you; *System → Updates → Update now* installs it |
| Phone app | notification, installs by itself on Wi-Fi (on mobile data a tap does it) | notification, a tap installs it |
| Desktop app | installs by itself | notification, a click installs it |
| Agent | installs by itself | installs by itself |

Each pi-guard looks for new releases every hour. *Check for updates* in the phone or desktop app makes it look on GitHub
at once (at most every 5 minutes, since anyone in the home network may ask).

## Where updates come from

*System → Updates* names the GitHub repository (`owner/name`, default `lytexdev/pi-guard`) and the channel:

- **Stable** takes `release-*` tags for the Pi and normal `apps-*` releases for the apps.
- **Beta** also takes `beta-*` tags and pre-releases, newest by date. `./scripts/ship.sh --beta` makes one
  (`release.sh tag beta`, `release-apps.sh --prerelease`).

pi-guard writes the choice to `update/source` in its data folder; the root job reads it like anything from pi-guard (no
links, one short checked line), points the clone's `origin` there, and still installs only a release signed with the
keys in `/opt/pi-guard/release-signers`. A fork with a different history is refused ("history differs"). The apps
check the update key built into them, whichever repository the files come from.

Each installation also gets an **instance ID** at its first start, shown on the same page. It never leaves the Pi;
it is there for later use such as support.

## Why this is safe

A pi-guard hands updates out, but it cannot make them. Every release is signed with an Ed25519 key that stays on the
computer you build on and never comes to the Pi. Agents and desktop apps have the public half built in and check before
anything is installed:

- the signature, which covers product, platform, version and checksum, so a signed agent cannot pass as a desktop app
  and a signed 1.0 not as a 2.0
- the checksum of the file itself
- that the version is newer, so nobody can bring back an old fault with a once valid file

So someone who breaks into your Pi cannot send anything to your other machines. The connection to the Pi is checked
against its own certificate authority as well.

The phone app relies on Android, which installs an update only if it carries the app's signature.

| | Checks | Installs | Runs |
| --- | --- | --- | --- |
| Agent | every 6 hours | by itself; a new version must start on that machine first, or the old one stays | restarts itself |
| Desktop app (Linux) | every 3 hours | by itself, the running version stays untouched | at the next start, or on a click |
| Desktop app (Windows) | every 3 hours | by itself after the check, then it closes | at the next start |
| Desktop app (macOS) | every 3 hours | shows the checked file, it must be replaced by hand | |
| Phone app | every 3 hours | a new minor or major version on Wi-Fi: the first time after a tap, then by itself on Android 12 and newer; a patch after a tap on its notification | after the update |

The desktop app only updates itself in the Linux install from `scripts/install-desktop.sh` (and on Windows after the
NSIS installer), not as AppImage or `.deb`. First install without a local file:

```bash
./scripts/install-desktop.sh          # Linux: downloads the newest apps-* tar.gz
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop.ps1   # Windows: newest .exe
```

Or pass a local archive / installer / URL. See [desktop.md](desktop.md). `AUTO_UPDATE=false` in
`/etc/pi-guard-agent/agent.conf` switches it off for an agent.

## Releases on GitHub

One command builds, signs and publishes the phone app, the desktop app, the agent and `pgproxy` (the proxy binaries
are not signed for updates; they are checked against `SHA256SUMS`):

```bash
./scripts/release-apps.sh 1.4.0                   # everything this computer can build
./scripts/release-apps.sh 1.4.0 android desktop   # only these
./scripts/release-apps.sh 1.4.0 --no-upload       # build and sign into dist/apps-1.4.0
```

It makes the GitHub release `apps-1.4.0` with all files to download (APK, AppImage, `.deb`, `.rpm`, `.pacman`,
`.tar.gz`, the Windows installer when wine is there, the agent), a `SHA256SUMS`, and one signed manifest for each file
devices update from, such as `android-apk.json` or `desktop-linux-x64.json`. It needs the update key, the app's
keystore, and the GitHub CLI logged in (`gh auth login`).

Every pi-guard looks at the newest `apps-*` release every hour and takes a file only if its manifest is signed with
the update key and the file matches the signed checksum, size and version. Its devices then fetch it from the Pi and
check the same signature again. So people with their own pi-guard get updates as well, without you sending them
anything. `PI_GUARD_APP_UPDATES_FROM_GITHUB=false` (*Fetch app updates from GitHub* in the expert settings) switches
this off; then only what you publish to that Pi yourself, below, reaches its devices.

**Why this does not run on GitHub.** Building there would need the signing keys there. Then anyone who breaks into the
GitHub account could sign updates for every device, which is exactly what the keys on your computer prevent. With the
keys at home, a broken-into account can only hold updates back. The phone app's version code is signed as well
(`1.4.0+812`), so an old APK cannot pass as a new one.

## Publishing a release to one pi-guard

```bash
./scripts/publish-update.sh agent linux-x86_64 0.2.0 agent/target/release/pi-guard-agent system
./scripts/publish-update.sh desktop linux-x64 1.1.0 desktop/dist/PiGuard-1.1.0-linux-x64.tar.gz system
./scripts/build-android.sh 1.3.0 --publish system
```

The last argument is the ssh host of your pi-guard. The key is `~/.config/pi-guard/update-ed25519.pem`.

An agent takes a new file only when it answers `--version` with the version it was signed for. Build it with that
version: `PI_GUARD_VERSION=0.5.0 cargo build --release` (`release-apps.sh` does this itself). Agents of release 1.16
and older said their crate version (0.4.0) and refused every update of 1.17 with *the new version does not run here*;
install such an agent once by hand, see *Manage says the agent is too old* in [troubleshooting.md](troubleshooting.md).

**Back it up.** Without it, no agent and no desktop app accepts a new version, and they would have to be installed again
by hand. Anyone who has it can sign updates for your machines, so keep it as safe as the app's signing key.

If you build your own, make your own key and put its public half into `agent/src/update.rs` and `desktop/updates.js`.

## Updating the Pi

The update button and `scripts/update.sh` install a new version as root. So they do not trust GitHub: they install only a
release tag signed with a key that stays on your computer. Someone who gets into the GitHub account can change `main`,
but no Pi will run it. A Pi takes the newest tag with a valid signature, and only if it continues the running version, so
an old release cannot be brought back.

Git commits on `main` alone are no release: after a merge, the Pi keeps its version until you sign one.

The request folder `/var/lib/pi-guard/update` belongs to pi-guard; the root job only reads and removes the note, and
gives back a folder an older version made as root. It compares the signed release with the installed commit
(`/opt/pi-guard/commit`), so a checkout moved by `git pull` still gets installed.

After installing, the service must report healthy within a minute; otherwise the previous version is put back and the
dashboard says *new version did not start; previous version restored*. Database changes of the new version stay, so a
rollback is a way out of a start failure, not a downgrade path. On 32-bit boards the update keeps using the
distribution's OpenCV, as the installer chose.

**Once, on your own computer, never on a Pi:**

```bash
./scripts/release.sh key
git add deploy/update/release-signers && git commit -m "build: the release key"
```

The key is `~/.config/pi-guard/release-ed25519`. Choose a passphrase and back it up. Then bring this commit to each Pi
and run `sudo ./scripts/install.sh` there: it stores the public half in `/opt/pi-guard/release-signers`. Until then, the
update button reports that there is no release key, and installing by hand still works.

**For each release,** with `main` pushed and checked out:

```bash
./scripts/release.sh tag
```

`scripts/ship.sh` does this as its last step. A pi-guard installed with `install.sh` installs a new minor or major
release by itself within an hour (`PI_GUARD_UPDATE_AUTO=false` turns that off) and only announces a patch; either way
it tells the admins in the chat. It leaves the same note as the update button, so the signature check above still
decides. A Pi on an older release cannot do this yet, so it takes the first new release with the update button.

Each update also refreshes optional root parts that were already installed (privacy exit helpers and path units,
WireGuard apply/status, AmneziaWG sync, update/maint path units) and restarts them when needed. It never installs
those for the first time: that stays `privacy-server.sh install`, `wireguard.sh setup`, or `amneziawg.sh` once.

An update never changes `/opt/pi-guard/release-signers`, or stolen access could replace it. To change the key, for
example because it was lost, make a new one and put it there by hand on each Pi:

```bash
sudo install -m 0644 deploy/update/release-signers /opt/pi-guard/release-signers
```

If you run your own fork, make your own key the same way. The dashboard shows the newest tag from GitHub only as a hint;
the check happens in `pi-guard-release-check` on the Pi.

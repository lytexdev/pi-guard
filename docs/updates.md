# Updates

Agents, desktop apps and the phone app update themselves from your pi-guard.

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
| Desktop app (Linux) | every 6 hours | by itself, the running version stays untouched | at the next start, or on a click |
| Desktop app (Windows) | every 6 hours | by itself after the check, then it closes | at the next start |
| Desktop app (macOS) | every 6 hours | shows the checked file, it must be replaced by hand | |
| Phone app | once a day, on Wi-Fi | the first time after a tap, then by itself on Android 12 and newer | after the update |

The desktop app only updates itself in the Linux install from `scripts/install-desktop.sh`, not as AppImage or `.deb`.
`AUTO_UPDATE=false` in `/etc/pi-guard-agent/agent.conf` switches it off for an agent.

## Publishing a release

```bash
./scripts/publish-update.sh agent linux-x86_64 0.2.0 agent/target/release/pi-guard-agent system
./scripts/publish-update.sh desktop linux-x64 1.1.0 desktop/dist/PiGuard-1.1.0-linux-x64.tar.gz system
./scripts/build-android.sh 1.3.0 --publish system
```

The last argument is the ssh host of your pi-guard. The key is `~/.config/pi-guard/update-ed25519.pem`.

**Back it up.** Without it, no agent and no desktop app accepts a new version, and they would have to be installed again
by hand. Anyone who has it can sign updates for your machines, so keep it as safe as the app's signing key.

If you build your own, make your own key and put its public half into `agent/src/update.rs` and `desktop/updates.js`.

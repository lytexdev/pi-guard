# Backups

pi-guard can back up its configuration and data by itself: to a Nextcloud, over rsync to a server, or into a Borg
repository. Set it up under *Settings → Backup*, or with the `PI_GUARD_BACKUP_*` settings.

## What is in a backup

- `/etc/pi-guard`: the configuration and the `.env` file, with its tokens and passwords
- the data directory: the databases (copied with SQLite's own backup, so nothing half-written), face prints of enrolled
  people, keys and certificates, sounds, the Signal link, added languages, dashboard settings

Left out unless *Recordings and pictures too* is on: recordings, snapshots and evidence videos (large), models and app
files (fetched again by themselves). The Pi's SSH key for backups is left out as well.

## Encryption

A backup holds everything needed to take over your pi-guard and the face prints of the people in it. So it leaves the
Pi only encrypted, with the **backup passphrase** (at least 12 characters):

- a key from the passphrase with scrypt (2^15, 8, 1), a new random salt for each backup
- AES-256-GCM over pieces of 1 MB; each piece is sealed with its number and with whether it is the last, so a backup
  that was cut short, extended or changed does not decrypt
- Borg encrypts by itself (`repokey-blake2`) with the same passphrase, and refuses a repository without encryption

Nextcloud or the server see only the encrypted file. **Without the passphrase nobody can restore a backup, not even
you.** Keep it apart from the Pi, for example in a password manager.

## Targets

| Target | Settings | Notes |
|---|---|---|
| Nextcloud | address (`https://…`), user, app password, folder | Use an app password (Nextcloud → Settings → Security), not your own. Uploaded over WebDAV; the oldest beyond *Backups to keep* are deleted. |
| rsync | `user@host:folder` | Over SSH with the Pi's own key, shown under *Settings → Backup* (*Make the key*): add it to `~/.ssh/authorized_keys` of that user. The server's key is remembered on first contact. |
| Borg | `ssh://user@host/folder` or a folder on the Pi | Needs `borg` on the Pi (`sudo apt install borgbackup`) and on the server. The repository is made on the first backup. |

Backups run daily or weekly at the chosen hour; a failed one is tried again after three hours. *Back up now* asks for
the dashboard password. The last result shows under *Settings → Backup*, and every backup goes into the audit log.

## Restoring

```bash
pi-guard backup restore pi-guard-backup-20261109T030000Z.pgbk --into restore
```

It asks for the passphrase, decrypts, and unpacks into `restore/pi-guard-backup`, without replacing anything on the
system. Then, on a Pi with pi-guard installed:

```bash
sudo systemctl stop pi-guard
sudo cp -a restore/pi-guard-backup/config/. /etc/pi-guard/
sudo cp -a restore/pi-guard-backup/data/. /var/lib/pi-guard/
sudo chown -R pi-guard:pi-guard /var/lib/pi-guard
sudo systemctl start pi-guard
```

For Borg: `borg extract REPO::pi-guard-…` with the passphrase, then the same copy steps from its `data` and `config`.

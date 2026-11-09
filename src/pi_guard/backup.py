"""Backups of pi-guard's configuration and data: to Nextcloud, over rsync, or into Borg.

What goes in: the configuration (also the .env with its tokens), the databases (copied with
SQLite's backup, so nothing half-written), keys, sounds, Signal's link and the languages
added. Recordings, pictures and models only on request: they are large or can be fetched again.

Because tokens, keys and face prints are in it, a backup leaves the Pi only encrypted:
AES-256-GCM with a key from the backup passphrase (scrypt). The file is cut into pieces of
1 MB, each sealed with its number and whether it is the last, so a shortened or changed
backup does not decrypt. Borg encrypts by itself (repokey) and gets the files directly.
Without the passphrase, nobody can restore a backup, also not its owner: keep it apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shlex
import shutil
import sqlite3
import struct
import subprocess
import tarfile
import tempfile
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import secure_file

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

MAGIC = b"PGBK1"
CHUNK = 1 << 20
SCRYPT = {"n": 2**15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}
PREFIX = "pi-guard-backup-"
SUFFIX = ".pgbk"
LAST_KEY = "backup.last"
CHECK_SECONDS = 600
# Left out of a backup unless asked for: large, or fetched again on their own.
BULKY = {"recordings", "media", "models", "apps", "update"}
SKIP = {"__pycache__", "maint", "backup-ssh"}
MIN_PASSPHRASE = 12


class BackupError(Exception):
    pass


# --- encryption -----------------------------------------------------------------------------


def _key(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode(), salt=salt, **SCRYPT)


def _nonce(prefix: bytes, number: int, last: bool) -> bytes:
    return prefix + struct.pack(">I", number) + (b"\x01" if last else b"\x00")


def encrypt(source: Path, target: Path, passphrase: str) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, prefix = os.urandom(16), os.urandom(7)
    aead = AESGCM(_key(passphrase, salt))
    size = source.stat().st_size
    with source.open("rb") as src, target.open("wb") as out:
        out.write(MAGIC + salt + prefix)
        number, done = 0, 0
        while True:
            piece = src.read(CHUNK)
            done += len(piece)
            last = done >= size
            sealed = aead.encrypt(_nonce(prefix, number, last), piece, MAGIC)
            out.write(struct.pack(">I", len(sealed)) + sealed)
            number += 1
            if last:
                break


def decrypt(source: Path, target: Path, passphrase: str) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with source.open("rb") as src, target.open("wb") as out:
        head = src.read(len(MAGIC) + 16 + 7)
        if not head.startswith(MAGIC) or len(head) < len(MAGIC) + 23:
            raise BackupError("not a pi-guard backup")
        salt, prefix = head[len(MAGIC) : len(MAGIC) + 16], head[len(MAGIC) + 16 :]
        aead = AESGCM(_key(passphrase, salt))
        number, finished = 0, False
        while not finished:
            length = src.read(4)
            if len(length) < 4:
                raise BackupError("the backup is cut short")
            sealed = src.read(struct.unpack(">I", length)[0])
            for last in (False, True):
                try:
                    out.write(aead.decrypt(_nonce(prefix, number, last), sealed, MAGIC))
                except InvalidTag:
                    continue
                finished = last
                break
            else:
                raise BackupError("wrong passphrase, or the backup was changed")
            number += 1
        if src.read(1):
            raise BackupError("data after the end of the backup")


# --- collecting -------------------------------------------------------------------------------


def _copy_database(source: Path, target: Path) -> None:
    with contextlib.closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src, \
            contextlib.closing(sqlite3.connect(target)) as dst:  # fmt: skip
        src.backup(dst)


def _files(root: Path, bulky: bool) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(p in SKIP for p in parts) or (not bulky and parts[0] in BULKY):
            continue
        if (
            path.name.endswith(("-wal", "-shm", "-journal"))
            or not path.is_file()
            or path.is_symlink()
        ):
            continue
        yield path


def collect(data_dir: Path, config_dir: Path | None, staging: Path, bulky: bool = False) -> int:
    """Copy everything into `staging/data` and `staging/config`; returns the number of files."""
    count = 0
    for path in _files(data_dir, bulky):
        target = staging / "data" / path.relative_to(data_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".db":
            try:
                _copy_database(path, target)
            except sqlite3.DatabaseError:
                shutil.copy2(path, target)
        else:
            shutil.copy2(path, target)
        count += 1
    if config_dir is not None and config_dir.is_dir():
        for path in sorted(config_dir.iterdir()):
            if path.is_file() and os.access(path, os.R_OK):
                target = staging / "config" / path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                count += 1
    (staging / "README.txt").write_text(
        "pi-guard backup. data/ belongs in the data directory (/var/lib/pi-guard),\n"
        "config/ in /etc/pi-guard. Stop pi-guard before putting them back.\n"
    )
    return count


def archive(staging: Path, target: Path) -> None:
    with tarfile.open(target, "w:gz") as tar:
        tar.add(staging, arcname="pi-guard-backup")


def restore(source: Path, passphrase: str, into: Path) -> Path:
    """Decrypt and unpack into `into`; nothing on the system is replaced."""
    into.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        plain = Path(tmp) / "backup.tar.gz"
        decrypt(source, plain, passphrase)
        with tarfile.open(plain) as tar:
            tar.extractall(into, filter="data")  # no links out, no devices, no absolute paths
    return into / "pi-guard-backup"


# --- targets -------------------------------------------------------------------------------


def _ssh_key(data_dir: Path) -> Path:
    """This Pi's own key for rsync and Borg over SSH; the public half goes on the server."""
    folder = data_dir / "backup-ssh"
    key = folder / "id_ed25519"
    if not key.exists():
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pi-guard backup",
                        "-f", str(key)], check=True, capture_output=True)  # fmt: skip
        secure_file(key)
    return key


def public_key(data_dir: Path) -> str | None:
    path = data_dir / "backup-ssh" / "id_ed25519.pub"
    return path.read_text().strip() if path.exists() else None


def _ssh_command(data_dir: Path) -> str:
    key = _ssh_key(data_dir)
    known = key.parent / "known_hosts"
    return (f"ssh -i {shlex.quote(str(key))} -o BatchMode=yes -o StrictHostKeyChecking=accept-new "
            f"-o UserKnownHostsFile={shlex.quote(str(known))}")  # fmt: skip


def _run(args: list[str], env: dict[str, str] | None = None, timeout: int = 3600) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False,
                            env={**os.environ, **(env or {})})  # fmt: skip
    if result.returncode != 0:
        raise BackupError((result.stderr or result.stdout).strip()[-300:] or "failed")
    return result.stdout


def _nextcloud_base(s: Any) -> str:
    url = str(s.backup_nextcloud_url or "").rstrip("/")
    folder = "/".join(quote(p) for p in str(s.backup_nextcloud_folder).strip("/").split("/") if p)
    return f"{url}/remote.php/dav/files/{quote(str(s.backup_nextcloud_user))}/{folder}"


def to_nextcloud(s: Any, file: Path) -> None:
    password = s.backup_nextcloud_password.get_secret_value() if s.backup_nextcloud_password else ""
    auth = (str(s.backup_nextcloud_user), password)
    base = _nextcloud_base(s)
    with httpx.Client(auth=auth, timeout=600) as client:
        made = client.request("MKCOL", base + "/")
        if made.status_code not in (201, 405):  # 405: exists already
            raise BackupError(f"Nextcloud folder: HTTP {made.status_code}")
        with file.open("rb") as body:
            sent = client.put(f"{base}/{file.name}", content=body)
        if sent.status_code not in (200, 201, 204):
            raise BackupError(f"Nextcloud upload: HTTP {sent.status_code}")
        listing = client.request("PROPFIND", base + "/", headers={"Depth": "1"})
        names = sorted(
            {n for n in _hrefs(listing.text) if n.startswith(PREFIX) and n.endswith(SUFFIX)}
        )
        for old in names[: max(0, len(names) - s.backup_keep)]:
            client.delete(f"{base}/{quote(old)}")


def _hrefs(xml: str) -> list[str]:
    import re
    from urllib.parse import unquote

    return [
        unquote(h.rstrip("/").rsplit("/", 1)[-1])
        for h in re.findall(r"<d:href>([^<]+)</d:href>", xml)
    ]


def to_rsync(s: Any, file: Path) -> None:
    target = str(s.backup_rsync_target)
    _run(
        [
            "rsync",
            "-a",
            "--chmod=F600",
            "-e",
            _ssh_command(s.data_dir),
            str(file),
            target.rstrip("/") + "/",
        ]
    )
    host, _, folder = target.partition(":")
    # Keep the newest backup_keep files of ours there; the folder name is the owner's own setting.
    pattern = shlex.quote(folder.rstrip("/") or ".") + "/" + PREFIX + "*" + SUFFIX
    command = (
        f"ls -1t {pattern} 2>/dev/null | tail -n +{int(s.backup_keep) + 1} | xargs -r rm -f --"
    )
    _run([*shlex.split(_ssh_command(s.data_dir)), host, command])


def to_borg(s: Any, staging: Path, stamp: str) -> None:
    passphrase = s.backup_passphrase.get_secret_value() if s.backup_passphrase else ""
    env = {
        "BORG_PASSPHRASE": passphrase,
        "BORG_RSH": _ssh_command(s.data_dir),
        "BORG_RELOCATED_REPO_ACCESS_IS_OK": "no",
        "BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK": "no",  # refuse a repository without a key
    }
    repo = str(s.backup_borg_repo)
    try:
        _run(["borg", "info", repo], env, timeout=120)
    except BackupError:
        _run(["borg", "init", "--encryption=repokey-blake2", repo], env, timeout=300)
    _run(["borg", "create", "--compression", "zstd", f"{repo}::pi-guard-{stamp}", "."], env)
    _run(
        [
            "borg",
            "prune",
            "--glob-archives",
            "pi-guard-*",
            f"--keep-last={int(s.backup_keep)}",
            repo,
        ],
        env,
    )


# --- running ---------------------------------------------------------------------------------


def run_backup(s: Any, config_dir: Path | None) -> dict[str, Any]:
    """One backup to the configured target. Blocking: call it in a thread."""
    passphrase = s.backup_passphrase.get_secret_value() if s.backup_passphrase else ""
    if len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(f"set a backup passphrase of at least {MIN_PASSPHRASE} characters")
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory(prefix="pg-backup-", dir=s.data_dir) as tmp:
        work = Path(tmp)
        work.chmod(0o700)
        staging = work / "staging"
        files = collect(s.data_dir, config_dir, staging, s.backup_recordings)
        if s.backup_target == "borg":
            to_borg(s, staging, stamp)
            return {"files": files, "size": None, "name": f"pi-guard-{stamp}"}
        plain, sealed = work / "backup.tar.gz", work / f"{PREFIX}{stamp}{SUFFIX}"
        archive(staging, plain)
        encrypt(plain, sealed, passphrase)
        plain.unlink()
        if s.backup_target == "nextcloud":
            to_nextcloud(s, sealed)
        elif s.backup_target == "rsync":
            to_rsync(s, sealed)
        else:
            raise BackupError("no backup target chosen")
        return {"files": files, "size": sealed.stat().st_size, "name": sealed.name}


RETRY = timedelta(hours=3)


def due(s: Any, last: dict[str, Any], now: datetime) -> bool:
    """At the chosen hour, daily or weekly; a failed backup is tried again after 3 hours."""
    if not s.backup_enabled or now.astimezone(s.tz).hour < s.backup_hour:
        return False
    if not last.get("time"):
        return True
    previous = datetime.fromisoformat(last["time"])
    if not last.get("ok"):
        return now - previous >= RETRY
    gap = timedelta(days=7 if s.backup_schedule == "weekly" else 1)
    return now - previous >= gap - timedelta(hours=1)


class Backups:
    def __init__(self, app: App) -> None:
        self._app = app
        self.running = False
        self.task: asyncio.Task[None] | None = None

    def last(self) -> dict[str, Any]:
        return dict(self._app.db.get(LAST_KEY) or {})

    async def run_now(self, actor: str) -> dict[str, Any]:
        if self.running:
            raise BackupError("a backup is running already")
        self.running = True
        started = utcnow()
        config_dir = Path("/etc/pi-guard")
        try:
            result = await asyncio.to_thread(run_backup, self._app.settings, config_dir)
            entry = {"time": started.isoformat(), "ok": True, **result,
                     "target": self._app.settings.backup_target}  # fmt: skip
            log.info(
                "backup done: %s files to %s", result["files"], self._app.settings.backup_target
            )
        except (BackupError, OSError, httpx.HTTPError, subprocess.SubprocessError) as exc:
            entry = {"time": started.isoformat(), "ok": False, "error": str(exc)[:300],
                     "target": self._app.settings.backup_target}  # fmt: skip
            log.warning("backup failed: %s", type(exc).__name__)
        finally:
            self.running = False
        self._app.db.set(LAST_KEY, entry)
        self._app.db.audit(actor, "backup", "ok" if entry["ok"] else "failed")
        return entry

    async def run(self) -> None:
        while True:
            if due(self._app.settings, self.last(), utcnow()):
                await self.run_now("schedule")
            await asyncio.sleep(CHECK_SECONDS)

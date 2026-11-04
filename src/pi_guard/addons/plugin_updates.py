"""Plugins installed from a Git link: which commit they came from, and updates.

An update is new code that runs with pi-guard's rights, so automatic updates are chosen per
plugin. A new version is checked like a first install and takes effect after a restart.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pi_guard.addons.plugin import UPLOAD_DIR, read_manifest
from pi_guard.core.timeutil import utcnow

log = logging.getLogger(__name__)

PREFIX = "plugin.git."
CHECK_SECONDS = 24 * 3600
START_DELAY = 600
TIMEOUT = 60
PROTOCOL = "https"
_COMMIT_RE = re.compile(r"^([0-9a-f]{40})\s")


class PluginGitError(Exception):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


async def _git(*args: str, home: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        "git", "-c", "protocol.allow=never", "-c", f"protocol.{PROTOCOL}.allow=always", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin", "HOME": home},
    )  # fmt: skip
    try:
        output, _ = await asyncio.wait_for(process.communicate(), TIMEOUT)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise PluginGitError("web.pm_git_failed") from None
    return process.returncode or 0, output.decode(errors="replace")


async def remote_commit(url: str, branch: str) -> str | None:
    """The newest commit of a branch, or of the repository's main branch if none is named."""
    ref = f"refs/heads/{branch}" if branch else "HEAD"
    with tempfile.TemporaryDirectory() as home:
        code, output = await _git("ls-remote", "--", url, ref, home=home)
    match = _COMMIT_RE.match(output)
    return match.group(1) if code == 0 and match else None


async def _clone(url: str, branch: str, work: Path) -> tuple[Path, str]:
    repo = work / "repo"
    shutil.rmtree(repo, ignore_errors=True)
    wanted = ("--branch", branch) if branch else ()
    code, _ = await _git("clone", "--depth", "1", "--no-recurse-submodules", *wanted, "--", url,
                         str(repo), home=str(work))  # fmt: skip
    if code != 0:
        raise PluginGitError("web.pm_git_failed")
    code, commit = await _git("-C", str(repo), "rev-parse", "HEAD", home=str(work))
    if code != 0:
        raise PluginGitError("web.pm_git_failed")
    shutil.rmtree(repo / ".git", ignore_errors=True)
    return repo, commit.strip()


def _checked(repo: Path) -> dict[str, Any]:
    try:
        manifest = read_manifest(repo)
    except (OSError, ValueError):
        raise PluginGitError("web.pm_bad_manifest") from None
    if not (repo / manifest["entry"]).is_file() or any(p.is_symlink() for p in repo.rglob("*")):
        raise PluginGitError("web.pm_bad_manifest")
    return manifest


def _replace(repo: Path, target: Path) -> None:
    previous = target.with_name(f".old-{target.name}")
    shutil.rmtree(previous, ignore_errors=True)
    if target.exists():
        target.rename(previous)
    repo.rename(target)
    shutil.rmtree(previous, ignore_errors=True)


class PluginUpdater:
    def __init__(self, app: Any) -> None:
        self._app = app
        self.root: Path = app.settings.data_dir / UPLOAD_DIR

    def record(self, plugin_id: str) -> dict[str, Any] | None:
        data: dict[str, Any] | None = self._app.db.get(PREFIX + plugin_id)
        return data

    def _save(self, plugin_id: str, record: dict[str, Any]) -> None:
        self._app.db.set(PREFIX + plugin_id, record)

    def forget(self, plugin_id: str) -> None:
        self._app.db.execute("DELETE FROM kv WHERE key = ?", (PREFIX + plugin_id,))

    def ids(self) -> list[str]:
        rows = self._app.db.execute("SELECT key FROM kv WHERE key LIKE 'plugin.git.%'").fetchall()
        return [r["key"].removeprefix(PREFIX) for r in rows]

    def set_auto(self, plugin_id: str, enabled: bool) -> bool:
        record = self.record(plugin_id)
        if record is None:
            return False
        self._save(plugin_id, {**record, "auto": enabled})
        return True

    async def install(self, url: str) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".git-", dir=self.root))
        try:
            repo, commit = await _clone(url, "", work)
            manifest = _checked(repo)
            branch = manifest["release_branch"]
            if branch:  # the plugin names the branch that holds its releases
                repo, commit = await _clone(url, branch, work)
                manifest = _checked(repo)
            _replace(repo, self.root / manifest["id"])
        finally:
            shutil.rmtree(work, ignore_errors=True)
        old = self.record(manifest["id"]) or {}
        self._save(manifest["id"], {"url": url, "branch": branch, "commit": commit,
                                    "latest": commit, "auto": bool(old.get("auto"))})  # fmt: skip
        return manifest

    async def check(self, plugin_id: str) -> dict[str, Any] | None:
        record = self.record(plugin_id)
        if record is None:
            return None
        latest = await remote_commit(record["url"], record["branch"])
        if latest is None:
            raise PluginGitError("web.pm_git_failed")
        record = {**record, "latest": latest, "checked": utcnow().isoformat()}
        self._save(plugin_id, record)
        return record

    async def update(self, plugin_id: str) -> dict[str, Any]:
        record = self.record(plugin_id)
        if record is None:
            raise PluginGitError("web.pm_git_failed")
        work = Path(tempfile.mkdtemp(prefix=".git-", dir=self.root))
        try:
            repo, commit = await _clone(record["url"], record["branch"], work)
            manifest = _checked(repo)
            if manifest["id"] != plugin_id:  # a repository must not replace another plugin
                raise PluginGitError("web.pm_bad_manifest")
            _replace(repo, self.root / plugin_id)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self._save(plugin_id, {**record, "branch": manifest["release_branch"] or record["branch"],
                               "commit": commit, "latest": commit})  # fmt: skip
        self._app.db.audit("system", "plugin.update", f"{plugin_id} {commit[:10]}")
        return manifest

    async def _pass(self) -> None:
        for plugin_id in self.ids():
            try:
                record = await self.check(plugin_id)
                if record and record["auto"] and record["latest"] != record["commit"]:
                    manifest = await self.update(plugin_id)
                    await self._app.messenger.notify_admins(
                        f"pi-guard: {manifest['name']} {manifest['version']} installed. "
                        "It takes effect after a restart."
                    )
            except (PluginGitError, OSError) as exc:
                log.warning("plugin update check failed: %s", type(exc).__name__)

    async def run(self) -> None:
        await asyncio.sleep(START_DELAY)
        while True:
            await self._pass()
            await asyncio.sleep(CHECK_SECONDS)

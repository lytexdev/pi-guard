"""New versions of pi-guard itself: tell the owner, and start the update for a new major or minor.

`scripts/ship.sh` makes a release: a signed `release-*` tag whose `__version__` is the version
of the apps released with it. Every hour this looks at the newest such tag on GitHub. A new patch
(1.8.1) is only announced; a new minor or major version (1.9.0, 2.0.0) is also installed, unless
`update_auto` is off.

Nothing here has to trust GitHub. It only leaves the same note as the dashboard's update button,
and the root job (`scripts/update.sh`) installs nothing but a release signed with the owner's
key. Whoever fakes a version on GitHub can at most make the Pi look for an update.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from pi_guard import __version__
from pi_guard.core.app_updates import step
from pi_guard.security.files import atomic_write

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

REPOSITORY = "lytexdev/pi-guard"  # the default; Settings > Updates may name another
TAG = re.compile(r"^release-\d{8}-\d{6}$")
BETA_TAG = re.compile(r"^(?:release|beta)-\d{8}-\d{6}$")
SOURCE_FILE = Path("update") / "source"  # for the root job: which repository and channel
VERSION_LINE = re.compile(r'^__version__ = "(\d{1,4}\.\d{1,4}\.\d{1,4})"$', re.M)
EVERY_SECONDS = 3600
COMMIT_FILE = Path("/opt/pi-guard/commit")  # written by install.sh and update.sh
TOLD = "update.told"  # the newest tag the owner heard about, so each comes once

_cache: tuple[float, dict[str, Any]] = (float("-inf"), {})


def _date(tag: str) -> str:
    return tag.split("-", 1)[1]


async def latest(
    client: httpx.AsyncClient, repository: str = REPOSITORY, channel: str = "stable"
) -> dict[str, Any]:
    """The newest release tag on GitHub, with its commit and version; {} if there is none."""
    api = f"https://api.github.com/repos/{repository}"
    headers = {"Accept": "application/vnd.github+json"}
    tags = await client.get(f"{api}/tags?per_page=100", headers=headers)
    tags.raise_for_status()
    pattern = BETA_TAG if channel == "beta" else TAG
    names = sorted((str(t["name"]) for t in tags.json() if pattern.match(str(t.get("name", "")))),
                   key=_date)  # fmt: skip
    if not names:
        return {}
    tag = names[-1]  # the names end in dates, so the newest sorts last
    commit = await client.get(f"{api}/commits/{tag}", headers=headers)
    commit.raise_for_status()
    data = commit.json()
    info: dict[str, Any] = {"tag": tag, "commit": str(data["sha"])[:10],
                            "date": data["commit"]["committer"]["date"],
                            "message": str(data["commit"]["message"]).split("\n")[0][:120],
                            "version": None}  # fmt: skip
    source = await client.get(
        f"https://raw.githubusercontent.com/{repository}/{tag}/src/pi_guard/__init__.py"
    )
    if source.status_code == 200:
        match = VERSION_LINE.search(source.text[:4096])
        info["version"] = match[1] if match else None
    return info


async def remote(
    force: bool = False, repository: str = REPOSITORY, channel: str = "stable"
) -> dict[str, Any]:
    """`latest`, asked at most once an hour; the last answer when GitHub cannot be reached."""
    global _cache
    fresh = time.monotonic() - _cache[0] < EVERY_SECONDS
    if not force and fresh and _cache[1] and _cache[1].get("source") == (repository, channel):
        return _cache[1]
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "pi-guard"}) as client:
            info = await latest(client, repository, channel)
            if info:
                info["source"] = (repository, channel)
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return _cache[1]
    _cache = (time.monotonic(), info)
    return info


def installed_commit() -> str | None:
    try:
        return COMMIT_FILE.read_text().strip()[:10] or None
    except OSError:
        return None


class SelfUpdate:
    def __init__(self, app: App) -> None:
        self._app = app

    def tell_root(self) -> None:
        """Which repository and channel the root job fetches from; it checks this itself."""
        s = self._app.settings
        path = s.data_dir / SOURCE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, f"{s.update_repository} {s.update_channel}\n".encode(), 0o644)

    async def run(self) -> None:
        with contextlib.suppress(OSError):
            self.tell_root()
        await asyncio.sleep(120)  # after the network, and after app updates had their turn
        s = self._app.settings
        while True:
            try:
                await self.consider(await remote(True, s.update_repository, s.update_channel))
            except OSError as exc:
                log.warning("pi-guard release not considered: %s", type(exc).__name__)
            await asyncio.sleep(EVERY_SECONDS)

    async def consider(self, info: dict[str, Any], running: str = __version__) -> str | None:
        """What was done about the newest release: "installing", "offered" or None."""
        version, tag = info.get("version"), info.get("tag")
        kind = step(version, running) if isinstance(version, str) else None
        if kind is None or not tag:
            return None
        if installed_commit() == info.get("commit"):
            return None  # an older Pi that took this release by hand
        if self._app.db.get(TOLD) == tag:
            return None
        self._app.db.set(TOLD, tag)
        from pi_guard.i18n import t

        lang = self._app.settings.language
        request = self._app.settings.data_dir / "update" / "request"
        automatic = (kind in ("major", "minor") and bool(self._app.settings.update_auto)
                     and installed_commit() is not None)  # fmt: skip
        if automatic:
            request.parent.mkdir(parents=True, exist_ok=True)
            request.write_text(str(time.time()))
            self._app.db.audit("system", "system.update", f"auto {version}")
            await self._app.messenger.notify_admins(t("update.installing", lang, version=version))
            return "installing"
        await self._app.messenger.notify_admins(t("update.available", lang, version=version))
        return "offered"

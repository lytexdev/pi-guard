"""New versions of the agent, the desktop app and the phone app, fetched from GitHub releases.

A pi-guard hands releases to its devices (web/api_updates.py, web/api_mobile.py). With this,
they arrive by themselves: every few hours the Pi looks at the newest `apps-*` release of the
project and takes a file only if its signature checks out against the update key, whose public
half is built in here, in the agent and in the desktop app. The devices check again. So a
broken-into GitHub account cannot hand anything to a device: it can only hold updates back.

`scripts/release-apps.sh` makes such a release. Each file comes with PRODUCT-PLATFORM.json:
version, sha256, size, signature and the asset name. The phone app's version also carries its
Android version code, as "1.4.0+812", so the code is signed as well.

The step between two versions decides what devices do: a new major or minor version (2.0.0,
1.9.0) installs by itself, a new patch (1.8.1) is only offered. `step` says which it is.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from pi_guard.security.files import atomic_write

if TYPE_CHECKING:
    from pi_guard.app import App

log = logging.getLogger(__name__)

REPOSITORY = "lytexdev/pi-guard"  # the default; Settings > Updates may name another


def releases_url(repository: str = REPOSITORY) -> str:
    return f"https://api.github.com/repos/{repository}/releases?per_page=10"


RELEASES = releases_url()
# The same key as in agent/src/update.rs and desktop/updates.js. Its private half never leaves
# the computer that builds releases. A fork puts its own here.
UPDATE_KEY = bytes.fromhex("7cdf1b203f7747803d04eaf7cdc52350c684f6e5b3b0ea64cd1636b593b8b716")
EVERY_SECONDS = 3600
# "Check for updates" on a device asks GitHub at once, but not more often than this: anyone in the
# home network may ask, and GitHub allows a device 60 questions an hour.
ASK_SECONDS = 300
ASK_WAIT = 90
MAX_MANIFEST = 4096
MAX_FILE = 400 * 1024 * 1024
MANIFEST = re.compile(r"^(agent|desktop|android)-([a-z0-9_-]{1,32})\.json$")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:\+(\d{1,9}))?$")
ASSET = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class BadRelease(ValueError):
    pass


@dataclass(frozen=True)
class Manifest:
    product: str
    platform: str
    version: str  # "1.4.0", for the phone app "1.4.0+812"
    sha256: str
    size: int
    signature: str
    file: str

    @property
    def parts(self) -> tuple[int, ...]:
        match = VERSION.match(self.version)
        assert match is not None
        return tuple(int(p) for p in match.groups(default="0"))

    @property
    def name(self) -> str:
        return self.version.split("+")[0]

    @property
    def code(self) -> int:
        return self.parts[3]


def step(candidate: str, present: str | None) -> str | None:
    """ "major", "minor" or "patch" when `candidate` is newer than `present`, else None.

    A build number after "+" (Android's version code) alone is a patch. Unreadable means None.
    """
    new, old = VERSION.match(candidate), VERSION.match(present or "")
    if new is None or old is None:
        return None
    a = tuple(int(p) for p in new.groups(default="0"))
    b = tuple(int(p) for p in old.groups(default="0"))
    if a <= b:
        return None
    if a[0] != b[0]:
        return "major"
    return "minor" if a[1] != b[1] else "patch"


def message(product: str, platform: str, version: str, sha256: str) -> bytes:
    """What the release script signs: product, platform, version and checksum together."""
    return f"pi-guard-update|{product}|{platform}|{version}|{sha256}".encode()


def parse(name: str, raw: bytes, key: bytes | None = None) -> Manifest:
    """A manifest from a release, or BadRelease. Its signature is checked here."""
    match = MANIFEST.match(name)
    if match is None or len(raw) > MAX_MANIFEST:
        raise BadRelease("not a manifest")
    try:
        data = json.loads(raw)
        fields = (str(data["version"]), str(data["sha256"]), int(data["size"]))
        manifest = Manifest(match[1], match[2], *fields, str(data["signature"]), str(data["file"]))
    except (ValueError, KeyError, TypeError):
        raise BadRelease("unreadable manifest") from None
    version = VERSION.match(manifest.version)
    if version is None or (manifest.product == "android") != (version[4] is not None):
        raise BadRelease("bad version")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest.sha256) or not ASSET.match(manifest.file):
        raise BadRelease("bad checksum or file name")
    if not 0 < manifest.size <= MAX_FILE:
        raise BadRelease("bad size")
    if not verify(manifest, key):
        raise BadRelease("signature does not match")
    return manifest


def verify(manifest: Manifest, key: bytes | None = None) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(key or UPDATE_KEY).verify(
            bytes.fromhex(manifest.signature),
            message(manifest.product, manifest.platform, manifest.version, manifest.sha256),
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def _is_apps(release: dict[str, Any], beta: bool = False) -> bool:
    tag = str(release.get("tag_name", ""))
    wanted = beta or not release.get("prerelease")
    return tag.startswith("apps-") and not release.get("draft") and bool(wanted)


def folder(data_dir: Path, manifest: Manifest) -> Path:
    if manifest.product == "android":
        return data_dir / "apps" / "android"
    return data_dir / "apps" / manifest.product / manifest.platform


def current(data_dir: Path, manifest: Manifest) -> tuple[int, ...] | None:
    """The version this Pi hands out now, comparable with Manifest.parts."""
    path = folder(data_dir, manifest) / "latest.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if manifest.product == "android":
        name = VERSION.match(str(data.get("version_name", "")))
        if name is None:
            return None
        return (*(int(p) for p in name.groups()[:3]), int(data.get("version_code", 0)))
    match = VERSION.match(str(data.get("version", "")))
    return tuple(int(p) for p in match.groups(default="0")) if match else None


def newer(candidate: Manifest, present: tuple[int, ...] | None) -> bool:
    if present is None:
        return True
    if candidate.product == "android":  # Android goes by the version code alone
        return candidate.code > present[3]
    return candidate.parts[:3] > present[:3]


def store(data_dir: Path, manifest: Manifest, file: Path) -> None:
    """Put a checked file where devices fetch it, in the shape they expect."""
    target = folder(data_dir, manifest)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if manifest.product == "android":
        file.replace(target / "latest.apk")
        meta: dict[str, Any] = {"version_code": manifest.code, "version_name": manifest.name,
                                "sha256": manifest.sha256, "size": manifest.size,
                                "signature": manifest.signature, "source": "github"}  # fmt: skip
    else:
        file.replace(target / "latest.bin")
        meta = {"version": manifest.version, "sha256": manifest.sha256, "size": manifest.size,
                "signature": manifest.signature, "source": "github"}  # fmt: skip
    atomic_write(target / "latest.json", json.dumps(meta).encode(), 0o600)


class AppUpdates:
    def __init__(self, app: App) -> None:
        self._app = app
        self.last: dict[str, str] = {}  # product-platform -> what happened, for the dashboard
        self._lock = asyncio.Lock()
        self._asked = float("-inf")  # monotonic; "never" must not mean "just now" after a boot

    @property
    def enabled(self) -> bool:
        return bool(self._app.settings.app_updates_from_github)

    async def run(self) -> None:
        await asyncio.sleep(60)  # let the network come up after a boot
        while True:
            if self.enabled:
                try:
                    async with self._lock:
                        await self.check()
                except (httpx.HTTPError, OSError, ValueError) as exc:
                    log.warning("app releases not checked: %s", type(exc).__name__)
            await asyncio.sleep(EVERY_SECONDS)

    async def ask(self) -> None:
        """A device's "check for updates": look on GitHub now, at most every ASK_SECONDS.

        Waits at most ASK_WAIT seconds; a slow download goes on and is there next time.
        """
        if not self.enabled or time.monotonic() - self._asked < ASK_SECONDS:
            return
        self._asked = time.monotonic()

        async def locked() -> None:
            async with self._lock:
                await self.check()

        task = asyncio.ensure_future(locked())
        try:
            await asyncio.wait_for(asyncio.shield(task), ASK_WAIT)
        except TimeoutError:
            pass
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log.warning("app releases not checked: %s", type(exc).__name__)

    async def check(self) -> list[str]:
        """Fetch what is new. Returns product-platform names that were updated."""
        data_dir = self._app.settings.data_dir
        updated: list[str] = []
        async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                     headers={"User-Agent": "pi-guard"}) as client:  # fmt: skip
            s = self._app.settings
            repository = s.update_repository
            url = RELEASES if repository == REPOSITORY else releases_url(repository)
            answer = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            answer.raise_for_status()
            beta = s.update_channel == "beta"
            release = next((r for r in answer.json() if _is_apps(r, beta)), None)
            if release is None:
                return updated
            assets = {str(a["name"]): str(a["browser_download_url"]) for a in release["assets"]}
            for name, url in sorted(assets.items()):
                if not MANIFEST.match(name):
                    continue
                key = name.removesuffix(".json")
                try:
                    manifest = parse(name, (await client.get(url)).content[: MAX_MANIFEST + 1])
                except BadRelease as exc:
                    log.warning("release file %s refused: %s", name, exc)
                    self.last[key] = f"refused: {exc}"
                    continue
                if not newer(manifest, current(data_dir, manifest)):
                    self.last[key] = f"current: {manifest.version}"
                    continue
                if manifest.file not in assets:
                    self.last[key] = "file missing"
                    continue
                try:
                    await self._fetch(client, assets[manifest.file], manifest, data_dir)
                except BadRelease as exc:
                    log.warning("release file %s refused: %s", manifest.file, exc)
                    self.last[key] = f"refused: {exc}"
                    continue
                log.info("%s %s %s fetched from GitHub", manifest.product, manifest.platform,
                         manifest.version)  # fmt: skip
                self.last[key] = f"updated: {manifest.version}"
                updated.append(key)
        return updated

    async def _fetch(self, client: httpx.AsyncClient, url: str, manifest: Manifest,
                     data_dir: Path) -> None:  # fmt: skip
        target = folder(data_dir, manifest)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        digest, size = hashlib.sha256(), 0
        with tempfile.NamedTemporaryFile(dir=target, delete=False) as out:
            part = Path(out.name)
            try:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > manifest.size:
                            raise BadRelease("file larger than signed")
                        digest.update(chunk)
                        out.write(chunk)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        if size != manifest.size or digest.hexdigest() != manifest.sha256:
            part.unlink(missing_ok=True)
            raise BadRelease("file does not match its signed checksum")
        await asyncio.to_thread(store, data_dir, manifest, part)

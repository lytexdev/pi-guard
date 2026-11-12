"""New releases: a patch is offered, a minor or major version installs, a device can ask now."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest

from pi_guard.core import app_updates, self_update
from pi_guard.core.app_updates import step
from pi_guard.core.self_update import TOLD, latest

from .helpers import make_app


def test_the_step_between_versions() -> None:
    assert step("1.9.0", "1.8.4") == "minor"
    assert step("2.0.0", "1.8.4") == "major"
    assert step("1.8.5", "1.8.4") == "patch"
    assert step("1.8.4+155", "1.8.4+154") == "patch"  # a rebuild of the phone app
    assert step("1.9.0+150", "1.8.4+154") == "minor"
    assert step("1.8.4", "1.8.4") is None
    assert step("1.8.3", "1.8.4") is None
    assert step("1.9", "1.8.4") is None and step("1.9.0", None) is None


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    application = make_app(tmp_path)
    told: list[str] = []

    async def notify(text: str, *args: Any, **kwargs: Any) -> None:
        told.append(text)

    monkeypatch.setattr(application.messenger, "notify_admins", notify)
    application.told = told
    commit = tmp_path / "commit"
    commit.write_text("0123456789abcdef\n")
    monkeypatch.setattr(self_update, "COMMIT_FILE", commit)
    yield application
    application.db.close()


def release(version: str | None, tag: str = "release-20261112-150000") -> dict[str, Any]:
    return {"tag": tag, "commit": "fedcba9876", "version": version}


async def test_a_minor_version_installs_by_itself(app: Any) -> None:
    assert await app.self_update.consider(release("1.9.0"), running="1.8.4") == "installing"
    assert (app.settings.data_dir / "update" / "request").exists()
    assert len(app.told) == 1 and "1.9.0" in app.told[0]
    # Once per release, or a failed update would start again every hour.
    assert await app.self_update.consider(release("1.9.0"), running="1.8.4") is None
    assert app.db.get(TOLD) == "release-20261112-150000"


async def test_a_patch_is_only_offered(app: Any) -> None:
    assert await app.self_update.consider(release("1.8.5"), running="1.8.4") == "offered"
    assert not (app.settings.data_dir / "update" / "request").exists()
    assert len(app.told) == 1


async def test_without_auto_or_the_install_script_it_is_only_offered(
    app: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app.settings.update_auto = False
    assert await app.self_update.consider(release("2.0.0"), running="1.8.4") == "offered"
    app.settings.update_auto = True
    monkeypatch.setattr(self_update, "COMMIT_FILE", tmp_path / "missing")
    other = release("3.0.0", tag="release-20261113-150000")
    assert await app.self_update.consider(other, running="1.8.4") == "offered"
    assert not (app.settings.data_dir / "update" / "request").exists()


async def test_nothing_for_the_same_an_older_or_an_unknown_version(app: Any) -> None:
    for version in ("1.8.4", "1.8.0", None, "x"):
        assert await app.self_update.consider(release(version), running="1.8.4") is None
    installed = {"tag": "release-20261112-150000", "commit": "0123456789", "version": "1.9.0"}
    assert await app.self_update.consider(installed, running="1.8.4") is None
    assert app.told == []


async def test_the_newest_release_tag_and_its_version() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/tags"):
            return httpx.Response(
                200,
                json=[
                    {"name": n}
                    for n in (
                        "release-20261109-093802",
                        "apps-1.8.0",
                        "release-20261112-150000",
                        "release-x",
                    )
                ],
            )
        if "/commits/" in path:
            assert path.endswith("/release-20261112-150000")
            return httpx.Response(
                200,
                json={
                    "sha": "fedcba9876543210",
                    "commit": {
                        "committer": {"date": "2026-11-12T15:00:00Z"},
                        "message": "release: 1.9.0\n\nx",
                    },
                },
            )
        assert path.endswith("/release-20261112-150000/src/pi_guard/__init__.py")
        return httpx.Response(200, text='"""pi-guard."""\n\n__version__ = "1.9.0"\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        info = await latest(client)
    assert info["tag"] == "release-20261112-150000" and info["version"] == "1.9.0"
    assert info["commit"] == "fedcba9876" and info["message"] == "release: 1.9.0"


async def test_a_device_asking_checks_github_at_most_every_few_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = make_app(tmp_path)
    calls: list[int] = []

    async def check() -> list[str]:
        calls.append(1)
        return []

    monkeypatch.setattr(application.app_updates, "check", check)
    await application.app_updates.ask()
    await application.app_updates.ask()
    assert len(calls) == 1
    monkeypatch.setattr(app_updates, "ASK_SECONDS", 0)
    await application.app_updates.ask()
    assert len(calls) == 2
    application.settings.app_updates_from_github = False
    await application.app_updates.ask()
    assert len(calls) == 2
    application.db.close()


async def test_a_slow_download_does_not_hold_the_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = make_app(tmp_path)
    done = asyncio.Event()

    async def check() -> list[str]:
        await asyncio.sleep(0.2)
        done.set()
        return []

    monkeypatch.setattr(application.app_updates, "check", check)
    monkeypatch.setattr(app_updates, "ASK_WAIT", 0.01)
    await application.app_updates.ask()
    assert not done.is_set()
    await asyncio.wait_for(done.wait(), 1)  # and it went on
    application.db.close()


def test_the_release_version_is_the_same_everywhere() -> None:
    import tomllib

    from pi_guard import __version__

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    assert project == __version__
    assert self_update.VERSION_LINE.search((root / "src/pi_guard/__init__.py").read_text())


def _ship_next(tmp_path: Path, messages: list[str], *step: str) -> str:
    import shutil
    import subprocess

    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(root / "scripts/ship.sh", repo / "scripts/ship.sh")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin",
           "HOME": str(tmp_path)}  # fmt: skip

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "first")
    git("tag", "-a", "apps-1.8.0", "-m", "x")
    git("tag", "-a", "apps-1.10.2", "-m", "x")  # sorted as versions, not as text
    for text in messages:
        git("commit", "-q", "--allow-empty", "-m", text)
    run = subprocess.run(["bash", str(repo / "scripts/ship.sh"), "--next", *step],
                         cwd=repo, env=env, capture_output=True, text=True)  # fmt: skip
    return run.stdout.strip() or run.stderr.strip()


def test_the_release_version_follows_the_commits(tmp_path: Path) -> None:
    cases = [
        (["fix: a", "docs: b"], (), "1.10.3"),
        (["fix: a", "feat(vpn): b"], (), "1.11.0"),
        (["feat: a", "fix!: b"], (), "2.0.0"),
        (["feat: a\n\nBREAKING CHANGE: the old way is gone"], (), "2.0.0"),
        (["fix: a"], ("minor",), "1.11.0"),
        ([], (), "error: nothing new since apps-1.10.2"),
        (["feat: a", "release: 1.11.0"], (), "1.11.0"),  # an unfinished run goes on
    ]
    for number, (messages, chosen, expected) in enumerate(cases):
        assert _ship_next(tmp_path / str(number), messages, *chosen) == expected, messages

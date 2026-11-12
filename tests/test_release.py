"""Which releases a Pi accepts, with real Git and real SSH signatures."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "deploy/update/pi-guard-release-check"
pytestmark = pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}  # fmt: skip


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], check=True, env=ENV,
                          capture_output=True, text=True)  # fmt: skip
    return done.stdout.strip()


def new_key(path: Path) -> Path:
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return path


def signers(path: Path, key: Path) -> Path:
    public = " ".join(key.with_suffix(".pub").read_text().split()[:2])
    path.write_text(f'pi-guard-release namespaces="git" {public}\n')
    return path


def commit(repo: Path, text: str) -> str:
    (repo / "file").write_text(text)
    git(repo, "add", "file")
    git(repo, "commit", "-q", "-m", text)
    return git(repo, "rev-parse", "HEAD")


def sign(repo: Path, name: str, key: Path) -> None:
    git(repo, "-c", "gpg.format=ssh", "-c", f"user.signingkey={key}",
        "tag", "-s", "-m", name, name)  # fmt: skip


def trusted(repo: Path, allowed: Path, channel: str = "stable") -> str | None:
    done = subprocess.run([str(CHECK), str(repo), str(allowed), channel], env=ENV,
                          capture_output=True, text=True)  # fmt: skip
    return done.stdout.strip() if done.returncode == 0 else None


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    return path


def test_the_newest_release_signed_with_the_key_counts(repo: Path, tmp_path: Path) -> None:
    key = new_key(tmp_path / "key")
    allowed = signers(tmp_path / "signers", key)
    commit(repo, "old")
    sign(repo, "release-20260101-000000", key)
    wanted = commit(repo, "new")
    sign(repo, "release-20260201-000000", key)
    commit(repo, "not released yet")
    assert trusted(repo, allowed) == wanted


def test_a_beta_counts_only_on_the_beta_channel(repo: Path, tmp_path: Path) -> None:
    key = new_key(tmp_path / "key")
    allowed = signers(tmp_path / "signers", key)
    stable = commit(repo, "stable")
    sign(repo, "release-20260101-000000", key)
    beta = commit(repo, "beta")
    sign(repo, "beta-20260301-000000", key)  # "beta" sorts before "release": the date must count
    assert trusted(repo, allowed) == stable
    assert trusted(repo, allowed, "beta") == beta
    newer = commit(repo, "stable again")
    sign(repo, "release-20260401-000000", key)
    assert trusted(repo, allowed, "beta") == newer


def test_a_signature_of_another_key_counts_for_nothing(repo: Path, tmp_path: Path) -> None:
    # What someone would do after getting into the GitHub account.
    allowed = signers(tmp_path / "signers", new_key(tmp_path / "key"))
    commit(repo, "evil")
    sign(repo, "release-20260101-000000", new_key(tmp_path / "attacker"))
    assert trusted(repo, allowed) is None


def test_unsigned_tags_count_for_nothing(repo: Path, tmp_path: Path) -> None:
    allowed = signers(tmp_path / "signers", new_key(tmp_path / "key"))
    commit(repo, "evil")
    git(repo, "tag", "release-20260101-000000")
    git(repo, "tag", "-a", "-m", "x", "release-20260101-000001")
    assert trusted(repo, allowed) is None


def test_a_forged_newer_tag_does_not_hide_the_real_one(repo: Path, tmp_path: Path) -> None:
    key = new_key(tmp_path / "key")
    allowed = signers(tmp_path / "signers", key)
    wanted = commit(repo, "good")
    sign(repo, "release-20260101-000000", key)
    commit(repo, "evil")
    sign(repo, "release-20990101-000000", new_key(tmp_path / "attacker"))
    assert trusted(repo, allowed) == wanted


def test_a_signature_does_not_carry_over_to_other_content(repo: Path, tmp_path: Path) -> None:
    key = new_key(tmp_path / "key")
    allowed = signers(tmp_path / "signers", key)
    commit(repo, "good")
    sign(repo, "release-20260101-000000", key)
    evil = commit(repo, "evil")
    body = git(repo, "cat-file", "tag", "release-20260101-000000")
    forged = "\n".join(
        f"object {evil}" if line.startswith("object ") else line for line in body.split("\n")
    )
    write = ["git", "-C", str(repo), "hash-object", "-t", "tag", "-w", "--stdin"]
    made = subprocess.run(write, input=forged + "\n", env=ENV, check=True,
                          capture_output=True, text=True)  # fmt: skip
    git(repo, "update-ref", "refs/tags/release-20260101-000000", made.stdout.strip())
    assert trusted(repo, allowed) is None


def test_no_signers_means_no_release(repo: Path, tmp_path: Path) -> None:
    key = new_key(tmp_path / "key")
    commit(repo, "good")
    sign(repo, "release-20260101-000000", key)
    assert trusted(repo, tmp_path / "missing") is None
    (tmp_path / "empty").write_text("")
    assert trusted(repo, tmp_path / "empty") is None


def test_release_script_signs_what_the_check_accepts(repo: Path, tmp_path: Path) -> None:
    (repo / "scripts").mkdir()
    (repo / "deploy/update").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/release.sh", repo / "scripts/release.sh")
    shutil.copy(CHECK, repo / "deploy/update/pi-guard-release-check")
    key = new_key(tmp_path / "key")
    allowed = signers(repo / "deploy/update/release-signers", key)
    commit(repo, "release me")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "tools")
    wanted = git(repo, "rev-parse", "HEAD")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, env=ENV)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")

    env = {**ENV, "PI_GUARD_RELEASE_KEY": str(key)}
    run = subprocess.run([str(repo / "scripts/release.sh"), "tag"], env=env, capture_output=True,
                         text=True)  # fmt: skip
    assert run.returncode == 0, run.stderr
    assert "release-" in git(origin, "tag", "--list")

    clone = tmp_path / "pi"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, env=ENV)
    assert trusted(clone, allowed) == wanted

    commit(repo, "changed, not pushed")
    (repo / "file").write_text("dirty")
    run = subprocess.run([str(repo / "scripts/release.sh"), "tag"], env=env, capture_output=True,
                         text=True)  # fmt: skip
    assert run.returncode != 0 and "uncommitted" in run.stderr


def test_update_trusts_no_key_from_the_repository() -> None:
    update = (ROOT / "scripts/update.sh").read_text()
    assert 'SIGNERS="$PREFIX/release-signers"' in update
    assert "origin/main" not in update  # the branch is what an attacker could move
    assert "deploy/update/release-signers" not in update

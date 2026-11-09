"""Logging in with an OpenPGP signature, checked by GnuPG itself."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pi_guard.web.pgp import PgpError, fingerprint_of, verify

from .test_web import PASSWORD, _client, _login

pytestmark = pytest.mark.skipif(shutil.which("gpg") is None, reason="needs gpg")


def gpg(home: Path, *args: str, data: str = "") -> str:
    return subprocess.run(["gpg", "--batch", "--homedir", str(home), "--pinentry-mode", "loopback",
                           "--passphrase", "", *args], input=data.encode(), capture_output=True,
                          check=True).stdout.decode()  # fmt: skip


@pytest.fixture
def keyring(tmp_path: Path) -> tuple[Path, str]:
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    gpg(home, "--quick-gen-key", "Owner <owner@example.org>", "ed25519", "sign", "1d")
    return home, gpg(home, "--armor", "--export", "owner@example.org")


def sign(home: Path, text: str) -> str:
    return gpg(home, "--clearsign", "--local-user", "owner@example.org", data=text)


def test_a_key_is_read_and_a_signature_checked(keyring: tuple[Path, str]) -> None:
    home, public = keyring
    fingerprint, name = fingerprint_of(public)
    assert len(fingerprint) == 40 and name == "Owner <owner@example.org>"
    assert verify([public], sign(home, "hello"), "hello") == fingerprint
    assert verify([public], sign(home, "hello"), "another text") is None  # an old login
    with pytest.raises(PgpError):
        fingerprint_of("-----BEGIN PGP PRIVATE KEY BLOCK-----")


async def test_login_with_a_signature(tmp_path: Path, keyring: tuple[Path, str]) -> None:
    home, public = keyring
    client, app = await _client(tmp_path)
    try:
        app.dashboard.guard.login_allowed = lambda ip: True  # many login steps in one minute
        headers = {"X-CSRF-Token": await _login(client)}
        wrong = await client.post("/api/account/pgp", json={"key": public, "password": "no"},
                                  headers=headers)  # fmt: skip
        assert wrong.status == 403
        added = await client.post("/api/account/pgp", json={"key": public, "password": PASSWORD},
                                  headers=headers)  # fmt: skip
        assert added.status == 200
        await client.post("/api/logout", headers=headers)
        client.session.cookie_jar.clear()
        assert "pgp" in (await (await client.get("/api/session")).json())["methods"]
        start = await (await client.post("/api/login/pgp/begin", json={})).json()
        stranger = tmp_path / "stranger"
        stranger.mkdir(mode=0o700)
        gpg(stranger, "--quick-gen-key", "Owner <owner@example.org>", "ed25519", "sign", "1d")
        forged = gpg(stranger, "--clearsign", data=start["text"])
        refused = await client.post("/api/login/pgp/finish", json={"flow": start["flow"],
                                                                    "signed": forged})  # fmt: skip
        assert refused.status == 401  # same name, other key
        start = await (await client.post("/api/login/pgp/begin", json={})).json()
        signed = sign(home, start["text"])
        done = await client.post(
            "/api/login/pgp/finish", json={"flow": start["flow"], "signed": signed}
        )
        assert done.status == 200 and (await client.get("/api/overview")).status == 200
        replay = await client.post(
            "/api/login/pgp/finish", json={"flow": start["flow"], "signed": signed}
        )
        assert replay.status == 410
    finally:
        await client.close()
        app.db.close()

"""Releases from GitHub reach devices only with a valid signature and a matching file."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pi_guard.core import app_updates
from pi_guard.core.app_updates import AppUpdates, BadRelease, message, parse

from .helpers import make_app

KEY = Ed25519PrivateKey.generate()
PUBLIC = KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def manifest(product: str, platform: str, version: str, data: bytes, file: str,
             key: Ed25519PrivateKey = KEY) -> bytes:  # fmt: skip
    sha = hashlib.sha256(data).hexdigest()
    signature = key.sign(message(product, platform, version, sha)).hex()
    return json.dumps({"version": version, "sha256": sha, "size": len(data),
                       "signature": signature, "file": file}).encode()  # fmt: skip


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(app_updates, "UPDATE_KEY", PUBLIC)
    application = make_app(tmp_path)
    yield application
    application.db.close()


def github(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes], tag: str = "apps-1.4.0"
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases"):
            assets = [{"name": n, "browser_download_url": f"https://dl.example/{n}"} for n in files]
            return httpx.Response(200, json=[
                {"tag_name": "release-2026-11-09", "assets": []},  # the Pi's own releases
                {"tag_name": tag, "draft": False, "prerelease": False, "assets": assets},
            ])  # fmt: skip
        return httpx.Response(200, content=files[request.url.path.lstrip("/")])

    real = httpx.AsyncClient
    monkeypatch.setattr(app_updates.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))  # fmt: skip


async def test_a_signed_release_reaches_devices(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    agent, apk = b"\x7fELF agent", b"PK apk"
    github(monkeypatch, {
        "agent-linux-x86_64.json": manifest("agent", "linux-x86_64", "1.4.0", agent,
                                            "pi-guard-agent"),
        "pi-guard-agent": agent,
        "android-apk.json": manifest("android", "apk", "1.4.0+812", apk, "PiGuard-1.4.0.apk"),
        "PiGuard-1.4.0.apk": apk,
    })  # fmt: skip
    updated = await AppUpdates(app).check()
    assert sorted(updated) == ["agent-linux-x86_64", "android-apk"]
    apps = app.settings.data_dir / "apps"
    assert (apps / "agent/linux-x86_64/latest.bin").read_bytes() == agent
    meta = json.loads((apps / "agent/linux-x86_64/latest.json").read_text())
    assert meta["version"] == "1.4.0" and len(meta["signature"]) == 128  # devices check it again
    phone = json.loads((apps / "android/latest.json").read_text())
    assert (phone["version_code"], phone["version_name"]) == (812, "1.4.0")
    assert (apps / "android/latest.apk").read_bytes() == apk
    assert await AppUpdates(app).check() == []  # nothing new the second time


async def test_a_wrong_key_or_file_is_refused(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    other = Ed25519PrivateKey.generate()
    good = b"desktop"
    github(monkeypatch, {
        "agent-linux-x86_64.json": manifest("agent", "linux-x86_64", "9.0.0", b"evil", "evil",
                                            other),
        "evil": b"evil",
        "desktop-linux-x64.json": manifest("desktop", "linux-x64", "2.0.0", good, "desk.tar.gz"),
        "desk.tar.gz": b"swapped file",
    })  # fmt: skip
    updates = AppUpdates(app)
    assert await updates.check() == []
    assert updates.last["agent-linux-x86_64"].startswith("refused")
    assert updates.last["desktop-linux-x64"].startswith("refused")
    assert not (app.settings.data_dir / "apps").exists() or not list(
        (app.settings.data_dir / "apps").rglob("latest.*")
    )


async def test_an_older_version_is_not_taken(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    target = app.settings.data_dir / "apps/android"
    target.mkdir(parents=True)
    (target / "latest.json").write_text(json.dumps({"version_code": 900, "version_name": "1.5.0"}))
    apk = b"PK old"
    github(monkeypatch, {"android-apk.json": manifest("android", "apk", "1.4.0+812", apk, "a.apk"),
                         "a.apk": apk})  # fmt: skip
    assert await AppUpdates(app).check() == []
    assert not (target / "latest.apk").exists()


def test_a_manifest_must_be_complete_and_signed() -> None:
    data = b"x"
    good = manifest("agent", "linux-x86_64", "1.0.0", data, "f")
    assert parse("agent-linux-x86_64.json", good, PUBLIC).version == "1.0.0"
    with pytest.raises(BadRelease, match="signature"):
        parse("agent-linux-aarch64.json", good, PUBLIC)  # signed for another platform
    with pytest.raises(BadRelease, match="version"):
        parse("android-apk.json", manifest("android", "apk", "1.0.0", data, "f"), PUBLIC)
    with pytest.raises(BadRelease):
        parse("../etc-passwd.json", good, PUBLIC)
    with pytest.raises(BadRelease, match="file name"):
        parse("agent-linux-x86_64.json", manifest("agent", "linux-x86_64", "1.0.0", data, "../x"),
              PUBLIC)  # fmt: skip


def test_the_built_in_key_is_the_devices_key() -> None:
    import base64

    root = Path(__file__).parents[1]
    assert app_updates.UPDATE_KEY.hex() in (root / "agent/src/update.rs").read_text()
    desktop = base64.b64encode(app_updates.UPDATE_KEY).decode()
    assert desktop in (root / "desktop/updates.js").read_text()


def test_the_release_script_signs_what_the_pi_accepts(tmp_path: Path) -> None:
    import shutil
    import subprocess

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    if shutil.which("openssl") is None:
        pytest.skip("needs openssl")
    root = Path(__file__).parents[1]
    key = tmp_path / "update.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True)
    private = load_pem_private_key(key.read_bytes(), password=None)
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)  # type: ignore[union-attr]
    apk = tmp_path / "PiGuard-1.4.0.apk"
    apk.write_bytes(b"PK phone app")
    out = tmp_path / "out"
    env = {"PATH": "/usr/bin:/bin", "PI_GUARD_UPDATE_KEY": str(key)}
    script = str(root / "scripts/sign-update.sh")
    subprocess.run([script, "android", "apk", "1.4.0+812", str(apk), str(out)], check=True, env=env,
                   capture_output=True)  # fmt: skip
    signed = parse("android-apk.json", (out / "android-apk.json").read_bytes(), public)
    assert (signed.code, signed.name, signed.file) == (812, "1.4.0", apk.name)
    with pytest.raises(BadRelease):
        parse("android-apk.json", (out / "android-apk.json").read_bytes(), PUBLIC)  # another key
    refused = subprocess.run([script, "android", "apk", "1.4.0", str(apk), str(out)], env=env,
                             capture_output=True)  # fmt: skip
    assert refused.returncode != 0  # a phone app without its version code is not signed

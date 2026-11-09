"""Backups: encrypted before they leave, complete or refused, and restored without surprises."""

from __future__ import annotations

import sqlite3
import tarfile
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from pi_guard import backup
from pi_guard.backup import BackupError, collect, decrypt, due, encrypt, restore, run_backup
from pi_guard.core.timeutil import utcnow

from .helpers import make_app

PASS = "a long backup passphrase"


@pytest.fixture
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup, "CHUNK", 1000)


def test_a_backup_decrypts_only_whole_and_with_its_passphrase(
    tmp_path: Path, small_chunks: None
) -> None:
    plain = tmp_path / "plain"
    plain.write_bytes(bytes(range(256)) * 20)  # several pieces
    sealed, back = tmp_path / "sealed", tmp_path / "back"
    encrypt(plain, sealed, PASS)
    assert plain.read_bytes()[:100] not in sealed.read_bytes()
    decrypt(sealed, back, PASS)
    assert back.read_bytes() == plain.read_bytes()
    with pytest.raises(BackupError, match="passphrase"):
        decrypt(sealed, back, "another passphrase")
    data = sealed.read_bytes()
    piece = 4 + 1000 + 16
    sealed.write_bytes(data[:-piece])  # the last piece is gone
    with pytest.raises(BackupError):
        decrypt(sealed, back, PASS)
    sealed.write_bytes(data + b"more")
    with pytest.raises(BackupError, match="after the end"):
        decrypt(sealed, back, PASS)
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    encrypt(empty, sealed, PASS)
    decrypt(sealed, back, PASS)
    assert back.read_bytes() == b""


def test_what_goes_into_a_backup(tmp_path: Path) -> None:
    data, config = tmp_path / "data", tmp_path / "config"
    (data / "recordings").mkdir(parents=True)
    (data / "recordings/cam.mp4").write_bytes(b"video")
    (data / "sounds").mkdir()
    (data / "sounds/bell.wav").write_bytes(b"wav")
    with sqlite3.connect(data / "pi-guard.db") as db:
        db.execute("CREATE TABLE t (x)")
        db.execute("INSERT INTO t VALUES (42)")
    (data / "pi-guard.db-wal").write_bytes(b"half written")
    config.mkdir()
    (config / "pi-guard.env").write_text("PI_GUARD_TELEGRAM_BOT_TOKEN=secret")
    staging = tmp_path / "staging"
    count = collect(data, config, staging)
    names = {str(p.relative_to(staging)) for p in staging.rglob("*") if p.is_file()}
    assert {"data/pi-guard.db", "data/sounds/bell.wav", "config/pi-guard.env"} <= names
    assert "data/recordings/cam.mp4" not in names and "data/pi-guard.db-wal" not in names
    with sqlite3.connect(staging / "data/pi-guard.db") as db:
        assert db.execute("SELECT x FROM t").fetchone() == (42,)
    assert count == 3
    collect(data, None, tmp_path / "bulky", bulky=True)
    assert (tmp_path / "bulky/data/recordings/cam.mp4").exists()


def test_restore_unpacks_beside_and_refuses_paths_out(tmp_path: Path) -> None:
    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "x"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as tar:
        tar.add(payload, arcname="../../escaped")
    sealed = tmp_path / "evil.pgbk"
    encrypt(evil, sealed, PASS)
    with pytest.raises(tarfile.TarError):
        restore(sealed, PASS, tmp_path / "into")
    assert not (tmp_path / "escaped").exists()


def test_the_schedule(tmp_path: Path) -> None:
    app = make_app(tmp_path, backup_enabled=True, backup_hour=0)
    s = app.settings
    now = utcnow()
    assert due(s, {}, now)
    assert not due(s, {"time": (now - timedelta(hours=2)).isoformat(), "ok": True}, now)
    assert due(s, {"time": (now - timedelta(hours=4)).isoformat(), "ok": False}, now)  # retried
    assert due(s, {"time": (now - timedelta(days=1)).isoformat(), "ok": True}, now)
    off = make_app(tmp_path / "off").settings
    assert not due(off, {}, now)
    app.db.close()


def test_a_backup_goes_to_nextcloud_encrypted_and_old_ones_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = make_app(tmp_path, backup_target="nextcloud", backup_passphrase=PASS, backup_keep=2,
                   backup_nextcloud_url="https://cloud.example.org", backup_nextcloud_user="lena",
                   backup_nextcloud_password="app-password")  # fmt: skip
    seen: list[tuple[str, str]] = []
    uploaded: dict[str, bytes] = {}

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "PUT":
            uploaded[request.url.path] = request.read()
            return httpx.Response(201)
        if request.method == "PROPFIND":
            older = [f"pi-guard-backup-2026010{day}T000000Z.pgbk" for day in (1, 2)]
            names = [*older, *[p.rsplit("/", 1)[1] for p in uploaded], "other.txt"]
            body = "".join(
                f"<d:href>/remote.php/dav/files/lena/pi-guard/{n}</d:href>" for n in names
            )
            return httpx.Response(207, text=body)
        return httpx.Response(201 if request.method == "MKCOL" else 204)

    real = httpx.Client
    monkeypatch.setattr(
        backup.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    result = run_backup(app.settings, None)
    assert result["name"].startswith("pi-guard-backup-") and result["files"] >= 1
    path, body = next(iter(uploaded.items()))
    assert path.startswith("/remote.php/dav/files/lena/pi-guard/") and body.startswith(b"PGBK1")
    deleted = [p for m, p in seen if m == "DELETE"]
    assert deleted == ["/remote.php/dav/files/lena/pi-guard/pi-guard-backup-20260101T000000Z.pgbk"]
    app.db.close()


def test_no_backup_without_a_passphrase(tmp_path: Path) -> None:
    app = make_app(tmp_path, backup_target="nextcloud")
    with pytest.raises(BackupError, match="passphrase"):
        run_backup(app.settings, None)
    app.db.close()


def test_backup_targets_are_checked() -> None:
    from pydantic import ValidationError

    from pi_guard.config.settings import Settings

    assert Settings(backup_rsync_target="pi@nas.local:backups/pg").backup_rsync_target
    for bad in ("nas:backups", "pi@nas.local:/x; rm -rf /", "-oProxyCommand=x@h:y"):
        with pytest.raises(ValidationError):
            Settings(backup_rsync_target=bad)
    with pytest.raises(ValidationError):
        Settings(backup_nextcloud_url="http://cloud.example.org")
    assert Settings(backup_borg_repo="ssh://pi@nas.local/srv/borg").backup_borg_repo


async def test_backing_up_from_the_dashboard_needs_the_password(tmp_path: Path) -> None:
    from .test_web import PASSWORD, _client, _login

    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        status = await (await client.get("/api/backup")).json()
        assert status["passphrase"] is False and status["last"] == {}
        refused = await client.post("/api/backup/run", json={"password": "no"}, headers=headers)
        assert refused.status == 403
        started = await client.post("/api/backup/run", json={"password": PASSWORD}, headers=headers)
        assert started.status == 200
        await app.backups.task
        assert app.backups.last()["ok"] is False  # no passphrase set: refused, and said so
    finally:
        await client.close()
        app.db.close()

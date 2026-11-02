from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from aiohttp.test_utils import TestClient, TestServer

from pi_guard.cameras.live import AudioFanout, LiveFrames, read_mpjpeg
from pi_guard.config import OVERRIDE_ENV, load_config
from pi_guard.web import auth
from pi_guard.web.auth import AuthStore
from pi_guard.web.server import Dashboard

from .helpers import TOKEN, make_app

PASSWORD = "correct horse battery"


# --- auth primitives -------------------------------------------------------------------


def test_password_hash_roundtrip() -> None:
    stored = auth.hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert auth.verify_password(PASSWORD, stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password(PASSWORD, "garbage")


def test_totp_rfc6238_vector() -> None:
    # RFC 6238 appendix B, SHA-1, T = 59 s -> 94287082 (last six digits: 287082)
    secret = __import__("base64").b32encode(b"12345678901234567890").decode()
    assert auth.totp_code(secret, 59 // 30) == "287082"
    assert auth.totp_match(secret, "287 082", now=59) == 1
    assert auth.totp_match(secret, "000000", now=59) is None


def test_keyfiles_and_sessions(db: Any) -> None:
    store = AuthStore(db)
    secret = store.create_keyfile("laptop")
    assert store.check_keyfile(secret + "\n") == "laptop"
    assert store.check_keyfile("pi-guard-key-v1:forged") is None
    token = store.create_session("admin", "1.2.3.4", "test")
    session = store.session(token)
    assert session is not None and session.username == "admin" and not session.pending
    assert db.execute("SELECT id_hash FROM web_sessions").fetchone()[0] != token  # only hashes
    store.end(token)
    assert store.session(token) is None
    assert store.delete_keyfile("laptop") and store.check_keyfile(secret) is None


def test_totp_codes_work_once(db: Any) -> None:
    store = AuthStore(db)
    assert store.totp_step_unused("admin", 100)
    assert not store.totp_step_unused("admin", 100)
    assert store.totp_step_unused("admin", 101)


# --- server ------------------------------------------------------------------------------


async def _client(tmp_path: Path, **settings: Any) -> tuple[TestClient, Any]:
    values = {
        "dashboard_enabled": True,
        "dashboard_tls": False,
        "dashboard_password_hash": auth.hash_password(PASSWORD),
    } | settings
    app = make_app(tmp_path, **values)
    dashboard = Dashboard(app)
    app.dashboard = dashboard
    client = TestClient(TestServer(dashboard.web))
    await client.start_server()
    return client, app


async def _login(client: TestClient) -> str:
    response = await client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert response.status == 200
    return (await (await client.get("/api/session")).json())["csrf"]


@pytest.fixture
async def web(tmp_path: Path):  # type: ignore[no-untyped-def]
    client, app = await _client(tmp_path)
    yield client, app
    await client.close()
    app.db.close()


async def test_api_requires_login(web) -> None:  # type: ignore[no-untyped-def]
    client, _ = web
    assert (await client.get("/api/overview")).status == 401
    assert (await client.get("/api/i18n/de")).status == 200
    assert (
        await client.post("/api/login", json={"username": "admin", "password": "no"})
    ).status == 401
    await _login(client)
    overview = await (await client.get("/api/overview")).json()
    assert overview["credit"] == "made by lytex" and overview["cameras"][0]["id"] == "living_room"


async def test_csrf_and_origin_checks(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    csrf = await _login(client)
    assert (await client.post("/api/mode", json={"mode": "AWAY"})).status == 403
    evil = {"X-CSRF-Token": csrf, "Origin": "https://evil.example"}
    assert (await client.post("/api/mode", json={"mode": "AWAY"}, headers=evil)).status == 403
    ok = await client.post("/api/mode", json={"mode": "AWAY"}, headers={"X-CSRF-Token": csrf})
    assert ok.status == 200 and app.modes.effective.value == "AWAY"


async def test_security_headers_and_robots(web) -> None:  # type: ignore[no-untyped-def]
    client, _ = web
    response = await client.get("/")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "noindex" in response.headers["X-Robots-Tag"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Disallow: /" in await (await client.get("/robots.txt")).text()
    cookie = await client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    set_cookie = cookie.headers["Set-Cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie


async def test_honeypots_ban_the_address(web, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pi_guard.web import security

    monkeypatch.setattr(security, "TARPIT_SECONDS", 0)
    client, _ = web
    assert (await client.get("/wp-login.php")).status == 404
    assert (await client.get("/")).status == 403


async def test_login_form_honeypot(web) -> None:  # type: ignore[no-untyped-def]
    client, _ = web
    response = await client.post(
        "/api/login", json={"username": "admin", "password": PASSWORD, "website": "http://spam"}
    )
    assert response.status == 401
    assert (await client.get("/api/session")).status == 403


async def test_repeated_failures_ban(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    app.dashboard.guard._login_limiter.burst = 100
    app.dashboard.guard._login_limiter.per_minute = 1000
    for _ in range(8):
        await client.post("/api/login", json={"username": "admin", "password": "nope"})
    assert (await client.get("/api/session")).status == 403


async def test_totp_second_step(tmp_path: Path) -> None:
    secret = auth.new_totp_secret()
    client, app = await _client(tmp_path, dashboard_totp_secret=secret)
    first = await client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert (await first.json())["totp_required"]
    assert (await client.get("/api/overview")).status == 401  # half logged in
    import time

    code = auth.totp_code(secret, int(time.time() // 30))
    assert (await client.post("/api/login/totp", json={"code": "000000"})).status == 401
    assert (await client.post("/api/login/totp", json={"code": code})).status == 200
    assert (await client.get("/api/overview")).status == 200
    await client.close()
    app.db.close()


async def test_keyfile_login(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, dashboard_auth="keyfile")
    secret = AuthStore(app.db).create_keyfile("tablet")
    assert (
        await client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    ).status == 401
    assert (await client.post("/api/login", json={"keyfile": secret})).status == 200
    assert (await client.get("/api/overview")).status == 200
    await client.close()
    app.db.close()


async def test_settings_are_validated_and_written(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    csrf = await _login(client)
    headers = {"X-CSRF-Token": csrf}
    data = await (await client.get("/api/settings")).json()
    assert data["secrets"]["telegram_bot_token"] is True and TOKEN not in json.dumps(data)
    bad = await client.put("/api/settings", json={"telegram_bot_token": "x"}, headers=headers)
    assert bad.status == 400
    bad = await client.put("/api/settings", json={"motion_sensitivity": 500}, headers=headers)
    assert bad.status == 400
    ok = await client.put(
        "/api/settings", json={"motion_sensitivity": 70, "language": "de"}, headers=headers
    )
    assert ok.status == 200
    written = (app.settings.data_dir / OVERRIDE_ENV).read_text()
    assert "PI_GUARD_MOTION_SENSITIVITY=70" in written


def test_overrides_take_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    data = tmp_path / "data"
    data.mkdir()
    env.write_text(
        f"PI_GUARD_TELEGRAM_ENABLED=false\nPI_GUARD_DATA_DIR={data}\n"
        f"PI_GUARD_CONFIG_FILE={tmp_path / 'none.yaml'}\nPI_GUARD_MOTION_SENSITIVITY=10\n"
    )
    (data / OVERRIDE_ENV).write_text("PI_GUARD_MOTION_SENSITIVITY=80\n")
    monkeypatch.setenv("PI_GUARD_MOTION_SENSITIVITY", "20")
    assert load_config(env).settings.motion_sensitivity == 80


async def test_config_validation(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    csrf = await _login(client)
    config = await (await client.get("/api/config")).json()
    config["cameras"][0]["resolution"] = "huge"
    bad = await client.put("/api/config", json=config, headers={"X-CSRF-Token": csrf})
    assert bad.status == 400 and "resolution" in await bad.text()
    config["cameras"][0]["resolution"] = "640x480"
    assert (
        await client.put("/api/config", json=config, headers={"X-CSRF-Token": csrf})
    ).status == 200
    assert (app.settings.data_dir / "dashboard.yaml").exists()


async def test_media_paths_cannot_escape(web) -> None:  # type: ignore[no-untyped-def]
    client, _ = web
    await _login(client)
    for path in (
        "/api/media/snapshots/..%2F..%2Fpi-guard.db",
        "/api/media/../pi-guard.db",
        "/api/media/tls/ca.key",
    ):
        assert (await client.get(path)).status in (403, 404)


async def test_photo_and_image_tools(web) -> None:  # type: ignore[no-untyped-def]
    cv2 = pytest.importorskip("cv2")
    client, app = web
    csrf = await _login(client)
    image = np.zeros((360, 640, 3), dtype=np.uint8)
    image[:, 320:] = 200
    path = app.cameras["living_room"].snapshot_path
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), image)
    headers = {"X-CSRF-Token": csrf}
    shot = await (await client.post("/api/live/living_room/snapshot", headers=headers)).json()
    assert (await client.get(shot["url"])).status == 200
    zoomed = await client.post(
        "/api/image/zoom", json={"source": shot["name"], "region": "rechts"}, headers=headers
    )
    assert zoomed.status == 200
    traversal = await client.post(
        "/api/image/brighter", json={"source": "../pi-guard.db"}, headers=headers
    )
    assert traversal.status == 404


async def test_people_require_consent(web) -> None:  # type: ignore[no-untyped-def]
    from aiohttp import FormData

    from pi_guard.detection.faces import BiometricStore

    from .test_enrollment import FakeRecognizer

    client, app = web
    csrf = await _login(client)
    store = BiometricStore(app.settings.data_dir / "bio.db")

    class Analysis:
        faces = FakeRecognizer(store)
        persons = None

    app.analysis = Analysis()  # type: ignore[assignment]
    form = FormData()
    form.add_field("files", b"jpeg", filename="a.jpg")
    no = await client.post("/api/people/Erna/samples", data=form, headers={"X-CSRF-Token": csrf})
    assert no.status == 400 and store.people() == []
    form = FormData()
    form.add_field("consent", "yes")
    form.add_field("files", b"jpeg", filename="a.jpg")
    form.add_field("files", b"jpeg", filename="b.jpg")
    ok = await client.post("/api/people/Erna/samples", data=form, headers={"X-CSRF-Token": csrf})
    assert (await ok.json())["stored"] == 2
    assert (
        await client.patch(
            "/api/people/Erna", json={"name": "Oma Erna"}, headers={"X-CSRF-Token": csrf}
        )
    ).status == 200
    assert store.people()[0][0] == "Oma Erna"
    assert (
        await client.delete("/api/people/Oma%20Erna", headers={"X-CSRF-Token": csrf})
    ).status == 200
    store.close()


def test_unauthenticated_dashboard_needs_explicit_opt_in() -> None:
    from pydantic import ValidationError

    from pi_guard.config.settings import Settings

    with pytest.raises(ValidationError, match="ALLOW_UNAUTHENTICATED"):
        Settings(telegram_enabled=False, dashboard_enabled=True, dashboard_auth="none")
    Settings(
        telegram_enabled=False,
        dashboard_enabled=True,
        dashboard_auth="none",
        dashboard_host="127.0.0.1",
    )
    with pytest.raises(ValidationError, match="PASSWORD"):
        Settings(telegram_enabled=False, dashboard_enabled=True)


# --- live streams ------------------------------------------------------------------------


async def test_mpjpeg_parser_reads_exact_frames() -> None:
    reader = asyncio.StreamReader()
    frames = LiveFrames()
    one, two = b"\xff\xd8one\xff\xd9", b"\xff\xd8second\r\n--frame\xff\xd9"
    for frame in (one, two):
        reader.feed_data(
            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(frame)).encode()
            + b"\r\n\r\n"
            + frame
            + b"\r\n"
        )
    reader.feed_eof()
    await read_mpjpeg(reader, frames)
    # A viewer that was busy gets the newest frame, not a backlog.
    number, latest = await frames.next(0)
    assert (number, latest) == (2, two)
    assert frames.number == 2 and frames.frame == two  # boundary text inside a frame is fine


def test_audio_fanout_drops_old_chunks_for_slow_listeners() -> None:
    fanout = AudioFanout()
    queue = fanout.subscribe()
    for i in range(100):
        fanout.publish(bytes([i]))
    assert queue.qsize() == 64 and queue.get_nowait() == bytes([36])
    fanout.unsubscribe(queue)
    assert fanout.listeners == 0


async def test_live_video_stream(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    await _login(client)
    pipeline = app.cameras["living_room"]
    pipeline.live_fps = 5

    async def feed() -> None:
        for i in range(3):
            await asyncio.sleep(0.05)
            await pipeline.live_frames.publish(b"\xff\xd8frame%d\xff\xd9" % i)

    task = asyncio.create_task(feed())
    response = await client.get("/api/live/living_room/video")
    assert response.headers["Content-Type"].startswith("multipart/x-mixed-replace")
    body = b""
    while body.count(b"Content-Type: image/jpeg") < 2:
        body += await response.content.readany()
    response.close()
    await task
    assert b"frame0" in body or b"frame1" in body


@pytest.mark.skipif(__import__("shutil").which("openssl") is None, reason="openssl missing")
def test_certificates_cover_extra_names_and_renew(tmp_path: Path) -> None:
    from pi_guard.web import tls

    ca, cert, key = tls.ensure_certificates(tmp_path, ["192.0.2.10", "pi.example"])
    sans = tls._san(cert)
    assert {"192.0.2.10", "pi.example", "localhost", "127.0.0.1"} <= sans
    assert key.stat().st_mode & 0o077 == 0 and (tmp_path / "ca.key").stat().st_mode & 0o077 == 0
    first = cert.read_bytes()
    tls.ensure_certificates(tmp_path, ["192.0.2.10", "pi.example"])
    assert cert.read_bytes() == first  # nothing changed, nothing reissued
    tls.ensure_certificates(tmp_path, ["192.0.2.11"])
    assert "192.0.2.11" in tls._san(cert) and ca.exists()

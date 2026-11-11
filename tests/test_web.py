from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from pi_guard.cameras.live import AudioFanout, LiveFrames, read_mpjpeg
from pi_guard.config import OVERRIDE_ENV, load_config
from pi_guard.core.events import EventType
from pi_guard.web import auth, server
from pi_guard.web.auth import AuthStore
from pi_guard.web.server import Dashboard
from pi_guard.web.settings_schema import EXPERT

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
    fresh = settings.pop("fresh", False)
    values = {"dashboard_enabled": True, "dashboard_tls": False} | settings
    app = make_app(tmp_path, **values)
    dashboard = Dashboard(app)
    app.dashboard = dashboard
    if not fresh:  # skip the first-login password change in most tests
        dashboard.auth.change_credentials(auth.DEFAULT_PASSWORD, "admin", PASSWORD)
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


async def test_packed_files_and_texts_travel_compressed(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    static = tmp_path / "static"
    static.mkdir()
    (static / "app.js").write_text("plain();")
    (static / "app.js.gz").write_bytes(gzip.compress(b"plain();"))
    monkeypatch.setattr(server, "STATIC", static)
    client, app = await _client(tmp_path)
    try:
        packed = await client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
        assert packed.headers["Content-Encoding"] == "gzip"
        assert packed.headers["Content-Type"].endswith("javascript")
        assert "Accept-Encoding" in packed.headers["Vary"]
        assert await packed.text() == "plain();"
        plain = await client.get("/static/app.js", headers={"Accept-Encoding": "identity"})
        assert "Content-Encoding" not in plain.headers
        texts = await client.get("/api/i18n/de", headers={"Accept-Encoding": "gzip"})
        assert texts.headers["Content-Encoding"] == "gzip"
        assert "Accept-Encoding" in texts.headers["Vary"]
        assert "web.loading" in await texts.json()
    finally:
        await client.close()
        app.db.close()


async def test_the_csrf_token_is_never_compressed(web) -> None:  # type: ignore[no-untyped-def]
    client, _ = web
    await _login(client)
    response = await client.get("/api/session", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in response.headers


async def test_slow_requests_are_logged_without_their_address(web, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    client, _ = web
    await _login(client)
    monkeypatch.setattr(server, "SLOW_REQUEST", 0)
    with caplog.at_level("INFO", logger="pi_guard.web.server"):
        await client.get("/api/events?camera=secret")
        await client.get("/api/media/snapshots/private.jpg")
    assert "slow request: GET /api/events took" in caplog.text
    assert "secret" not in caplog.text
    assert "private.jpg" not in caplog.text


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
    import time

    client, app = await _client(tmp_path)
    secret = app.dashboard.auth.begin_totp()
    assert app.dashboard.auth.confirm_totp(auth.totp_code(secret, int(time.time() // 30)))
    client.session.cookie_jar.clear()
    first = await client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert (await first.json())["totp_required"]
    assert (await client.get("/api/overview")).status == 401  # half logged in
    code = auth.totp_code(secret, int(time.time() // 30) + 1)  # the current step was used once
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
    assert data["values"]["telegram_bot_token"] == "" and TOKEN not in json.dumps(data)
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


async def test_a_face_in_an_event_picture_is_learned(web) -> None:  # type: ignore[no-untyped-def]
    from pi_guard.core.timeutil import utcnow
    from pi_guard.detection.faces import EnrollmentError, Identification, Verdict
    from pi_guard.detection.persons import PersonResult

    client, app = web
    csrf = await _login(client)
    face = np.array([[100, 50, 60, 60, *([0] * 10), 0.9]], np.float32)
    calls: list[tuple[str, str]] = []

    class Faces:
        store = SimpleNamespace(people=lambda: [("Lena", 10, "")], exists=lambda n: "Lena")

        def identify(self, image: Any, face: Any) -> Identification:
            return Identification(Verdict.KNOWN, "Lena", 0.6)

        def learn_problem(self, image: Any, face: Any) -> tuple[str | None, Any]:
            return None, image

        def learn(self, name: str, image: Any, face: Any, actor: str) -> int:
            if name == "Ben":
                raise EnrollmentError("faces.learn_not_enrolled")
            calls.append(("learn", name))
            return 11, 0.33

        def reject(self, name: str, image: Any, face: Any) -> float:
            calls.append(("reject", name))
            return 0.52

        def quality(self, name: str) -> dict[str, Any]:
            return {"score": 50}

    class Persons:
        def detect(self, path: Path) -> PersonResult:
            return PersonResult(1, face, np.zeros((720, 1280, 3), np.uint8))

    app.analysis = SimpleNamespace(faces=Faces(), persons=Persons())
    shots = app.media_dir / "snapshots"
    shots.mkdir(parents=True, exist_ok=True)
    (shots / "person_1.jpg").write_bytes(b"jpeg")
    app.db.add_event(utcnow(), "known_person", "living_room", {"snapshot": "person_1.jpg"})
    app.db.add_event(utcnow(), "known_person", "living_room", {"snapshot": "../pi-guard.db"})
    app.db.add_event(utcnow(), "motion_detected", "living_room", {"snapshot": "person_1.jpg"})
    headers = {"X-CSRF-Token": csrf}

    found = await (await client.get("/api/events/1/faces")).json()
    assert found["faces"] == [{"box": [100, 50, 60, 60], "verdict": "known", "name": "Lena",
                               "score": 0.6, "learn_problem": None}]  # fmt: skip
    assert (found["width"], found["height"], found["people"]) == (1280, 720, ["Lena"])
    assert (await client.get("/api/events/2/faces")).status == 410  # no way out of the folder
    assert (await client.get("/api/events/3/faces")).status == 404  # not a person event

    learned = await client.post("/api/events/1/faces/0", json={"name": "Lena", "is": True},
                                headers=headers)  # fmt: skip
    assert (await learned.json())["total"] == 11
    no = await client.post("/api/events/1/faces/0", json={"name": "Lena", "is": False},
                           headers=headers)  # fmt: skip
    assert no.status == 200 and calls == [("learn", "Lena"), ("reject", "Lena")]
    refused = await client.post("/api/events/1/faces/0", json={"name": "Ben", "is": True},
                                headers=headers)  # fmt: skip
    assert refused.status == 422 and await refused.text() == "faces.learn_not_enrolled"
    missing = await client.post("/api/events/1/faces/5", json={"name": "Lena", "is": True},
                                headers=headers)  # fmt: skip
    assert missing.status == 404
    bad = await client.post("/api/events/1/faces/0", json={"name": "Lena"}, headers=headers)
    assert bad.status == 400
    actions = [r[0] for r in app.db.execute("SELECT action FROM audit WHERE action LIKE 'faces.%'")]
    assert actions == ["faces.learn", "faces.reject"]
    corrections = app.db.get("contribution.corrections")
    assert corrections["is"][6] == 1 and corrections["not"][10] == 1  # bins of 0.05


async def test_statistics_are_shown_before_they_are_switched_on(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    csrf = await _login(client)
    status = await (await client.get("/api/contribution")).json()
    assert status["enabled"] is False and status["url"] is None and status["report"]["report"] == 2
    on = await client.put("/api/contribution", json={"enabled": True},
                          headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert on.status == 200 and app.db.get("contribution.enabled") is True
    bad = await client.put("/api/contribution", json={"enabled": "yes"},
                           headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert bad.status == 400


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
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(telegram_enabled=False, dashboard_enabled=True, dashboard_secret_key="short")
    # Telegram can be set up later in the dashboard.
    assert not Settings(dashboard_enabled=True).telegram_ready


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


# --- first login, account, setup ----------------------------------------------------------


async def test_default_login_must_be_changed_first(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, fresh=True)
    login = await client.post("/api/login", json={"username": "admin", "password": "admin123"})
    assert login.status == 200
    session = await (await client.get("/api/session")).json()
    assert session["setup_required"] and not session["setup_done"]
    headers = {"X-CSRF-Token": session["csrf"]}
    blocked = await client.get("/api/overview")
    assert blocked.status == 403 and await blocked.text() == "setup_required"
    assert (await client.get("/api/account")).status == 200

    async def change(current: str, username: str, password: str) -> tuple[int, str]:
        r = await client.post(
            "/api/account/credentials",
            headers=headers,
            json={"current": current, "username": username, "password": password},
        )
        return r.status, await r.text()

    assert await change("wrong", "oma", PASSWORD) == (400, "web.wrong_password")
    assert await change("admin123", "oma", "short") == (400, "web.password_too_short")
    assert await change("admin123", "oma", "admin123") == (400, "web.password_too_short")
    assert await change("admin123", "oma", "admin123admin") == (
        200,
        '{"ok": true, "username": "oma"}',
    )
    assert (await client.get("/api/overview")).status == 200
    assert not (await (await client.get("/api/session")).json())["setup_required"]
    client.session.cookie_jar.clear()
    old = await client.post("/api/login", json={"username": "admin", "password": "admin123"})
    assert old.status == 401
    new = await client.post("/api/login", json={"username": "Oma", "password": "admin123admin"})
    assert new.status == 200
    await client.close()
    app.db.close()


async def test_totp_setup_through_the_api(web) -> None:  # type: ignore[no-untyped-def]
    import time

    client, app = web
    csrf = await _login(client)
    headers = {"X-CSRF-Token": csrf}
    begin = await (await client.post("/api/account/totp/begin", headers=headers)).json()
    assert begin["qr"].startswith("data:image/svg+xml;base64,") and begin["uri"].startswith(
        "otpauth://"
    )
    bad = await client.post("/api/account/totp/confirm", json={"code": "000000"}, headers=headers)
    assert bad.status == 400
    code = auth.totp_code(begin["secret"], int(time.time() // 30))
    ok = await client.post("/api/account/totp/confirm", json={"code": code}, headers=headers)
    assert ok.status == 200 and (await (await client.get("/api/account")).json())["totp"]
    stored = app.db.execute("SELECT totp FROM web_account").fetchone()[0]
    assert begin["secret"] not in stored  # encrypted at rest
    wrong = await client.post("/api/account/totp/disable", json={"password": "x"}, headers=headers)
    assert wrong.status == 400
    off = await client.post(
        "/api/account/totp/disable", json={"password": PASSWORD}, headers=headers
    )
    assert off.status == 200


def test_secret_key_peppers_passwords_and_protects_totp(db: Any) -> None:
    store = AuthStore(db, b"k" * 40)
    store.ensure_account()
    store.change_credentials("admin123", "admin", PASSWORD)
    secret = store.begin_totp()
    assert store.check_password("admin", PASSWORD) == "admin"
    other = AuthStore(db, b"x" * 40)  # same database, different key
    assert other.check_password("admin", PASSWORD) is None
    assert other._decrypt(db.execute("SELECT totp_pending FROM web_account").fetchone()[0]) is None
    assert secret not in str(db.execute("SELECT * FROM web_account").fetchone()[:])
    other.reset_account()
    assert other.check_password("admin", "admin123") == "admin"
    assert other.account()["must_change"]


async def test_secret_key_is_generated_when_not_configured(web) -> None:  # type: ignore[no-untyped-def]
    _, app = web
    key = app.settings.data_dir / "dashboard.secret"
    assert len(key.read_text().strip()) == 64 and key.stat().st_mode & 0o077 == 0


async def test_bot_token_is_write_only(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    csrf = await _login(client)
    new_token = "987654321:BBH" + "z" * 32
    r = await client.put("/api/settings", json={"telegram_bot_token": new_token,
                                                "telegram_admin_user_ids": ["4242"]},
                         headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert r.status == 200
    data = await (await client.get("/api/settings")).json()
    assert data["values"]["telegram_bot_token"] == "" and new_token not in json.dumps(data)
    written = (app.settings.data_dir / "dashboard.env").read_text()
    assert f"PI_GUARD_TELEGRAM_BOT_TOKEN={new_token}" in written
    keep = await client.put("/api/settings", json={"telegram_bot_token": ""},
                            headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert keep.status == 200 and new_token in (app.settings.data_dir / "dashboard.env").read_text()
    bad = await client.put("/api/settings", json={"telegram_bot_token": "not-a-token"},
                           headers={"X-CSRF-Token": csrf})  # fmt: skip
    assert bad.status == 400 and "not-a-token" not in await bad.text()


async def test_setup_done_and_models_need_confirmation(web) -> None:  # type: ignore[no-untyped-def]
    client, _ = web
    csrf = await _login(client)
    headers = {"X-CSRF-Token": csrf}
    assert not (await (await client.get("/api/session")).json())["setup_done"]
    assert (await client.post("/api/setup/done", headers=headers)).status == 200
    assert (await (await client.get("/api/session")).json())["setup_done"]
    models = await (await client.get("/api/models")).json()
    assert {m["name"] for m in models} == {"yunet", "sface", "yolox"} and all(
        m["size_mb"] for m in models
    )
    no = await client.post("/api/models/download", json={}, headers=headers)
    assert no.status == 400


async def test_setup_status_reports_what_the_installer_set(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, fresh=True, language="de")
    try:
        response = await client.post(
            "/api/login", json={"username": "admin", "password": auth.DEFAULT_PASSWORD}
        )
        assert response.status == 200
        # Readable while the first-login password change is still pending.
        status = await (await client.get("/api/setup/status")).json()
        assert status["language"] == "de"
        assert status["telegram"]["admins"] >= 1
        assert status["devices"]["cameras"]
    finally:
        await client.close()
        app.db.close()


async def test_a_name_used_for_a_home_server_enters_the_certificate(tmp_path: Path) -> None:
    from pi_guard.web.tls import _san, ensure_certificates, server_context

    client, app = await _client(tmp_path)
    d = app.dashboard
    try:
        _, cert, key = ensure_certificates(d.tls_dir, [])
        d._ssl = server_context(cert, key)
        await d.learn_name("lytex.system")
        assert "lytex.system" in _san(cert)
        await d.learn_name("lytex.system")
        assert d.tls_names().count("lytex.system") == 1
        await d.learn_name("evil name\n")
        assert "evil name\n" not in d.tls_names()
    finally:
        await client.close()
        app.db.close()


async def test_every_notification_can_choose_its_ways(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        body = await (await client.get("/api/notifications")).json()
        assert "ways" in body and "signal" in body["rules"][0]["providers"]
        chosen = {"providers": ["signal", "mobile"]}
        answer = await client.put(
            "/api/notifications/motion_detected", json=chosen, headers=headers
        )
        assert answer.status == 200
        rule = app.notifier.rule(EventType.MOTION_DETECTED)
        assert rule.providers == ("signal", "mobile")
        assert (await client.put("/api/notifications/motion_detected", json={"providers": ["x"]},
                                 headers=headers)).status == 400  # fmt: skip
    finally:
        await client.close()
        app.db.close()


async def test_settings_tell_which_ones_are_for_experts(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        await _login(client)
        fields = (await (await client.get("/api/settings")).json())["fields"]
        expert = {f["key"]: f["expert"] for f in fields}
        assert expert["signal_cli_address"] and not expert["language"]
        assert set(EXPERT) <= set(expert)  # a typo here would quietly show a field to everybody
    finally:
        await client.close()
        app.db.close()


async def test_releases_are_handed_out_without_a_login(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        assert await (await client.get("/api/updates/agent/linux-x86_64")).json() == {}
        folder = app.settings.data_dir / "apps" / "agent" / "linux-x86_64"
        folder.mkdir(parents=True)
        (folder / "latest.json").write_text('{"version": "1.0.0"}')
        (folder / "latest.bin").write_bytes(b"binary")
        answer = await client.get("/api/updates/agent/linux-x86_64")
        assert (await answer.json())["version"] == "1.0.0"
        assert await (await client.get("/api/updates/agent/linux-x86_64/file")).read() == b"binary"
        assert (await client.get("/api/updates/passwords/linux-x86_64")).status == 404
        assert (await client.get("/api/updates/agent/..%2F..%2Ftls")).status == 404
        assert (await client.post("/api/updates/agent/linux-x86_64", json={})).status in (403, 405)
    finally:
        await client.close()
        app.db.close()


async def test_a_long_voice_message_arrives_whole(tmp_path: Path) -> None:
    """It used to be cut off after the first part, which came out as nonsense."""
    from aiohttp import web

    from pi_guard.web.server import read_body

    got: list[int] = []

    async def handler(request: web.Request) -> web.Response:
        got.append(len(await read_body(request, 5_000_000)))
        return web.Response()

    application = web.Application(client_max_size=10_000_000)
    application.router.add_post("/", handler)
    client = TestClient(TestServer(application))
    await client.start_server()
    try:

        async def slowly() -> Any:
            for _ in range(20):
                yield b"x" * 100_000
                await asyncio.sleep(0)

        assert (await client.post("/", data=slowly())).status == 200
        assert got == [2_000_000]
        assert (await client.post("/", data=b"x" * 6_000_000)).status == 413
    finally:
        await client.close()


async def test_a_voice_message_reaches_the_assistant_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, app = await _client(tmp_path)
    heard: list[int] = []

    class Assistant:
        async def transcribe(self, path: Path, language: str = "de") -> str:
            heard.append(path.stat().st_size)
            return ""

    app.addons.assistant = Assistant()
    try:
        headers = {"X-CSRF-Token": await _login(client)}

        async def recording() -> Any:
            for _ in range(10):
                yield b"o" * 100_000
                await asyncio.sleep(0)

        await client.post("/api/assistant/chat/voice", data=recording(), headers=headers)
        assert heard == [1_000_000]
    finally:
        app.addons.assistant = None
        await client.close()
        app.db.close()


async def test_a_new_password_ends_all_other_logins(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    csrf = {"X-CSRF-Token": await _login(client)}
    other = TestClient(client.server, cookie_jar=CookieJar(unsafe=True))
    await other.start_server()
    try:
        await _login(other)
        key = app.dashboard.auth.create_session("key:laptop", "127.0.0.1", "test")
        body = {"current": PASSWORD, "username": "admin", "password": "another long password"}
        response = await client.post("/api/account/credentials", json=body, headers=csrf)
        assert response.status == 200
        assert (await client.get("/api/overview")).status == 200  # the one that changed it stays
        assert (await other.get("/api/overview")).status == 401
        assert app.dashboard.auth.session(key) is None
    finally:
        await other.close()


async def test_a_viewers_new_password_leaves_other_people_logged_in(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    await _login(client)
    app.dashboard.auth.add_user("guest", "guest password 1", "viewer")
    viewer = TestClient(client.server, cookie_jar=CookieJar(unsafe=True))
    second = TestClient(client.server, cookie_jar=CookieJar(unsafe=True))
    await viewer.start_server()
    await second.start_server()
    try:
        login = {"username": "guest", "password": "guest password 1"}
        await viewer.post("/api/login", json=login)
        await second.post("/api/login", json=login)
        csrf = (await (await viewer.get("/api/session")).json())["csrf"]
        body = {"current": "guest password 1", "password": "guest password 2"}
        response = await viewer.post("/api/me/password", json=body,
                                     headers={"X-CSRF-Token": csrf})  # fmt: skip
        assert response.status == 200
        assert (await viewer.get("/api/overview")).status == 200
        assert (await second.get("/api/overview")).status == 401
        assert (await client.get("/api/overview")).status == 200
    finally:
        await viewer.close()
        await second.close()


async def test_the_first_password_replaces_the_well_known_one(tmp_path: Path) -> None:
    app = make_app(tmp_path, dashboard_enabled=True, dashboard_tls=False,
                   dashboard_first_password="chosen by the installer")  # fmt: skip
    dashboard = Dashboard(app)
    try:
        assert dashboard.auth.check_password("admin", "chosen by the installer") == "admin"
        assert dashboard.auth.check_password("admin", auth.DEFAULT_PASSWORD) is None
        assert dashboard.auth.account()["must_change"]
    finally:
        app.db.close()


def test_reset_password_shows_a_new_random_password(tmp_path: Path, monkeypatch: Any) -> None:
    from typer.testing import CliRunner

    from pi_guard import cli

    app = make_app(tmp_path, dashboard_enabled=True, dashboard_tls=False)
    settings = app.settings
    app.db.close()
    monkeypatch.setattr(cli, "_config", lambda: SimpleNamespace(settings=settings))
    answer = CliRunner().invoke(cli.app, ["dashboard", "reset-password"], input="y\n")
    assert answer.exit_code == 0, answer.output
    password = answer.output.split("admin / ", 1)[1].split(";", 1)[0]
    assert len(password) >= 10 and password != auth.DEFAULT_PASSWORD


async def test_every_messenger_can_send_a_test(web) -> None:  # type: ignore[no-untyped-def]
    client, app = web
    csrf = await _login(client)
    listed = await (await client.get("/api/messengers")).json()
    assert [m["name"] for m in listed] == ["telegram"] and listed[0]["admins"] >= 1
    sent = await client.post("/api/messengers/telegram/test", headers={"X-CSRF-Token": csrf})
    assert sent.status == 200 and "test" in app.providers["telegram"].last().text.lower()
    off = await client.post("/api/messengers/whatsapp/test", headers={"X-CSRF-Token": csrf})
    assert off.status == 409


async def test_the_live_marks_need_yolox_and_are_cached(web, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from pi_guard.web import api_live

    client, app = web
    await _login(client)
    plain = await (await client.get("/api/live/living_room/overlay")).json()
    assert plain == {"fps": 0.0}  # no detection unless asked for
    calls = []

    class Detector:
        body_detector = "yolox"

        def objects(self, image: Any) -> list[Any]:
            calls.append(1)
            return [((10.0, 20.0, 30.0, 40.0), "dog", 0.8)]

    monkeypatch.setattr(app, "person_locator", lambda: Detector())
    import cv2

    frame = cv2.imencode(".jpg", np.zeros((100, 200, 3), np.uint8))[1].tobytes()
    await app.cameras["living_room"].live_frames.publish(frame)
    api_live._overlay.clear()
    first = await (await client.get("/api/live/living_room/overlay?objects=1")).json()
    assert first["objects"] == [{"box": [0.05, 0.2, 0.15, 0.4], "label": "dog", "score": 0.8}]
    await client.get("/api/live/living_room/overlay?objects=1")
    assert len(calls) == 1  # one check per second, however often asked
    monkeypatch.setattr(app, "person_locator", lambda: None)
    api_live._overlay.clear()
    assert (await client.get("/api/live/living_room/overlay?objects=1")).status == 409


async def test_learning_at_the_camera_keeps_no_picture(web, monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from pi_guard.web import api_manage

    client, app = web
    headers = {"X-CSRF-Token": await _login(client)}
    taken: list[Path] = []
    learned: list[int] = []

    class Faces:
        settings = SimpleNamespace(max_samples_per_person=40, match_threshold=0.4,
                                   uncertain_margin=0.06)  # fmt: skip
        store = SimpleNamespace(exists=lambda name: "Lena" if name.casefold() == "lena" else None,
                                people=lambda: [("Lena", 3, "2026-11-01")],
                                details=lambda name: [{"source": "camera"}] * 3)  # fmt: skip

        def camera_sample_from(self, photo: Path) -> tuple[Any, Any]:
            assert photo.exists()
            return ("sample", None)

        def learn_many(self, name: str, candidates: list[Any], actor: str) -> dict[str, Any]:
            learned.append(len(candidates))
            return {"seen": len(candidates), "stored": 1, "total": 4, "similar": 0}

        def quality(self, name: str) -> dict[str, Any]:
            return {"score": 60, "level": "ok", "views": {}, "tips": []}

        def _samples(self) -> list[Any]:
            return []

    async def photo(camera: str, prefix: str = "") -> Path:
        path = tmp_path / f"{prefix}-{len(taken)}.jpg"
        path.write_bytes(b"jpeg")
        taken.append(path)
        return path

    app.analysis = SimpleNamespace(faces=Faces(), persons=None)  # type: ignore[assignment]
    app.cameras = {"flur": SimpleNamespace(camera=SimpleNamespace(snapshot_interval_seconds=1.0))}
    monkeypatch.setattr(app, "take_photo", photo)
    monkeypatch.setattr(api_manage, "LEARN_SECONDS", (1, 1))
    url = "/api/people/Lena/camera/learn"
    no = await client.post(url, json={"camera": "flur"}, headers=headers)
    assert no.status == 400  # consent first
    unknown = await client.post("/api/people/Ben/camera/learn", json={"consent": True},
                                headers=headers)  # fmt: skip
    assert unknown.status == 404
    ok = await client.post(url, json={"camera": "flur", "consent": True}, headers=headers)
    body = await ok.json()
    assert ok.status == 200 and body["result"]["stored"] == 1 and learned == [len(taken)]
    assert taken and not any(p.exists() for p in taken)  # every picture is gone
    diagnosis = await (await client.get("/api/people-diagnosis", headers=headers)).json()
    assert diagnosis["people"][0]["name"] == "Lena" and diagnosis["people"][0]["from_camera"] == 3


async def test_the_picture_limit_is_saved_and_used_at_once(web) -> None:  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    import yaml

    from pi_guard.config.files import FaceSettings

    client, app = web
    headers = {"X-CSRF-Token": await _login(client)}
    faces = SimpleNamespace(settings=FaceSettings(max_samples_per_person=10))
    app.analysis = SimpleNamespace(faces=faces, persons=None)  # type: ignore[assignment]
    for bad in (0, 81, "40", 12.5, True):
        response = await client.put("/api/people-limit", json={"max_samples": bad}, headers=headers)
        assert response.status == 400, bad
    ok = await client.put("/api/people-limit", json={"max_samples": 40}, headers=headers)
    assert ok.status == 200 and faces.settings.max_samples_per_person == 40
    saved = yaml.safe_load((app.settings.data_dir / "dashboard.yaml").read_text())
    assert saved["faces"]["max_samples_per_person"] == 40

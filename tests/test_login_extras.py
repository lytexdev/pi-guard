from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pi_guard.web import auth
from pi_guard.web.auth import AuthStore, CredentialError, totp_code

from .helpers import ADMIN, VIEWER, msg
from .test_web import PASSWORD, _client, _login


def store(db: Any) -> AuthStore:
    s = AuthStore(db, b"k" * 32)
    s.ensure_account()
    s.change_credentials(auth.DEFAULT_PASSWORD, "admin", PASSWORD)
    return s


def test_recovery_codes_work_once_and_are_not_stored_plainly(db: Any) -> None:
    s = store(db)
    codes = s.new_recovery_codes()
    assert len(set(codes)) == 10 and s.recovery_left == 10
    assert s.use_recovery_code(codes[0].lower().replace("-", " "))
    assert not s.use_recovery_code(codes[0])
    assert not s.use_recovery_code("AAAA-BBBB-CCCC-DDDD")
    assert s.recovery_left == 9
    assert s.new_recovery_codes() and not s.use_recovery_code(codes[1])  # old set is void
    stored = [r[0] for r in db.execute("SELECT hash FROM web_recovery")]
    assert codes[1] not in stored


def test_code_word(db: Any) -> None:
    s = store(db)
    assert s.check_codeword("") and not s.has_codeword
    with pytest.raises(CredentialError):
        s.set_codeword("wrong", "Blaubeere")
    with pytest.raises(CredentialError):
        s.set_codeword(PASSWORD, "ab")
    s.set_codeword(PASSWORD, "Blaubeere")
    assert s.check_codeword(" blaubeere ") and not s.check_codeword("himbeere")
    s.set_codeword(PASSWORD, "")
    assert not s.has_codeword


async def test_login_with_totp_recovery_and_code_word(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        s = app.dashboard.auth
        secret = s.begin_totp()
        import time

        assert s.confirm_totp(totp_code(secret, int(time.time()) // 30))
        codes = s.new_recovery_codes()
        s.set_codeword(PASSWORD, "blaubeere")
        body = {"username": "admin", "password": PASSWORD}
        assert (await (await client.post("/api/login", json=body)).json())["totp_required"]
        assert (await client.get("/api/overview")).status == 401
        session = await (await client.get("/api/session")).json()
        assert session["totp"] and session["codeword"]
        # A wrong code word must not use up a recovery code.
        data = {"code": codes[0], "codeword": "falsch"}
        assert (await client.post("/api/login/totp", json=data)).status == 401
        assert s.recovery_left == 10
        data = {"code": codes[0], "codeword": "Blaubeere"}
        assert (await client.post("/api/login/totp", json=data)).status == 200
        assert s.recovery_left == 9
        assert (await client.get("/api/overview")).status == 200
        actions = [r[0] for r in app.db.execute("SELECT action FROM audit")]
        assert "login.recovery_code" in actions
    finally:
        await client.close()
        app.db.close()


async def test_telegram_login(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, dashboard_auth=["password", "telegram"])
    try:
        provider = app.providers["telegram"]
        provider.connected_flag = True
        start = await (await client.post("/api/login/telegram", json={})).json()
        message = provider.last()
        assert start["number"] in message.text
        yes = message.buttons[0][0].command
        poll = {"poll": start["poll"]}
        wait = await (await client.post("/api/login/telegram/poll", json=poll)).json()
        assert wait == {"state": "waiting"}
        assert (await client.get("/api/overview")).status == 401
        await app.messenger.handle(msg(yes, VIEWER, from_button=True))  # not an admin
        wait = await (await client.post("/api/login/telegram/poll", json=poll)).json()
        assert wait == {"state": "waiting"}
        await app.messenger.handle(msg(yes, ADMIN, from_button=True))
        assert (await client.post("/api/login/telegram/poll", json={"poll": "x"})).status == 200
        done = await (await client.post("/api/login/telegram/poll", json=poll)).json()
        assert done["ok"]
        assert (await client.get("/api/overview")).status == 200
        again = await (await client.post("/api/login/telegram/poll", json=poll)).json()
        assert again == {"state": "expired"}  # one login per confirmation
    finally:
        await client.close()
        app.db.close()


async def test_telegram_login_can_be_refused_and_needs_opt_in(tmp_path: Path) -> None:
    client, app = await _client(tmp_path / "a")
    assert (await client.post("/api/login/telegram", json={})).status == 404
    await client.close()
    app.db.close()
    client, app = await _client(tmp_path / "b", dashboard_auth=["password", "telegram"])
    try:
        start = await (await client.post("/api/login/telegram", json={})).json()
        no = app.providers["telegram"].last().buttons[1][0].command
        await app.messenger.handle(msg(no, ADMIN, from_button=True))
        result = await client.post("/api/login/telegram/poll", json={"poll": start["poll"]})
        assert await result.json() == {"state": "denied"}
        assert (await client.get("/api/overview")).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_public_address_restricts_host_names(tmp_path: Path) -> None:
    client, app = await _client(tmp_path, dashboard_public_url="https://guard.example.org")
    try:
        assert (await client.get("/", headers={"Host": "guard.example.org"})).status == 200
        assert (await client.get("/", headers={"Host": "192.168.178.109:8443"})).status == 200
        assert (await client.get("/", headers={"Host": "evil.example"})).status == 421
        response = await client.post("/api/login", headers={"Host": "guard.example.org"},
                                     json={"username": "admin", "password": PASSWORD})  # fmt: skip
        assert "Secure" in response.headers["Set-Cookie"]
        actions = [r[0] for r in app.db.execute("SELECT action FROM audit")]
        assert "security.bad_host" in actions
    finally:
        await client.close()
        app.db.close()


async def test_webauthn_refuses_ip_addresses(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        data = await (await client.get("/api/account/webauthn")).json()
        assert data["usable_here"] is False  # the test client talks to 127.0.0.1
        response = await client.post("/api/account/webauthn/begin", json={}, headers=headers)
        assert response.status == 409
    finally:
        await client.close()
        app.db.close()


async def test_security_analysis(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.web import api_security

    async def neighbours(interface: Any = None) -> dict[str, Any]:
        return {"aa:bb:cc:00:00:01": ("192.168.178.5", set())}

    monkeypatch.setattr(api_security, "neighbour_table", neighbours)
    client, app = await _client(tmp_path)
    try:
        bad = {"username": "admin", "password": "wrong"}
        assert (await client.post("/api/login", json=bad)).status == 401
        await _login(client)
        guard = app.dashboard.guard
        guard.honeypot_hit("203.0.113.9", "/wp-admin")
        data = await (await client.get("/api/security")).json()
        assert data["counts"]["failed_logins"] == 1
        assert data["counts"]["honeypot_hits"] == 1 and data["counts"]["bans"] == 1
        assert any(s["ip"] == "203.0.113.9" for s in data["top_sources"])
        assert data["honeypot_paths"][0]["path"] == "/wp-admin"
        assert data["recommendations"][0] == {"key": "web.sec_tip_2fa", "href": "#/settings/access"}
        assert data["new_devices"] == []  # the first look only learns what is there

        async def more(interface: Any = None) -> dict[str, Any]:
            return {**await neighbours(), "aa:bb:cc:00:00:02": ("192.168.178.6", set())}

        monkeypatch.setattr(api_security, "neighbour_table", more)
        data = await (await client.get("/api/security")).json()
        assert [d["ip"] for d in data["new_devices"]] == ["192.168.178.6"]
    finally:
        await client.close()
        app.db.close()


async def test_notifications_can_be_switched_off_at_once(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        rows = (await (await client.get("/api/notifications")).json())["rules"]
        assert next(r for r in rows if r["event"] == "system_started")["enabled"] is True
        response = await client.put("/api/notifications/system_started", json={"enabled": False},
                                    headers=headers)  # fmt: skip
        assert response.status == 200
        rows = (await (await client.get("/api/notifications")).json())["rules"]
        assert next(r for r in rows if r["event"] == "system_started")["enabled"] is False
        assert app.config.file.notifications.rules["system_started"].enabled is False
        wrong = await client.put("/api/notifications/nope", json={}, headers=headers)
        assert wrong.status == 404
        bad = await client.put("/api/notifications/mode_changed", json={"modes": ["X"]},
                               headers=headers)  # fmt: skip
        assert bad.status == 400
    finally:
        await client.close()
        app.db.close()


async def _viewer(client: Any, csrf: str, role: str = "viewer") -> tuple[int, dict[str, str]]:
    """Adds a person and logs in as them in a second cookie jar; returns (user id, headers)."""
    body = {"username": "oma", "password": "lange-passphrase-1", "role": role}
    response = await client.post("/api/dashboard-users", json=body, headers={"X-CSRF-Token": csrf})
    assert response.status == 200
    data = await (await client.get("/api/dashboard-users")).json()
    client.session.cookie_jar.clear()
    login = await client.post("/api/login", json={"username": "oma", "password": body["password"]})
    assert login.status == 200
    session = await (await client.get("/api/session")).json()
    assert (session["role"] == role and session["assistant"] is False) or role == "admin"
    return data["users"][0]["id"], {"X-CSRF-Token": session["csrf"]}


async def test_viewers_only_watch(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        _, headers = await _viewer(client, await _login(client))
        for path in ("/api/overview", "/api/events", "/api/stats?days=3", "/api/account"):
            assert (await client.get(path)).status == 200, path
        for path in ("/api/settings", "/api/users", "/api/logs", "/api/people", "/api/topology",
                     "/api/dashboard-users", "/api/live/cam/audio", "/api/account/webauthn",
                     "/api/tokens", "/api/sounds"):  # fmt: skip
            assert (await client.get(path)).status == 403, path
        for path in ("/api/mode", "/api/talk", "/api/alarm", "/api/alerts/ack", "/api/evidence",
                     "/api/assistant/chat", "/api/dashboard-users",
                     "/api/system/restart"):  # fmt: skip
            assert (await client.post(path, json={}, headers=headers)).status == 403, path
        assert (await client.put("/api/appearance", json={}, headers=headers)).status == 403
    finally:
        await client.close()
        app.db.close()


async def test_people_keep_their_own_login(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        csrf = await _login(client)
        owner = (await (await client.get("/api/account")).json())["username"]
        taken = {"username": owner.upper(), "password": "lange-passphrase-1", "role": "viewer"}
        headers = {"X-CSRF-Token": csrf}
        response = await client.post("/api/dashboard-users", json=taken, headers=headers)
        assert response.status == 400 and await response.text() == "web.user_taken"

        user_id, headers = await _viewer(client, csrf)
        new = {"current": "lange-passphrase-1", "password": "andere-passphrase-2"}
        assert (await client.post("/api/me/password", json=new, headers=headers)).status == 200
        setup = await (await client.post("/api/account/totp/begin", headers=headers)).json()
        assert app.db.execute("SELECT totp FROM web_account").fetchone()["totp"] is None
        assert setup["secret"]

        # Removed people lose their session at once; a changed role counts immediately.
        users = app.dashboard.auth
        assert users.set_role(user_id, "admin") and users.role_of("oma") == "admin"
        assert (await client.get("/api/settings")).status == 200
        assert users.delete_user(user_id)
        assert (await client.get("/api/overview")).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_appearance_and_logo(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        bad = {"accent": "red;}", "font": "system"}
        assert (await client.put("/api/appearance", json=bad, headers=headers)).status == 400
        body = {"accent": "#ff7a59", "font": "serif", "animations": False}
        assert (await client.put("/api/appearance", json=body, headers=headers)).status == 200

        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        url = "/api/appearance/logo"
        assert (await client.post(url, data=svg, headers=headers)).status == 400
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
        assert (await client.post(url, data=png, headers=headers)).status == 200

        client.session.cookie_jar.clear()  # the login page shows both
        session = await (await client.get("/api/session")).json()
        assert session["appearance"]["accent"] == "#ff7a59" and session["appearance"]["logo"]
        logo = await client.get("/branding/logo")
        assert logo.status == 200 and await logo.read() == png
    finally:
        await client.close()
        app.db.close()

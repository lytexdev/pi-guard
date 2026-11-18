"""Inviting a person: the apps, and a VPN access only with the password, only to their own ways."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .test_mobile import _with_vpn
from .test_web import PASSWORD, _client, _login


async def test_an_invitation_goes_only_to_the_person_and_vpn_needs_the_password(
    tmp_path: Path,
) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        person = {"display_name": "Mia", "roles": ["household"],
                  "links": [{"kind": "messenger", "ref": "telegram:4711"}]}  # fmt: skip
        made = await (await client.post("/api/persons", json={"person": person}, headers=h)).json()
        url = f"/api/persons/{made['id']}/invite"
        sent: list[Any] = []

        async def send(provider: str, chat: str, reply: Any) -> None:
            sent.append((provider, chat, reply.text, reply.document and reply.document.read_text()))

        app.messenger.send = send  # type: ignore[method-assign]
        stranger = {"provider": "telegram", "address": "999", "items": ["android"]}
        assert (await client.post(url, json=stranger, headers=h)).status == 400
        mine = {"provider": "telegram", "address": "4711", "items": ["android"]}
        assert (await client.post(url, json=mine, headers=h)).status == 200
        assert sent[0][:2] == ("telegram", "4711") and sent[0][3] is None
        assert "not on this Pi yet" in sent[0][2]  # no app published here yet
        apk = app.settings.data_dir / "apps" / "android"
        apk.mkdir(parents=True)
        (apk / "latest.json").write_text('{"version_name": "1.14.0"}')
        assert (await client.post(url, json=mine, headers=h)).status == 200
        assert "/get/" in sent[-1][2] and sent[-1][2].count("/android") == 1

        await _with_vpn(tmp_path, app)
        vpn = {**mine, "items": ["vpn"]}
        assert (await client.post(url, json={**vpn, "password": "nope"}, headers=h)).status == 403
        ok = await client.post(url, json={**vpn, "password": PASSWORD}, headers=h)
        assert ok.status == 200 and (await ok.json())["vpn"]
        assert "[Interface]" in sent[-1][3] and "PrivateKey" in sent[-1][3]
        saved = (await (await client.get("/api/persons")).json())["persons"][0]
        assert any(link["kind"] == "vpn" for link in saved["links"])  # the device belongs to Mia
        assert [p["name"] for p in app.dashboard.wg_peers.list()] == ["Mia"]
    finally:
        await client.close()
        app.db.close()


async def test_a_failed_invitation_leaves_no_access_and_a_wrong_password_no_link(
    tmp_path: Path,
) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        person = {"display_name": "Mia", "links": [{"kind": "messenger", "ref": "telegram:4711"}]}
        made = await (await client.post("/api/persons", json={"person": person}, headers=h)).json()
        url = f"/api/persons/{made['id']}/invite"
        await _with_vpn(tmp_path, app)
        both = {"provider": "telegram", "address": "4711", "items": ["android", "vpn"]}

        assert (await client.post(url, json={**both, "password": "nope"}, headers=h)).status == 403
        assert not app.db.get("apps.invites")  # no download link was made either

        async def broken(provider: str, chat: str, reply: Any) -> None:
            raise RuntimeError("offline")

        app.messenger.send = broken  # type: ignore[method-assign]
        failed = await client.post(url, json={**both, "password": PASSWORD}, headers=h)
        assert failed.status == 502
        assert app.dashboard.wg_peers.list() == []  # nobody got it, so it is gone
        saved = (await (await client.get("/api/persons")).json())["persons"][0]
        assert not any(link["kind"] == "vpn" for link in saved["links"])
    finally:
        await client.close()
        app.db.close()


async def test_the_contacts_grant_alone_hands_out_no_vpn_access(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        person = {"display_name": "Mia", "links": [{"kind": "messenger", "ref": "telegram:4711"}]}
        made = await (await client.post("/api/persons", json={"person": person}, headers=h)).json()
        app.dashboard.auth.add_user("ben", "another long password", "member")
        ben = next(u for u in app.dashboard.auth.users() if u["username"] == "ben")
        grant = {"role": "member", "permissions": ["contacts"]}
        await client.patch(f"/api/dashboard-users/{ben['id']}", json=grant, headers=h)
        await _with_vpn(tmp_path, app)

        client.session.cookie_jar.clear()
        login = {"username": "ben", "password": "another long password"}
        assert (await client.post("/api/login", json=login)).status == 200
        h = {"X-CSRF-Token": (await (await client.get("/api/session")).json())["csrf"]}
        assert (await client.get("/api/persons")).status == 200
        url = f"/api/persons/{made['id']}/invite"
        vpn = {"provider": "telegram", "address": "4711", "items": ["vpn"],
               "password": "another long password"}  # fmt: skip
        assert (await client.post(url, json=vpn, headers=h)).status == 403
        app_only = {"provider": "telegram", "address": "4711", "items": ["android"]}
        assert (await client.post(url, json=app_only, headers=h)).status == 403
        assert app.dashboard.wg_peers.list() == []
    finally:
        await client.close()
        app.db.close()


async def test_login_details_carry_a_temporary_password_that_must_be_changed(
    tmp_path: Path,
) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        auth = app.dashboard.auth
        new_user = {"username": "oma", "role": "viewer", "temporary": True}
        made = await client.post("/api/dashboard-users", json=new_user, headers=h)
        first = (await made.json())["password"]
        assert auth.must_renew("oma") and auth.check_password("oma", first) == "oma"
        links = [
            {"kind": "messenger", "ref": "telegram:4711"},
            {"kind": "web_account", "ref": "oma"},
        ]
        person = {"display_name": "Oma", "links": links}
        made = await (await client.post("/api/persons", json={"person": person}, headers=h)).json()
        url = f"/api/persons/{made['id']}/invite"
        sent: list[str] = []

        async def send(provider: str, chat: str, reply: Any) -> None:
            sent.append(reply.text)

        app.messenger.send = send  # type: ignore[method-assign]
        login = {"provider": "telegram", "address": "4711", "items": ["dashboard", "login"]}
        assert (await client.post(url, json=login, headers=h)).status == 403  # needs the password
        assert auth.check_password("oma", first) == "oma" and not sent
        ok = await client.post(url, json={**login, "password": PASSWORD}, headers=h)
        assert ok.status == 200 and (await ok.json())["login"]
        temporary = sent[0].split("temporary password ")[1].split()[0]
        assert "oma" in sent[0] and auth.check_password("oma", temporary) == "oma"
        assert auth.check_password("oma", first) is None  # the old one is gone

        # The person logs in and may only choose an own password.
        client.session.cookie_jar.clear()
        assert (
            await client.post("/api/login", json={"username": "oma", "password": temporary})
        ).status == 200
        session = await (await client.get("/api/session")).json()
        assert session["password_renew"]
        mine = {"X-CSRF-Token": session["csrf"]}
        blocked = await client.get("/api/overview")
        assert blocked.status == 403 and await blocked.text() == "password_renew"
        change = {"current": temporary, "password": "my own long password"}
        assert (await client.post("/api/me/password", json=change, headers=mine)).status == 200
        assert not auth.must_renew("oma")
        assert (await client.get("/api/overview")).status == 200
    finally:
        await client.close()
        app.db.close()


async def test_the_owner_never_gets_login_details_sent(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        owner = app.dashboard.auth.account()["username"]
        links = [
            {"kind": "messenger", "ref": "telegram:4711"},
            {"kind": "web_account", "ref": owner},
        ]
        person = {"display_name": "Ich", "links": links}
        made = await (await client.post("/api/persons", json={"person": person}, headers=h)).json()
        body = {"provider": "telegram", "address": "4711", "items": ["login"], "password": PASSWORD}
        answer = await client.post(f"/api/persons/{made['id']}/invite", json=body, headers=h)
        assert answer.status == 409 and await answer.text() == "web.inv_no_account"
    finally:
        await client.close()
        app.db.close()

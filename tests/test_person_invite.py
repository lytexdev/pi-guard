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
        stranger = {"provider": "telegram", "address": "999", "app": True}
        assert (await client.post(url, json=stranger, headers=h)).status == 400
        mine = {"provider": "telegram", "address": "4711", "app": True}
        assert (await client.post(url, json=mine, headers=h)).status == 200
        assert sent[0][:2] == ("telegram", "4711") and "/get/" in sent[0][2] and sent[0][3] is None

        await _with_vpn(tmp_path, app)
        vpn = {**mine, "app": False, "vpn": True}
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
        both = {"provider": "telegram", "address": "4711", "app": True, "vpn": True}

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
        vpn = {"provider": "telegram", "address": "4711", "app": False, "vpn": True,
               "password": "another long password"}  # fmt: skip
        assert (await client.post(url, json=vpn, headers=h)).status == 403
        app_only = {"provider": "telegram", "address": "4711", "app": True}
        assert (await client.post(url, json=app_only, headers=h)).status == 403
        assert app.dashboard.wg_peers.list() == []
    finally:
        await client.close()
        app.db.close()

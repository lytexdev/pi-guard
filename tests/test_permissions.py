"""Members: what the owner granted, and nothing else, also nothing added later."""

from __future__ import annotations

from pathlib import Path

from pi_guard.web import permissions
from pi_guard.web.permissions import allowed

from .test_web import _client, _login


def test_a_grant_opens_only_its_paths() -> None:
    assert allowed(["control"], "POST", "/api/mode")
    assert not allowed(["control"], "POST", "/api/models/download")  # a prefix is no grant
    assert allowed(["network"], "GET", "/api/network/wireguard/peers")
    assert not allowed(["network"], "GET", "/api/networks")
    assert allowed(["people"], "GET", "/api/people-diagnosis")
    assert allowed(["people"], "POST", "/api/events/3/faces/0")
    assert not allowed([], "POST", "/api/events/3/faces/0")
    assert allowed(["addon:medical"], "POST", "/api/medical/view")
    assert allowed(["addon:kids"], "GET", "/api/addons") and not allowed(
        ["addon:kids"], "PUT", "/api/addons"
    )
    assert not allowed(["addon:kids"], "GET", "/api/medical")
    never = (
        "/api/dashboard-users",
        "/api/tokens",
        "/api/backup",
        "/api/system/update",
        "/api/scan/fix",
        "/api/plugin-manager",
        "/api/settings/history",
        "/api/v1/status",
    )
    for path in never:
        assert not allowed(permissions.ALL, "POST", path), path
    assert permissions.clean(["logs", "nonsense", "logs", "settings:general"]) == [
        "logs",
        "settings:general",
    ]


async def test_a_member_gets_what_was_granted(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        app.dashboard.auth.add_user("ben", "another long password", "member")
        ben = next(u for u in app.dashboard.auth.users() if u["username"] == "ben")
        grant = {"role": "member", "permissions": ["logs", "settings:recording", "x"]}
        assert (
            await client.patch(f"/api/dashboard-users/{ben['id']}", json=grant, headers=h)
        ).status == 200
        listed = await (await client.get("/api/dashboard-users")).json()
        assert next(u for u in listed["users"] if u["username"] == "ben")["permissions"] == [
            "logs",
            "settings:recording",
        ]

        client.session.cookie_jar.clear()
        assert (
            await client.post(
                "/api/login", json={"username": "ben", "password": "another long password"}
            )
        ).status == 200
        session = await (await client.get("/api/session")).json()
        h = {"X-CSRF-Token": session["csrf"]}
        assert session["role"] == "member" and session["permissions"] == [
            "logs",
            "settings:recording",
        ]
        assert (await client.get("/api/overview")).status == 200  # what viewers see
        assert (await client.get("/api/logs")).status == 200  # granted
        assert (await client.get("/api/network")).status == 403  # not granted
        assert (await client.get("/api/dashboard-users")).status == 403  # never grantable
        settings = await (await client.get("/api/settings")).json()
        assert {f["group"] for f in settings["fields"]} == {"recording"}
        ok = await client.put("/api/settings", json={"recording_retention_days": 9}, headers=h)
        assert ok.status == 200
        sneaky = await client.put("/api/settings", json={"dashboard_tls": False}, headers=h)
        assert sneaky.status == 403
    finally:
        await client.close()
        app.db.close()


def test_profile_links_are_checked() -> None:
    import pytest

    from pi_guard.web.profiles import ProfileError, clean

    links = {"telegram": "@anna_k", "discord": "123456789012345678", "signal": "+49 170 1"}
    assert clean(links) == links
    for bad in ({"telegram": "anna; rm"}, {"discord": "abc"}, {"whatsapp": "call me"}):
        with pytest.raises(ProfileError):
            clean(bad)

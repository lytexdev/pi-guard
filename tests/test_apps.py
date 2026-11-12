"""Invitations only open downloads, and only for a few days."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .test_web import _client, _login


async def test_an_invitation_opens_the_downloads_and_nothing_else(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        android = app.settings.data_dir / "apps" / "android"
        android.mkdir(parents=True)
        (android / "latest.apk").write_bytes(b"PK apk")
        (android / "latest.json").write_text(
            json.dumps({"version_name": "1.9.0", "version_code": 9})
        )
        headers = {"X-CSRF-Token": await _login(client)}
        info = await (await client.get("/api/apps")).json()
        assert info["android"] == "1.9.0" and info["invites"] == 0 and info["pi"]
        made = await (await client.post("/api/apps/invite", json={}, headers=headers)).json()
        path = made["path"]

        client.session.cookie_jar.clear()  # someone without an account
        page = await client.get(path)
        assert page.status == 200 and "1.9.0" in await page.text()
        apk = await client.get(f"{path}/android")
        assert apk.status == 200 and await apk.read() == b"PK apk"
        assert (await client.get(f"{path}/desktop/linux-x64")).status == 404
        assert (await client.get(f"{path}/desktop/../../etc")).status == 404
        assert (await client.get("/api/overview")).status == 401  # still no way in
        assert (await client.get("/get/" + "a" * 32)).status == 404
        stored = json.dumps(app.db.get("apps.invites"))
        assert path.rsplit("/", 1)[1] not in stored  # only its hash

        invites = app.db.get("apps.invites")
        invites[0]["expires"] = time.time() - 1
        app.db.set("apps.invites", invites)
        assert (await client.get(path)).status == 404
    finally:
        await client.close()
        app.db.close()


def test_an_add_on_from_others_can_bring_its_own_help() -> None:
    import pytest

    from pi_guard.addons.plugin import ManifestError, check_manifest

    base = {"id": "bell_ring", "name": "Bell", "entry": "plugin.py"}
    manifest = check_manifest(
        {**base, "help": "## Use\n1. Ring", "docs": "https://example.org/bell"}
    )
    assert manifest["help"].startswith("## Use") and manifest["docs"] == "https://example.org/bell"
    assert (
        check_manifest(base)["help"] == ""
        and len(check_manifest({**base, "help": "x" * 9000})["help"]) == 4000
    )
    with pytest.raises(ManifestError):
        check_manifest({**base, "docs": "javascript:alert(1)"})

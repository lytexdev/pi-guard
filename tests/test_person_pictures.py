"""Own profile pictures of contacts: only JPEG, small, and back to the initials."""

from __future__ import annotations

from pathlib import Path

from .test_web import _client, _login


async def test_an_own_picture_replaces_the_initials_and_can_go_again(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        made = await (await client.post("/api/persons", json={"person": {"display_name": "Oma"}},
                                        headers=h)).json()  # fmt: skip
        url = f"/api/persons/{made['id']}/picture"
        assert (await client.get(url)).status == 404
        jpeg = b"\xff\xd8\xff\xe0" + b"0" * 2000
        assert (await client.put(url, data=b"<svg onload=x>", headers=h)).status == 400
        assert (
            await client.put(url, data=b"\xff\xd8\xff" + b"0" * 130_000, headers=h)
        ).status == 413
        assert (await client.put(url, data=jpeg, headers=h)).status == 200
        got = await client.get(url)
        assert got.content_type == "image/jpeg" and await got.read() == jpeg
        listed = await (await client.get("/api/persons")).json()
        assert listed["persons"][0]["picture"] and "admin" in listed["account_roles"].values()
        assert (await client.delete(url, headers=h)).status == 200
        assert (await client.get(url)).status == 404
        assert (await client.put(url, data=jpeg)).status == 403  # CSRF
    finally:
        await client.close()
        app.db.close()

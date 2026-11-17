"""Saved Wi-Fi profiles: the key is sealed in the database and only opened for a QR code."""

from __future__ import annotations

import json
from pathlib import Path

from .test_web import PASSWORD, _client, _login

KEY = "correct horse battery"


async def test_wifi_keys_are_sealed_and_old_plain_ones_get_sealed(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        body = {"password": PASSWORD,
                "profiles": [{"id": "home", "ssid": "Zuhause", "key": KEY}]}  # fmt: skip
        put = await client.put("/api/network/wifi/profiles", json=body, headers=h)
        assert put.status == 200
        listed = await put.json()
        assert listed["profiles"][0]["has_key"] and "key" not in listed["profiles"][0]
        assert KEY not in json.dumps(app.db.get("network.wifi_profiles"))

        qr = await client.post("/api/network/wifi/qr", json={"profile": "home"}, headers=h)
        assert (await qr.json())["key"] == KEY

        app.db.set("network.wifi_profiles", [{"id": "old", "ssid": "Alt", "key": KEY}])
        assert (await client.get("/api/network/wifi/profiles")).status == 200
        assert KEY not in json.dumps(app.db.get("network.wifi_profiles"))
        qr = await client.post("/api/network/wifi/qr", json={"profile": "old"}, headers=h)
        assert (await qr.json())["key"] == KEY
    finally:
        await client.close()
        app.db.close()

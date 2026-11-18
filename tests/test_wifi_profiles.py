"""The two saved Wi-Fi networks: keys sealed in the database, only opened for a QR code."""

from __future__ import annotations

import json
from pathlib import Path

from .test_web import PASSWORD, _client, _login

KEY = "correct horse battery"


async def test_home_and_guest_are_sealed_and_need_the_password(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        url = "/api/network/wifi/profiles/home"
        wrong = {"password": "falsch", "ssid": "Zuhause", "key": KEY}
        assert (await client.put(url, json=wrong, headers=h)).status == 403
        put = await client.put(url, json={**wrong, "password": PASSWORD}, headers=h)
        assert put.status == 200
        listed = (await put.json())["profiles"]
        assert listed == [{"id": "home", "ssid": "Zuhause", "guest": False, "has_key": True}]
        assert KEY not in json.dumps(app.db.get("network.wifi_profiles"))
        qr = await client.post("/api/network/wifi/qr", json={"profile": "home"}, headers=h)
        assert (await qr.json())["key"] == KEY

        # A new name alone keeps the key; a third slot does not exist.
        rename = {"password": PASSWORD, "ssid": "Daheim", "key": ""}
        assert (await client.put(url, json=rename, headers=h)).status == 200
        qr = await client.post("/api/network/wifi/qr", json={"profile": "home"}, headers=h)
        assert (await qr.json())["ssid"] == "Daheim" and (await qr.json())["key"] == KEY
        other = "/api/network/wifi/profiles/office"
        assert (await client.put(other, json=rename, headers=h)).status == 404

        gone = await client.delete(url, json={"password": PASSWORD}, headers=h)
        assert (await gone.json())["profiles"] == []
    finally:
        await client.close()
        app.db.close()


async def test_old_profile_lists_become_the_two_slots(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        h = {"X-CSRF-Token": await _login(client)}
        app.db.set("network.wifi_profiles", [
            {"id": "a1", "ssid": "Alt", "key": KEY},
            {"id": "b2", "ssid": "Besuch", "key": KEY, "guest": True},
            {"id": "c3", "ssid": "Noch eins", "key": KEY},
        ])  # fmt: skip
        listed = (await (await client.get("/api/network/wifi/profiles")).json())["profiles"]
        assert [(p["id"], p["ssid"]) for p in listed] == [("home", "Alt"), ("guest", "Besuch")]
        assert KEY not in json.dumps(app.db.get("network.wifi_profiles"))
        qr = await client.post("/api/network/wifi/qr", json={"profile": "guest"}, headers=h)
        assert (await qr.json())["key"] == KEY
    finally:
        await client.close()
        app.db.close()

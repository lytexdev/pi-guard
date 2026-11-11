"""Moving: noticing another network, the checks, and the moving file for a new Pi."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pi_guard.network import move
from pi_guard.network.move import Network, changes

from .test_web import PASSWORD, _client, _login

HOME = Network(gateway="192.168.178.1", gateway_mac="aa:aa:aa:aa:aa:01", interface="wlan0",
               addresses=["192.168.178.20/24"], ssid="Zuhause")  # fmt: skip


def test_another_router_or_network_is_noticed() -> None:
    home = HOME.as_dict()
    assert changes(None, HOME) == [] and changes(home, HOME) == []
    # Every FRITZ!Box is 192.168.178.1: only its hardware address tells a new one apart.
    new_box = Network(**{**home, "gateway_mac": "bb:bb:bb:bb:bb:02"})
    assert changes(home, new_box) == ["router"]
    elsewhere = Network(gateway="192.168.1.1", gateway_mac="cc:cc:cc:cc:cc:03", interface="eth0",
                        addresses=["192.168.1.30/24"])  # fmt: skip
    assert changes(home, elsewhere) == ["router", "network"]
    other_wifi = Network(**{**home, "ssid": "Neu"})
    assert changes(home, other_wifi) == ["wifi"]
    assert HOME.contains("192.168.178.77") and not HOME.contains("10.0.0.5")


def test_the_vpn_check_offers_the_new_address(tmp_path: Path) -> None:
    from types import SimpleNamespace

    app = SimpleNamespace(settings=SimpleNamespace(
        wireguard_enabled=True, wireguard_endpoint="203.0.113.5:51820", data_dir=tmp_path,
        wireguard_interface="wg0", runtime_dir=tmp_path))  # fmt: skip
    found = move.vpn(app, "198.51.100.7", HOME)
    assert found["state"] == "todo" and found["public"] == "198.51.100.7" and found["kind"] == "ip"
    assert move.vpn(app, "203.0.113.5", HOME)["state"] == "info"
    app.settings.wireguard_endpoint = "home.example.org"
    assert move.vpn(app, "198.51.100.7", HOME)["kind"] == "name"
    app.settings.wireguard_enabled = False
    assert move.vpn(app, None, HOME)["state"] == "off"


async def test_moving_in_the_dashboard(tmp_path: Path, monkeypatch: Any) -> None:
    from pi_guard.backup import restore

    async def here() -> Network:
        return HOME

    async def public() -> str:
        return "198.51.100.7"

    monkeypatch.setattr(move, "current", here)
    monkeypatch.setattr("pi_guard.web.api_addons.home_ip", public)
    client, app = await _client(tmp_path, wireguard_enabled=True,
                                wireguard_endpoint="203.0.113.5:51820")  # fmt: skip
    try:
        headers = {"X-CSRF-Token": await _login(client)}
        first = await (await client.get("/api/move?check=1")).json()
        assert first["changes"] == [] and app.db.get(move.HOME_KEY)["ssid"] == "Zuhause"
        checks = {c["id"]: c for c in first["checks"]}
        assert checks["vpn"]["state"] == "todo" and checks["backup"]["state"] == "todo"
        assert "cameras" not in checks  # only USB cameras here

        wrong = await client.post("/api/move/endpoint", json={"password": "no"}, headers=headers)
        assert wrong.status == 403
        done = await client.post("/api/move/endpoint", json={"password": PASSWORD},
                                 headers=headers)  # fmt: skip
        assert (await done.json())["endpoint"] == "198.51.100.7:51820"
        assert "PI_GUARD_WIREGUARD_ENDPOINT=198.51.100.7:51820" in (
            app.settings.data_dir / "dashboard.env").read_text()  # fmt: skip

        short = await client.post("/api/move/package", headers=headers,
                                  json={"password": PASSWORD, "passphrase": "short"})  # fmt: skip
        assert short.status == 400
        body = {"password": PASSWORD, "passphrase": "a long moving passphrase"}
        sealed = await client.post("/api/move/package", headers=headers, json=body)
        assert sealed.status == 200
        file = tmp_path / "move.pgbk"
        file.write_bytes(await sealed.read())
        unpacked = restore(file, "a long moving passphrase", tmp_path / "out")
        assert (unpacked / "data" / "pi-guard.db").is_file()
        actions = {r[0] for r in app.db.execute("SELECT action FROM audit")}
        assert {"move.endpoint", "move.package"} <= actions
    finally:
        await client.close()
        app.db.close()

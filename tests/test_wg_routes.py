"""Per-device VPN exit routes and renaming."""

from __future__ import annotations

from pathlib import Path

import pytest

from pi_guard.network.wgpeers import PeerError, PeerStore
from pi_guard.privacy.firewall import Plan, render
from pi_guard.privacy.store import PrivacyStore

from .test_web import _client, _login


def test_rename_and_route(db: object, tmp_path: Path) -> None:
    store = PeerStore(db, tmp_path, b"k" * 32)  # type: ignore[arg-type]
    peer_id = store.add("Phone")
    assert store.rename(peer_id, "Pixel Anna")
    assert store.name(peer_id) == "Pixel Anna"
    with pytest.raises(PeerError):
        store.rename(peer_id, "")
    assert store.set_route(peer_id, "home")
    peers = {p["id"]: p for p in store.list()}
    assert peers[peer_id]["route"] == "home"
    assert store.home_addresses() == [peers[peer_id]["address"]]
    routes = (tmp_path / "privacy" / "device-routes").read_text()
    assert peers[peer_id]["address"] in routes and "home" in routes
    with pytest.raises(PeerError):
        store.set_route(peer_id, "toaster")


async def test_dashboard_renames_vpn_device(tmp_path: Path) -> None:
    """Session has no .role; rename must use auth.role_of or it returns 500."""
    client, app = await _client(
        tmp_path,
        wireguard_enabled=True,
        wireguard_endpoint="home.example.net",
        wireguard_home_network="192.168.178.0/24",
    )
    try:
        csrf = await _login(client)
        headers = {"X-CSRF-Token": csrf}
        (tmp_path / "run").mkdir(exist_ok=True)
        (tmp_path / "run" / "wireguard.status").write_text(
            "written\t2026-11-02T10:00:00Z\n(hidden)\t" + "S" * 43 + "=\t51820\toff\n"
        )
        added = await client.post(
            "/api/network/wireguard/peers", json={"name": "Handy Anna"}, headers=headers
        )
        assert added.status == 200
        peer_id = (await added.json())["id"]
        renamed = await client.patch(
            f"/api/network/wireguard/peers/{peer_id}",
            json={"name": "Pixel Anna"},
            headers=headers,
        )
        assert renamed.status == 200, await renamed.text()
        assert (await renamed.json())["changed"] == ["name"]
        overview = await (await client.get("/api/network")).json()
        assert overview["wireguard"]["peers"][0]["name"] == "Pixel Anna"
        home = await client.patch(
            f"/api/network/wireguard/peers/{peer_id}",
            json={"route": "home"},
            headers=headers,
        )
        assert home.status == 200, await home.text()
    finally:
        await client.close()
        app.db.close()


def test_firewall_lets_home_only_bypass_exit() -> None:
    text = render(
        Plan(mode="strict", exit_up=True, home_only=("10.8.0.5", "10.8.0.6"))
    )
    assert "ip saddr { 10.8.0.5, 10.8.0.6 } accept" in text
    assert 'oifname "pgexit" accept' in text


def test_privacy_store_reads_home_routes(tmp_path: Path) -> None:
    store = PrivacyStore(tmp_path, b"s" * 32)
    path = tmp_path / "privacy" / "device-routes"
    path.write_text("10.8.0.5\thome\n10.8.0.9\texit\nbad\thome\n")
    assert store.home_only_addresses() == ("10.8.0.5",)

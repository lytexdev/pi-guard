"""Per-device VPN exit routes and renaming."""

from __future__ import annotations

from pathlib import Path

import pytest

from pi_guard.network.wgpeers import PeerError, PeerStore
from pi_guard.privacy.firewall import Plan, render
from pi_guard.privacy.store import PrivacyStore


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

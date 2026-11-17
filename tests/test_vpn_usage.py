"""Hourly VPN usage samples."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pi_guard.network.usage import VpnUsage
from pi_guard.network.wgpeers import PeerStore


def test_usage_samples_deltas(db: Any, tmp_path: Path, monkeypatch: Any) -> None:
    store = PeerStore(db, tmp_path, b"k" * 32)
    peer_id = store.add("Laptop")
    peer = store.list()[0]
    settings = type("S", (), {"wireguard_enabled": True, "data_dir": tmp_path})()
    usage = VpnUsage(db, settings)

    def status(_path: Path | None = None) -> dict[str, Any]:
        return {
            "peers": [
                {
                    "public_key": peer["public_key"],
                    "received": status.rx,
                    "sent": status.tx,
                    "active": True,
                }
            ]
        }

    status.rx, status.tx = 1000, 2000
    monkeypatch.setattr("pi_guard.network.usage.read_status", lambda path: status())
    monkeypatch.setattr("pi_guard.network.usage.status_file", lambda s, name: tmp_path / name)
    usage.sample()  # baseline only
    status.rx, status.tx = 1500, 2600
    usage.sample()
    rows = usage.history(peer_id, days=1)
    assert rows and rows[-1]["rx"] == 500 and rows[-1]["tx"] == 600
    totals = usage.totals(1)
    assert totals[0]["peer_id"] == peer_id and totals[0]["rx"] == 500

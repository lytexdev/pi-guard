from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pi_guard.network.wgpeers import PeerError, PeerStore, Server, generate_keypair

from .test_web import _client, _login

REPO = Path(__file__).resolve().parent.parent
APPLY = REPO / "deploy" / "wireguard" / "pi-guard-wg-apply"
SERVER = Server("S" * 43 + "=", "home.example.net", "10.8.0.1", "192.168.178.0/24")


def test_keypair_matches_wireguard_format() -> None:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    private, public = generate_keypair()
    key = X25519PrivateKey.from_private_bytes(base64.b64decode(private))
    derived = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert base64.b64encode(derived).decode() == public
    assert len(private) == len(public) == 44


def test_peers_get_addresses_and_configs(db: Any, tmp_path: Path) -> None:
    store = PeerStore(db, tmp_path, b"k" * 32)
    phone = store.add("Handy Anna")
    laptop = store.add("Laptop", full_tunnel=False)
    with pytest.raises(PeerError, match="taken"):
        store.add("handy anna")
    with pytest.raises(PeerError, match="bad_name"):
        store.add("   ")
    assert [p["address"] for p in store.list()] == ["10.8.0.2", "10.8.0.3"]

    full = store.client_config(phone, SERVER)
    assert full is not None
    assert "AllowedIPs = 0.0.0.0/0, ::/0" in full
    assert "Endpoint = home.example.net:51820" in full
    assert "DNS = 10.8.0.1" in full
    home = store.client_config(laptop, SERVER)
    assert home is not None and "AllowedIPs = 10.8.0.0/24, 192.168.178.0/24" in home

    # The file for the root job holds no private keys.
    text = (tmp_path / "wireguard" / "peers").read_text()
    assert len(text.splitlines()) == 2
    private = re.search(r"PrivateKey = (\S+)", full)
    assert private and private.group(1) not in text

    store.remove(phone)
    assert store.add("Tablet") and store.list()[1]["address"] == "10.8.0.2"
    # A different secret key cannot open the stored keys.
    with pytest.raises(PeerError, match="key_lost"):
        PeerStore(db, tmp_path, b"x" * 32).client_config(laptop, SERVER)


def test_endpoint_port_formatting() -> None:
    assert Server("k", "1.2.3.4", None, None).endpoint_with_port() == "1.2.3.4:51820"
    assert Server("k", "a.b:4500", None, None).endpoint_with_port() == "a.b:4500"
    assert Server("k", "2001:db8::1", None, None).endpoint_with_port() == "[2001:db8::1]:51820"


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_root_job_only_accepts_valid_lines(db: Any, tmp_path: Path) -> None:
    store = PeerStore(db, tmp_path, b"k" * 32)
    store.add("Handy")
    peers = tmp_path / "wireguard" / "peers"
    good = peers.read_text()
    pub, psk, _ = good.split("\t")
    peers.write_text(
        good
        + f"{pub}\t{psk}\t10.8.0.9/32\n"  # duplicate key
        + f"{'A' * 43}=\t{psk}\t0.0.0.0/0\n"  # would route everything to this peer
        + f"{'B' * 42}A=\t{psk}\t10.8.0.5/32\nPostUp = rm -rf /\n"
        + f"{'C' * 42}A=\t{psk}\t10.8.0.6/32\textra\n"
    )
    wg = tmp_path / "etc"
    wg.mkdir()
    (wg / "wg0.base").write_text("[Interface]\nListenPort = 51820\n")
    env = os.environ | {"PI_GUARD_WG_PEERS": str(peers), "PI_GUARD_WG_DIR": str(wg)}
    result = subprocess.run(["bash", str(APPLY), "wg0"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    conf = (wg / "wg0.conf").read_text()
    assert conf.count("[Peer]") == 2
    assert "0.0.0.0/0" not in conf and "rm -rf" not in conf and "C" * 42 not in conf
    assert "skipped 4" in result.stderr  # duplicate, 0.0.0.0/0, PostUp, extra field
    assert oct((wg / "wg0.conf").stat().st_mode & 0o777) == "0o600"


async def test_dashboard_manages_vpn_devices(tmp_path: Path) -> None:
    client, app = await _client(
        tmp_path, wireguard_enabled=True, wireguard_endpoint="home.example.net",
        wireguard_home_network="192.168.178.0/24",
    )  # fmt: skip
    try:
        csrf = await _login(client)
        headers = {"X-CSRF-Token": csrf}
        body = {"name": "Handy Anna"}
        # Without the root job's status the server key is unknown.
        response = await client.post("/api/network/wireguard/peers", json=body, headers=headers)
        assert response.status == 409
        (tmp_path / "run").mkdir(exist_ok=True)
        (tmp_path / "run" / "wireguard.status").write_text(
            "written\t2026-11-02T10:00:00Z\n(hidden)\t" + "S" * 43 + "=\t51820\toff\n"
        )
        response = await client.post("/api/network/wireguard/peers", json=body, headers=headers)
        assert response.status == 200
        peer = await response.json()
        assert peer["qr"].startswith("data:image/svg+xml")

        overview = await (await client.get("/api/network")).json()
        assert overview["wireguard"]["peers"][0]["name"] == "Handy Anna"
        assert overview["wireguard"]["peers"][0]["pending"] is True

        file = await client.get(f"/api/network/wireguard/peers/{peer['id']}/file")
        assert file.status == 200 and "attachment" in file.headers["Content-Disposition"]
        assert "Endpoint = home.example.net:51820" in await file.text()

        url = f"/api/network/wireguard/peers/{peer['id']}"
        assert (await client.delete(url, headers=headers)).status == 200
        assert (await client.get(url)).status == 404
        actions = [r[0] for r in app.db.execute("SELECT action FROM audit")]
        assert "wireguard.add" in actions and "wireguard.remove" in actions
    finally:
        await client.close()
        app.db.close()

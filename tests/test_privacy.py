"""Privacy upstream: store, compat, providers, firewall, API."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pi_guard.network.wgpeers import generate_keypair
from pi_guard.privacy.capabilities import detect
from pi_guard.privacy.chain import set_hops
from pi_guard.privacy.compat import validate_chain, validate_dns
from pi_guard.privacy.errors import PrivacyError
from pi_guard.privacy.firewall import Plan, apply_or_relaxed, render
from pi_guard.privacy.profiles import apply_profile
from pi_guard.privacy.providers.custom_wg import CustomWireGuard
from pi_guard.privacy.providers.import_wg import parse_wireguard_conf
from pi_guard.privacy.providers.mullvad import MullvadProvider
from pi_guard.privacy.service import PrivacyService
from pi_guard.privacy.store import PRIVACY_CONFIG_VERSION, PrivacyStore, migrate

from .test_web import _client, _login

REPO = Path(__file__).resolve().parent.parent
EXIT_APPLY = REPO / "deploy" / "privacy" / "pi-guard-exit-apply"


@pytest.fixture
def privacy(tmp_path: Path) -> PrivacyService:
    return PrivacyService(tmp_path, b"k" * 32, home_network="192.168.1.0/24")


def test_migrate_keeps_advanced_off() -> None:
    data = migrate({"version": 0})
    assert data["version"] == PRIVACY_CONFIG_VERSION
    assert data["enabled"] is False
    assert data["kill_switch"] == "off"
    assert data["chain"]["hops"] == []
    assert data["tor"]["enabled"] is False


def test_store_seals_and_redacts(tmp_path: Path) -> None:
    store = PrivacyStore(tmp_path, b"k" * 32)
    sealed = store.seal("1234567890123456")
    assert "1234567890123456" not in sealed
    assert store.open(sealed) == "1234567890123456"
    store.data["providers"]["mullvad"]["account_sealed"] = sealed
    store.data["providers"]["mullvad"]["private_key_sealed"] = store.seal("secret")
    store.save()
    view = store.public_view()
    assert view["providers"]["mullvad"]["has_account"] is True
    assert view["providers"]["mullvad"]["account_sealed"] is None
    assert view["providers"]["mullvad"]["private_key_sealed"] is None
    text = (tmp_path / "privacy" / "config.json").read_text()
    assert "1234567890123456" not in text
    assert "secret" not in text


def test_profiles_are_starting_points(privacy: PrivacyService) -> None:
    cfg = privacy.set_profile("enhanced")
    assert cfg["profile"] == "enhanced"
    assert cfg["kill_switch"] == "vpn"
    assert cfg["dns"]["mode"] == "vpn_routed"
    maximum = apply_profile(privacy.store.data, "maximum")
    assert maximum["kill_switch"] == "strict"
    assert maximum["dns"]["strict"] is True


def test_compat_blocks_tor_with_wg_exit() -> None:
    caps = detect()
    with pytest.raises(PrivacyError, match="tor_with_wg"):
        validate_chain(
            [{"id": "1", "provider": "custom", "params": {}}],
            caps,
            tor_mode="vpn_then_tor",
        )


def test_compat_blocks_strict_system_dns() -> None:
    with pytest.raises(PrivacyError, match="strict_system"):
        validate_dns({"mode": "system", "strict": True, "servers": []}, detect())


def test_import_rejects_postup_and_amnezia() -> None:
    private, public = generate_keypair()
    good = f"""
[Interface]
PrivateKey = {private}
Address = 10.64.0.2/32
[Peer]
PublicKey = {public}
Endpoint = 1.2.3.4:51820
AllowedIPs = 0.0.0.0/0
"""
    parsed = parse_wireguard_conf(good)
    assert parsed["endpoint"] == "1.2.3.4:51820"
    with pytest.raises(PrivacyError, match="forbidden"):
        parse_wireguard_conf(good + "\nPostUp = id\n")
    with pytest.raises(PrivacyError, match="forbidden"):
        parse_wireguard_conf(
            good.replace("AllowedIPs = 0.0.0.0/0", "Jc = 4\nAllowedIPs = 0.0.0.0/0")
        )


def test_custom_server_and_peer_file(privacy: PrivacyService) -> None:
    private, public = generate_keypair()
    CustomWireGuard().add_manual(
        privacy.store,
        {
            "name": "VPS",
            "endpoint": "203.0.113.8:51820",
            "public_key": public,
            "private_key": private,
            "address": "10.66.0.2/32",
        },
    )
    servers = privacy.store.data["providers"]["custom"]
    assert len(servers) == 1
    set_hops(
        privacy.store,
        [
            {
                "provider": "custom",
                "name": "VPS",
                "server_id": servers[0]["id"],
            }
        ],
        detect(),
    )
    (privacy.store._dir / "installed").write_text("1\n")
    privacy.activate()
    line = (privacy.store._dir / "exit.peer").read_text()
    assert public in line
    assert private in line
    assert "203.0.113.8:51820" in line
    view = privacy.config()
    dumped = json.dumps(view)
    assert private not in dumped
    assert view["providers"]["custom"][0].get("private_key_sealed") is None


def test_firewall_strict_drops_and_keeps_mgmt() -> None:
    text = render(Plan(mode="strict", exit_up=True, home_network="192.168.1.0/24"))
    assert text.count("policy drop") >= 1
    assert "192.168.1.0/24" in text
    assert "tcp dport" in text
    assert "pgexit" in text
    off = render(Plan(mode="off"))
    assert "policy drop" not in off


def test_firewall_relaxed_when_tor_uid_fails() -> None:
    calls: list[Plan] = []

    def load(plan: Plan) -> None:
        calls.append(plan)
        if plan.tor:
            raise RuntimeError("no such user")

    out = apply_or_relaxed(Plan(mode="strict", exit_up=True, tor=True), load)
    assert out.tor is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_mullvad_register_mocked(privacy: PrivacyService) -> None:
    provider = MullvadProvider()
    account = "1234567890123456"
    private, public = generate_keypair()

    async def fake_token(_: str) -> str:
        return "tok"

    class Resp:
        status = 201

        async def json(self, content_type: Any = None) -> dict[str, Any]:
            return {
                "id": "dev-1",
                "name": "happy cat",
                "ipv4_address": "10.64.0.9/32",
                "ipv6_address": "fc00::9/128",
                "pubkey": public,
            }

        async def __aenter__(self) -> Resp:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    session = MagicMock()
    session.post = MagicMock(return_value=Resp())
    provider._session = session
    with (
        patch.object(provider, "token", AsyncMock(side_effect=fake_token)),
        patch(
            "pi_guard.privacy.providers.mullvad.generate_keypair", return_value=(private, public)
        ),
    ):
        result = await provider.register_device(account, privacy.store)
    assert result["device_id"] == "dev-1"
    assert (
        privacy.store.open(privacy.store.data["providers"]["mullvad"]["account_sealed"]) == account
    )
    assert "1234567890123456" not in privacy.store.public_view().__repr__()


@pytest.mark.skipif(not EXIT_APPLY.is_file(), reason="deploy script missing")
def test_exit_apply_rejects_bad_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import subprocess

    privacy_dir = tmp_path / "privacy"
    privacy_dir.mkdir()
    conf_dir = tmp_path / "wg"
    conf_dir.mkdir()
    status = tmp_path / "status"
    status.mkdir()
    private, public = generate_keypair()
    psk = base64.b64encode(b"\x00" * 32).decode()
    # Injection attempt in endpoint
    (privacy_dir / "exit.peer").write_text(
        f"{public}\t{psk}\t1.2.3.4;reboot:51820\t0.0.0.0/0\t10.64.0.2/32\t{private}\t1280\n"
    )
    env = {
        **os.environ,
        "PI_GUARD_PRIVACY_DIR": str(privacy_dir),
        "PI_GUARD_EXIT_DIR": str(conf_dir),
        "PI_GUARD_STATUS_DIR": str(status),
    }
    result = subprocess.run(["bash", str(EXIT_APPLY)], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert not (conf_dir / "pgexit.conf").exists()


async def test_api_privacy_requires_login(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        assert (await client.get("/api/privacy/capabilities")).status == 401
    finally:
        await client.close()
        app.db.close()


async def test_api_privacy_config_and_profile(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        csrf = await _login(client)
        headers = {"X-CSRF-Token": csrf}
        caps = await client.get("/api/privacy/capabilities")
        assert caps.status == 200
        assert "features" in await caps.json()
        cfg = await client.get("/api/privacy/config")
        assert cfg.status == 200
        assert (await cfg.json())["enabled"] is False
        put = await client.put(
            "/api/privacy/profile", json={"profile": "enhanced"}, headers=headers
        )
        assert put.status == 200
        assert (await put.json())["kill_switch"] == "vpn"
        bad = await client.put(
            "/api/privacy/dns", json={"mode": "system", "strict": True}, headers=headers
        )
        assert bad.status == 400
    finally:
        await client.close()
        app.db.close()

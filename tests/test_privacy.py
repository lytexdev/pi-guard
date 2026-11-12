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


def test_diagnostics_path_includes_home_vpn(privacy: PrivacyService) -> None:
    from pi_guard.privacy.health import path_nodes, remember_exit_ip, snapshot

    path = path_nodes(privacy.store, exit_st={}, tor_st={})
    kinds = [n["kind"] for n in path]
    assert kinds[0] == "clients"
    assert kinds[1] == "home_vpn"
    assert kinds[-1] == "internet"
    remember_exit_ip("203.0.113.10", kind="public")
    snap = snapshot(privacy.store, installed=False)
    assert snap["exit_ip"] == "203.0.113.10"
    assert "web.priv_diag_need_install" in snap["notes"]
    assert snap["path"][1]["kind"] == "home_vpn"


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
    session.get = MagicMock(return_value=_Reply(200, {"expiry": "2099-01-01T00:00:00+00:00"}))
    provider._session = session
    with (
        patch.object(provider, "token", AsyncMock(side_effect=fake_token)),
        patch(
            "pi_guard.privacy.providers.mullvad.generate_keypair", return_value=(private, public)
        ),
    ):
        result = await provider.register_device(account, privacy.store)
    assert result["device_id"] == "dev-1"
    assert privacy.store.data["providers"]["mullvad"]["expiry"].startswith("2099")
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


def _run_exit_apply(tmp_path: Path, line: str) -> tuple[int, Path]:
    import os
    import subprocess

    privacy_dir, conf_dir, status = tmp_path / "privacy", tmp_path / "wg", tmp_path / "status"
    for folder in (privacy_dir, conf_dir, status):
        folder.mkdir(exist_ok=True)
    (privacy_dir / "exit.peer").write_text(line)
    env = {**os.environ, "PI_GUARD_PRIVACY_DIR": str(privacy_dir),
           "PI_GUARD_EXIT_DIR": str(conf_dir), "PI_GUARD_STATUS_DIR": str(status)}  # fmt: skip
    result = subprocess.run(["bash", str(EXIT_APPLY)], env=env, capture_output=True, text=True)
    return result.returncode, conf_dir / "pgexit.conf"


@pytest.mark.skipif(not EXIT_APPLY.is_file(), reason="deploy script missing")
@pytest.mark.parametrize(
    "endpoint", ["185.209.196.74:51820", "[2a03:1b20:3:f011::a01f]:51820", "vpn.example.org:51820"]
)
def test_exit_apply_takes_real_endpoints(tmp_path: Path, endpoint: str) -> None:
    # Every endpoint was refused once: "\]" ended the bracket in the shell pattern.
    private, public = generate_keypair()
    psk = "A" * 43 + "="
    code, conf = _run_exit_apply(
        tmp_path, f"{public}\t{psk}\t{endpoint}\t0.0.0.0/0,::/0\t10.64.0.2/32\t{private}\t1280\n"
    )
    assert code == 0 and conf.is_file()
    text = conf.read_text()
    assert f"Endpoint = {endpoint}" in text and "PresharedKey" not in text
    # VPN clients go out through the exit; the LAN and the VPN itself stay in the main table.
    for home in ("wg0", "awg0"):  # AmneziaWG devices must not pass the exit either
        assert f"iif {home} lookup main suppress_prefixlength 0" in text
        assert f"iif {home} table 51821" in text


def test_firewall_sends_vpn_clients_through_the_exit() -> None:
    text = render(Plan(mode="vpn", exit_up=True, home_network="192.168.1.0/24"))
    assert 'oifname "pgexit" masquerade' in text  # the exit knows only its own address
    vpn = '{ "wg0", "awg0" }'  # AmneziaWG carries the same devices
    assert f"iifname != {vpn} accept" in text  # the kill switch leaves containers alone
    assert f"iifname {vpn} ip daddr 192.168.1.0/24 accept" in text  # home stays reachable
    assert f'iifname {vpn} oifname "pgexit" accept' in text
    off = render(Plan(mode="off", exit_up=True))
    assert "masquerade" in off and "policy drop" not in off
    assert "masquerade" not in render(Plan(mode="vpn", exit_up=False))


class _Reply:
    def __init__(self, status: int, body: Any) -> None:
        self.status, self.body = status, body

    async def json(self, content_type: Any = None) -> Any:
        return self.body

    async def __aenter__(self) -> _Reply:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


async def test_mullvad_wrong_account_is_named() -> None:
    # Mullvad answers a wrong number with 400 INVALID_ACCOUNT, not 401 (checked 2026-11-12).
    provider = MullvadProvider()
    session = MagicMock()
    session.post = MagicMock(return_value=_Reply(400, {"code": "INVALID_ACCOUNT"}))
    provider._session = session
    with pytest.raises(PrivacyError, match="bad_account"):
        await provider.token("0000000000000000")
    session.post = MagicMock(return_value=_Reply(503, {"code": "SERVICE_UNAVAILABLE"}))
    with pytest.raises(PrivacyError, match="priv_mullvad_api"):
        await provider.token("0000000000000000")


async def test_mullvad_refuses_an_expired_account(privacy: PrivacyService) -> None:
    provider = MullvadProvider()
    session = MagicMock()
    session.get = MagicMock(return_value=_Reply(200, {"expiry": "2020-01-01T00:00:00+00:00"}))
    session.post = MagicMock()
    provider._session = session
    with (
        patch.object(provider, "token", AsyncMock(return_value="tok")),
        pytest.raises(PrivacyError, match="expired"),
    ):
        await provider.register_device("1234567890123456", privacy.store)
    session.post.assert_not_called()  # no device is used up on the account


RELAYS = [
    {"code": "de", "name": "Germany", "cities": [{"code": "fra", "name": "Frankfurt", "relays": [
        {"hostname": "de-fra-wg-004", "ipv4": "185.209.196.74", "public_key": "EXIT=",
         "multihop_port": 3155}]}]},
    {"code": "se", "name": "Sweden", "cities": [{"code": "sto", "name": "Stockholm", "relays": [
        {"hostname": "se-sto-wg-001", "ipv4": "193.138.218.220", "public_key": "ENTRY=",
         "multihop_port": 3001}]}]},
]  # fmt: skip


def test_mullvad_multihop_uses_entry_address_and_exit_port(privacy: PrivacyService) -> None:
    provider = MullvadProvider()
    mullvad = privacy.store.data["providers"]["mullvad"]
    private, _ = generate_keypair()
    mullvad.update({"device_id": "d", "ipv4_address": "10.64.0.9/32",
                    "private_key_sealed": privacy.store.seal(private)})  # fmt: skip
    # The browser's keys and ports are ignored: they come from the relay list.
    chosen = provider.select_server(
        privacy.store,
        {"hostname": "de-fra-wg-004", "entry_hostname": "se-sto-wg-001", "public_key": "FAKE"},
        RELAYS,
    )
    assert chosen["public_key"] == "EXIT=" and chosen["entry_ipv4"] == "193.138.218.220"
    peer = provider.build_peer(privacy.store)
    assert peer.endpoint == "193.138.218.220:3155" and peer.peer_public_key == "EXIT="
    single = provider.select_server(privacy.store, {"hostname": "de-fra-wg-004"}, RELAYS)
    assert single["entry_hostname"] is None
    assert provider.build_peer(privacy.store).endpoint == "185.209.196.74:51820"
    for bad in ({"hostname": "de-fra-wg-004", "entry_hostname": "de-fra-wg-004"},
                {"hostname": "de-fra-wg-004", "entry_hostname": "xx-nowhere"}):  # fmt: skip
        with pytest.raises(PrivacyError):
            provider.select_server(privacy.store, bad, RELAYS)
    # A multi-hop without what it needs is refused, never made a single hop.
    mullvad["selected"] = {**single, "entry_hostname": "se-sto-wg-001"}
    with pytest.raises(PrivacyError):
        provider.build_peer(privacy.store)


def test_two_mullvad_hops_use_the_exits_port(privacy: PrivacyService) -> None:
    from pi_guard.privacy.chain import build_active_peer

    mullvad = privacy.store.data["providers"]["mullvad"]
    private, _ = generate_keypair()
    mullvad.update({"device_id": "d", "ipv4_address": "10.64.0.9/32",
                    "private_key_sealed": privacy.store.seal(private)})  # fmt: skip
    entry = MullvadProvider.find_relay(RELAYS, "se-sto-wg-001")
    exit_relay = MullvadProvider.find_relay(RELAYS, "de-fra-wg-004")
    privacy.store.data["chain"]["hops"] = [
        {"id": "a", "provider": "mullvad", "name": "in", "params": entry},
        {"id": "b", "provider": "mullvad", "name": "out", "params": exit_relay},
    ]
    assert build_active_peer(privacy.store).endpoint == "193.138.218.220:3155"

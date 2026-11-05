"""The ways home: what the dashboard claims about them, and what it must not claim."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pi_guard.network import reach
from pi_guard.network.reach import (
    addresses,
    carried,
    endpoints,
    host_of,
    ipv4_way,
    ipv6_way,
    own_ipv6,
    relay_way,
    tailscale_way,
    with_port,
)
from pi_guard.web.security import Guard

from .test_web import _client, _login


def settings(**values: Any) -> Any:
    base = {
        "wireguard_endpoint": None,
        "wireguard_endpoint_v6": None,
        "wireguard_relay": None,
        "dashboard_public_url": None,
        "dashboard_tls": True,
        "dashboard_port": 8443,
    }
    return SimpleNamespace(**{**base, **values})


@pytest.mark.parametrize(
    ("host", "port", "expected"),
    [
        ("home.example", None, "home.example:51820"),
        ("home.example:1234", None, "home.example:1234"),
        ("home.example", "4000", "home.example:4000"),
        ("2001:db8::1", None, "[2001:db8::1]:51820"),
        ("[2001:db8::1]:9", None, "[2001:db8::1]:9"),
    ],
)
def test_endpoints_are_written_the_way_wireguard_wants(
    host: str, port: str | None, expected: str
) -> None:
    assert with_port(host, port) == expected


def test_the_host_comes_out_of_every_form() -> None:
    assert host_of("home.example:1") == "home.example"
    assert host_of("[2001:db8::1]:9") == "2001:db8::1"
    assert host_of("2001:db8::1") == "2001:db8::1"


def test_ways_keep_their_order_and_only_the_relay_lowers_the_mtu() -> None:
    ways = endpoints(settings(wireguard_endpoint="a.example", wireguard_endpoint_v6="b.example",
                              wireguard_relay="c.example:7"))  # fmt: skip
    assert [w["endpoint"] for w in ways] == ["a.example:51820", "b.example:51820", "c.example:7"]
    assert [w.get("mtu") for w in ways] == [None, None, 1340]
    assert endpoints(settings()) == []


def test_a_shared_address_is_called_out() -> None:
    way = ipv4_way("home.example", ["100.72.1.1"], None, 0)
    assert way["state"] == "warn" and way["key"] == "web.reach_v4_shared"


def test_a_name_pointing_elsewhere_is_called_out() -> None:
    way = ipv4_way("home.example", ["93.184.216.34"], "8.8.4.4", 0)
    assert way["state"] == "warn" and way["key"] == "web.reach_v4_other"


def test_a_fitting_address_is_not_claimed_to_be_proven() -> None:
    way = ipv4_way("home.example", ["93.184.216.34"], "93.184.216.34", 0)
    assert way["state"] == "ok" and way["key"] == "web.reach_v4_ok"
    assert ipv4_way("home.example", ["93.184.216.34"], None, 2)["key"] == "web.reach_seen"


def test_ipv6_must_lead_to_this_device_and_not_the_router() -> None:
    assert ipv6_way("r.example", ["2001:db8::1"], ["2001:db8::2"], 0)["key"] == "web.reach_v6_other"
    assert ipv6_way("p.example", ["2001:db8::2"], ["2001:db8::2"], 0)["state"] == "ok"
    assert ipv6_way(None, [], ["2001:db8::2"], 0)["key"] == "web.reach_v6_possible"
    assert ipv6_way(None, [], [], 0)["key"] == "web.reach_v6_none"


def test_the_relay_counts_only_with_a_live_tunnel() -> None:
    assert relay_way(None, None, 0)["state"] == "off"
    assert relay_way("v.example:1", {"peers": [{"active": False}]}, 0)["state"] == "warn"
    assert relay_way("v.example:1", {"peers": [{"active": True}]}, 0)["state"] == "ok"


def test_only_devices_from_outside_prove_a_way() -> None:
    status = {"peers": [
        {"active": True, "endpoint": "91.67.15.22:50342"},  # the home's own address, from inside
        {"active": True, "endpoint": "203.0.113.9:1"},
        {"active": True, "endpoint": "[2001:db8::9]:1"},
        {"active": True, "endpoint": "10.9.0.1:41378"},
        {"active": False, "endpoint": "203.0.113.8:1"},
    ]}  # fmt: skip
    assert carried(status, "91.67.15.22") == {"ipv4": 1, "ipv6": 1, "relay": 1}
    assert carried(None, None) == {"ipv4": 0, "ipv6": 0, "relay": 0}


def test_tailscale_states() -> None:
    assert tailscale_way(None)["state"] == "off"
    login = tailscale_way({"state": "NeedsLogin", "addresses": [], "login": "https://l.example"})
    assert login["key"] == "web.reach_ts_login" and login["login"] == "https://l.example"
    running = tailscale_way({"state": "Running", "addresses": ["100.70.0.1"], "login": None})
    assert running["state"] == "ok" and running["address"] == "100.70.0.1"
    assert tailscale_way({"state": "Stopped", "addresses": [], "login": None})["state"] == "warn"


def test_the_app_gets_addresses_only_from_a_running_tailnet() -> None:
    running = {"state": "Running", "addresses": ["100.70.0.1", "fd7a::1"], "login": None}
    assert addresses(settings(), running) == ["https://100.70.0.1:8443"]
    assert addresses(settings(), {**running, "state": "NeedsLogin"}) == []
    public = settings(dashboard_public_url="https://guard.example/")
    assert addresses(public, None) == ["https://guard.example"]


def test_reading_tailscale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reach.shutil, "which", lambda _: None)
    assert reach.tailscale() is None
    monkeypatch.setattr(reach.shutil, "which", lambda _: "/usr/bin/tailscale")
    answer = {"BackendState": "Running", "TailscaleIPs": ["100.70.0.1"], "AuthURL": ""}

    def run(*_: Any, **__: Any) -> Any:
        return SimpleNamespace(stdout=json.dumps(answer).encode())

    monkeypatch.setattr(reach.subprocess, "run", run)
    assert reach.tailscale() == {"state": "Running", "addresses": ["100.70.0.1"], "login": None}

    def stuck(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired("tailscale", 3)

    monkeypatch.setattr(reach.subprocess, "run", stuck)
    assert reach.tailscale() == {"state": "unknown", "addresses": [], "login": None}


def test_only_public_ipv6_addresses_count(tmp_path: Path) -> None:
    table = tmp_path / "if_inet6"
    table.write_text(
        "2a02810b160dfb001484ab614e589d74 02 40 00 00 wlan0\n"
        + "fdce1940b6060000783e1b00d710f62c 03 40 00 00 wlan0\n"
        + "fe800000000000000de8081ff7955592 03 40 20 80 wlan0\n"
        + "00000000000000000000000000000001 01 80 10 80 lo\n"
    )
    assert own_ipv6(table) == ["2a02:810b:160d:fb00:1484:ab61:4e58:9d74"]
    assert own_ipv6(tmp_path / "missing") == []


async def test_the_dashboard_reports_all_four_ways_to_admins(tmp_path: Path) -> None:
    client, app = await _client(tmp_path)
    try:
        assert (await client.get("/api/network/reach")).status == 401
        await _login(client)
        body = await (await client.get("/api/network/reach")).json()
        assert [w["id"] for w in body["ways"]] == ["ipv4", "ipv6", "relay", "tailscale"]
        assert body["public"] is None  # nothing was asked on the internet
    finally:
        await client.close()
        app.db.close()


def test_every_message_exists_in_both_languages() -> None:
    root = Path(reach.__file__).parents[1]
    keys = set(
        __import__("re").findall(r'"(web\.reach_[a-z0-9_]+)"', Path(reach.__file__).read_text())
    )
    for lang in ("en", "de"):
        table = json.loads((root / "i18n" / f"{lang}.json").read_text())
        assert keys <= set(table), lang


def test_a_tailnet_address_is_a_known_way_in() -> None:
    guard = object.__new__(Guard)
    guard.allowed_hosts = {"guard.example"}
    assert guard._host_allowed("100.70.0.1:8443")
    assert not guard._host_allowed("93.184.216.34:8443")

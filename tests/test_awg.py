"""AmneziaWG: the obfuscated way home next to WireGuard."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pi_guard.network import awg

REPO = Path(__file__).resolve().parent.parent
SYNC = REPO / "deploy" / "amneziawg" / "pi-guard-awg-sync"
GOOD = "port=443\njc=7\njmin=8\njmax=80\ns1=40\ns2=99\nh1=11\nh2=22\nh3=33\nh4=44\n"


def _params(tmp_path: Path, text: str = GOOD) -> Path:
    path = tmp_path / "params"
    path.write_text(text)
    return path


def test_params_are_checked(tmp_path: Path) -> None:
    expected = {"port": 443, "jc": 7, "jmin": 8, "jmax": 80, "s1": 40, "s2": 99,
                "h1": 11, "h2": 22, "h3": 33, "h4": 44}  # fmt: skip
    assert awg.read(_params(tmp_path)) == expected
    assert awg.read(tmp_path / "missing") is None
    for wrong in (
        ("jmin=8", "jmin=90"),
        ("s2=99", "s2=96"),
        ("h2=22", "h2=11"),
        ("jc=7", "jc=0"),
        ("port=443", "port=x"),
        ("h4=44\n", ""),
    ):
        assert awg.read(_params(tmp_path, GOOD.replace(*wrong))) is None, wrong


def test_obfuscated_ways_use_the_same_addresses(tmp_path: Path) -> None:
    params = awg.read(_params(tmp_path))
    plain = [{"kind": "ipv4", "endpoint": "home.example:51820"},
             {"kind": "ipv6", "endpoint": "[2001:db8::5]:51820"},
             {"kind": "relay", "endpoint": "relay.example:51820", "mtu": 1340}]  # fmt: skip
    hidden = awg.ways(plain, params)
    assert [w["endpoint"] for w in hidden] == ["home.example:443", "[2001:db8::5]:443"]
    assert hidden[0]["awg"] == {"Jc": 7, "Jmin": 8, "Jmax": 80, "S1": 40, "S2": 99,
                                "H1": 11, "H2": 22, "H3": 33, "H4": 44}  # fmt: skip
    assert awg.ways(plain, None) == []
    assert awg.way(None, "home.example", False)["state"] == "off"
    shown = awg.way(params, "home.example:51820", True)
    assert shown["state"] == "ok" and shown["host"] == "home.example:443"


def _tool(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text(f'#!/bin/bash\necho "{name} $*" >> {folder}/calls\n{body}\n')
    path.chmod(0o755)


@pytest.mark.skipif(not SYNC.is_file(), reason="deploy script missing")
def test_sync_mirrors_devices_and_routes_by_the_newer_greeting(tmp_path: Path) -> None:
    a, b, c = ("A" * 42 + "A=", "B" * 42 + "A=", "C" * 42 + "A=")
    psk_a, psk_b = ("a" * 42 + "A=", "b" * 42 + "A=")
    # wg0 has A (last seen on awg0) and B (on wg0); awg0 still has C, which was removed.
    _tool(
        tmp_path,
        "wg",
        f"""case "$3" in
  preshared-keys) printf '{a}\\t{psk_a}\\n{b}\\t{psk_b}\\n' ;;
  allowed-ips) printf '{a}\\t10.8.0.2/32\\n{b}\\t10.8.0.3/32\\n' ;;
  latest-handshakes) printf '{a}\\t100\\n{b}\\t500\\n' ;;
esac""",
    )
    keep = f'[[ "$1" == set && "$5" == preshared-key ]] && cat "$6" >> {tmp_path}/psk'
    _tool(
        tmp_path,
        "awg",
        f"""{keep}
case "$3" in
  preshared-keys) printf '{a}\\t{psk_a}\\n{c}\\t{psk_a}\\n' ;;
  allowed-ips) printf '{a}\\t10.8.0.2/32\\n{c}\\t10.8.0.9/32\\n' ;;
  latest-handshakes) printf '{a}\\t200\\n{c}\\t50\\n' ;;
esac""",
    )
    _tool(
        tmp_path,
        "ip",
        """[[ "$2" == show && "$3" == 10.8.0.3 ]] && echo "10.8.0.3 dev awg0"
exit 0""",
    )
    tools = {f"PI_GUARD_{n.upper()}_TOOL": str(tmp_path / n) for n in ("wg", "awg", "ip")}
    env = {**os.environ, "PI_GUARD_AWG_ONCE": "1", **tools}
    result = subprocess.run(["bash", str(SYNC)], env=env, capture_output=True, text=True, timeout=9)
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text()
    assert f"awg set awg0 peer {b} preshared-key /dev/fd/" in calls
    assert "allowed-ips 10.8.0.3/32" in calls
    assert psk_b not in calls and (tmp_path / "psk").read_text().strip() == psk_b  # never in argv
    assert f"awg set awg0 peer {a} " not in calls  # already equal
    assert "ip route replace 10.8.0.2/32 dev awg0" in calls  # greeted awg0 last
    assert "ip route del 10.8.0.3/32 dev awg0" in calls  # back on wg0
    assert f"awg set awg0 peer {c} remove" in calls and "ip route del 10.8.0.9/32 dev awg0" in calls


async def test_the_app_gets_obfuscated_ways(tmp_path: Path, monkeypatch: Any) -> None:
    from .test_mobile import _paired, _with_vpn

    client, app, _, bearer = await _paired(tmp_path)
    try:
        await _with_vpn(tmp_path, app)
        body = await (await client.post("/api/mobile/vpn", json={}, headers=bearer)).json()
        assert body["obfuscated"] == []
        monkeypatch.setattr(awg, "PARAMS", _params(tmp_path))
        body = await (await client.post("/api/mobile/vpn", json={}, headers=bearer)).json()
        assert body["ways"][0]["endpoint"] == "home.example:51820"  # untouched for older apps
        assert body["obfuscated"][0]["endpoint"] == "home.example:443"
    finally:
        await client.close()
        app.db.close()

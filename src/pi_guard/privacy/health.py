"""Lightweight privacy diagnostics. No secrets, no heavy probes by default."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

STATUS_DIR = Path("/run/pi-guard-status")

# Last successful public/exit IP probe in this process (not a secret).
_exit_ip_cache: dict[str, Any] = {"ip": None, "at": 0.0, "kind": None}
EXIT_IP_TTL = 300.0


def read_status(name: str) -> dict[str, str]:
    path = STATUS_DIR / name
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def remember_exit_ip(ip: str | None, *, kind: str = "exit") -> None:
    if not ip:
        return
    _exit_ip_cache["ip"] = ip
    _exit_ip_cache["at"] = time.monotonic()
    _exit_ip_cache["kind"] = kind


def cached_exit_ip() -> tuple[str | None, str | None, int | None]:
    ip = _exit_ip_cache.get("ip")
    at = float(_exit_ip_cache.get("at") or 0)
    if not ip or (time.monotonic() - at) > EXIT_IP_TTL:
        return None, None, None
    age = int(time.monotonic() - at)
    return str(ip), str(_exit_ip_cache.get("kind") or "exit"), age


def resources() -> dict[str, Any]:
    mem_total = mem_avail = None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    load1 = None
    with contextlib.suppress(OSError, ValueError, IndexError):
        load1 = float(Path("/proc/loadavg").read_text(encoding="utf-8").split()[0])
    return {
        "load1": load1,
        "memory_total": mem_total,
        "memory_available": mem_avail,
        "pid": os.getpid(),
    }


def path_nodes(
    store: Any, *, exit_st: dict[str, str], tor_st: dict[str, str]
) -> list[dict[str, Any]]:
    """Full traffic path for the chain visualizer, always including the home VPN hop."""
    data = store.public_view()
    chain_on = bool(data.get("enabled")) and bool(data["chain"].get("active"))
    tor_on = bool(data.get("tor", {}).get("enabled"))
    hops = [h for h in (data["chain"].get("hops") or []) if h.get("enabled") is not False]
    nodes: list[dict[str, Any]] = [
        {
            "id": "clients",
            "kind": "clients",
            "label": "clients",
            "title_key": "web.priv_hop_clients",
            "detail_key": "web.priv_hop_clients_detail",
            "status": "ok",
            "fixed": True,
        },
        {
            "id": "home",
            "kind": "home_vpn",
            "label": "Pi-Guard",
            "title_key": "web.priv_hop_home",
            "detail_key": "web.priv_hop_home_detail",
            "status": "ok",
            "fixed": True,
            "iface": "wg0",
        },
    ]
    for hop in hops:
        nodes.append(
            {
                "id": str(hop.get("id")),
                "kind": "exit",
                "label": hop.get("name") or hop.get("provider"),
                "provider": hop.get("provider"),
                "title_key": "web.priv_hop_exit",
                "status": (
                    "ok"
                    if chain_on and exit_st.get("up") == "1"
                    else ("pending" if chain_on else "idle")
                ),
                "fixed": False,
                "endpoint": exit_st.get("endpoint") if chain_on else None,
            }
        )
    if tor_on:
        nodes.append(
            {
                "id": "tor",
                "kind": "tor",
                "label": "Tor",
                "title_key": "web.priv_hop_tor",
                "detail_key": "web.priv_hop_tor_detail",
                "status": "ok" if tor_st.get("running") == "1" else "pending",
                "fixed": False,
                "mode": data.get("tor", {}).get("mode"),
            }
        )
    exit_ok = chain_on and exit_st.get("up") == "1"
    tor_ok = tor_on and tor_st.get("running") == "1"
    internet_ok = exit_ok or tor_ok or not (chain_on or tor_on)
    nodes.append(
        {
            "id": "internet",
            "kind": "internet",
            "label": "Internet",
            "title_key": "web.priv_hop_internet",
            "detail_key": "web.priv_hop_internet_detail",
            "status": "ok" if internet_ok else "pending",
            "fixed": True,
        }
    )
    return nodes


def snapshot(store: Any, *, exit_ip: str | None = None, installed: bool = False) -> dict[str, Any]:
    exit_st = read_status("privacy.exit")
    nft_st = read_status("privacy.nft")
    tor_st = read_status("privacy.tor")
    data = store.public_view()
    hops = data["chain"].get("hops") or []
    layers = ["home_vpn"]
    if data.get("enabled") and data["chain"].get("active"):
        layers.append("multi_hop")
    if data.get("kill_switch") not in (None, "off"):
        layers.append(f"kill_switch:{data['kill_switch']}")
    if data.get("dns", {}).get("mode") not in (None, "system"):
        layers.append(f"dns:{data['dns']['mode']}")
    if data.get("tor", {}).get("enabled"):
        layers.append(f"tor:{data['tor']['mode']}")
    if data.get("obfuscation", {}).get("mode") not in (None, "off"):
        layers.append(f"obfs:{data['obfuscation']['mode']}")

    cached_ip, cached_kind, cached_age = cached_exit_ip()
    shown_ip = exit_ip or cached_ip
    notes: list[str] = []
    if not installed:
        notes.append("web.priv_diag_need_install")
    if not exit_st and installed:
        notes.append("web.priv_diag_no_exit_status")
    if shown_ip is None:
        notes.append("web.priv_diag_need_check")

    return {
        "time": int(time.time()),
        "enabled": bool(data.get("enabled")),
        "installed": installed,
        "profile": data.get("profile"),
        "layers": layers,
        "path": path_nodes(store, exit_st=exit_st, tor_st=tor_st),
        "chain_active": bool(data["chain"].get("active")),
        "hops": [
            {
                "id": h.get("id"),
                "name": h.get("name"),
                "provider": h.get("provider"),
                "enabled": h.get("enabled") is not False,
            }
            for h in hops
        ],
        "exit": {
            "up": exit_st.get("up") == "1",
            "handshake_age": _int(exit_st.get("handshake_age")),
            "endpoint": exit_st.get("endpoint"),
            "transfer_rx": _int(exit_st.get("rx")),
            "transfer_tx": _int(exit_st.get("tx")),
            "has_status": bool(exit_st),
        },
        "firewall": {
            "mode": nft_st.get("mode") or data.get("kill_switch") or "off",
            "applied": nft_st.get("applied") == "1",
            "has_status": bool(nft_st),
        },
        "dns": {
            "mode": data.get("dns", {}).get("mode"),
            "strict": bool(data.get("dns", {}).get("strict")),
        },
        "tor": {
            "enabled": bool(data.get("tor", {}).get("enabled")),
            "running": tor_st.get("running") == "1",
            "bootstrap": _int(tor_st.get("bootstrap")),
            "has_status": bool(tor_st),
        },
        "exit_ip": shown_ip,
        "exit_ip_kind": (
            "exit" if data.get("enabled") and data["chain"].get("active") else "public"
        )
        if shown_ip
        else None,
        "exit_ip_age": 0 if exit_ip else cached_age,
        "exit_ip_cached": exit_ip is None and cached_ip is not None,
        "notes": notes,
        "resources": resources(),
        "cached_kind": cached_kind,
    }


def _int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None

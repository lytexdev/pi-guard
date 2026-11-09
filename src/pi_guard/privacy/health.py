"""Lightweight privacy diagnostics. No secrets, no heavy probes by default."""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

STATUS_DIR = Path("/run/pi-guard-status")


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


def snapshot(store: Any, *, exit_ip: str | None = None) -> dict[str, Any]:
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
    return {
        "time": int(time.time()),
        "enabled": bool(data.get("enabled")),
        "profile": data.get("profile"),
        "layers": layers,
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
        },
        "firewall": {
            "mode": nft_st.get("mode") or data.get("kill_switch") or "off",
            "applied": nft_st.get("applied") == "1",
        },
        "dns": {
            "mode": data.get("dns", {}).get("mode"),
            "strict": bool(data.get("dns", {}).get("strict")),
        },
        "tor": {
            "enabled": bool(data.get("tor", {}).get("enabled")),
            "running": tor_st.get("running") == "1",
            "bootstrap": _int(tor_st.get("bootstrap")),
        },
        "exit_ip": exit_ip,
        "resources": resources(),
    }


def _int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None

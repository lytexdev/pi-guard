"""The obfuscated way home: AmneziaWG (awg0) next to WireGuard (wg0).

`scripts/amneziawg.sh` sets it up as root and leaves the port and the disguise in a file pi-guard
may read. The values are not secret: they only make the packets look different, the keys stay
WireGuard's. Apps get them as extra ways, tried when the normal ones do not answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pi_guard.network.reach import host_of, with_port

PARAMS = Path("/var/lib/pi-guard-awg/params")
INTERFACE = Path("/sys/class/net/awg0")
# What AmneziaWG 1.0 accepts (amneziawg-linux-kernel-module README).
LIMITS = {
    "port": (1, 65535), "jc": (1, 128), "jmin": (0, 1279), "jmax": (1, 1280),
    "s1": (0, 1132), "s2": (0, 1188),
    "h1": (5, 2**32 - 1), "h2": (5, 2**32 - 1), "h3": (5, 2**32 - 1), "h4": (5, 2**32 - 1),
}  # fmt: skip
# The names in a WireGuard-style [Interface] section, as the apps write them.
CONFIG_NAMES = {"jc": "Jc", "jmin": "Jmin", "jmax": "Jmax", "s1": "S1", "s2": "S2",
                "h1": "H1", "h2": "H2", "h3": "H3", "h4": "H4"}  # fmt: skip


def read(path: Path | None = None) -> dict[str, int] | None:
    """The port and disguise, or None when missing or not what AmneziaWG accepts."""
    try:
        text = (path or PARAMS).read_text(encoding="utf-8")
    except OSError:
        return None
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, raw = line.partition("=")
        if key in LIMITS and raw.isdigit():
            values[key] = int(raw)
    if set(values) != set(LIMITS):
        return None
    if any(not low <= values[k] <= high for k, (low, high) in LIMITS.items()):
        return None
    headers = [values[k] for k in ("h1", "h2", "h3", "h4")]
    if values["jmin"] >= values["jmax"] or values["s1"] + 56 == values["s2"]:
        return None
    if len(set(headers)) != 4:
        return None
    return values


def running() -> bool:
    return INTERFACE.exists()


def ways(plain: list[dict[str, Any]], params: dict[str, int] | None) -> list[dict[str, Any]]:
    """The same addresses as the normal ways, on AmneziaWG's port and with its disguise.

    A relay server forwards only WireGuard's port, so it has no obfuscated way.
    """
    if not params:
        return []
    disguise = {CONFIG_NAMES[k]: params[k] for k in CONFIG_NAMES}
    hidden = []
    for plain_way in plain:
        if plain_way["kind"] in {"ipv4", "ipv6"}:
            endpoint = with_port(host_of(plain_way["endpoint"]), params["port"])
            hidden.append({"kind": plain_way["kind"], "endpoint": endpoint, "awg": disguise})
    return hidden


def way(params: dict[str, int] | None, host: str | None, up: bool) -> dict[str, Any]:
    """The obfuscated way for the dashboard's list of ways home."""
    item: dict[str, Any] = {"id": "awg", "host": None, "used": 0}
    if not params:
        return {**item, "state": "off", "key": "web.reach_awg_off"}
    item["port"] = params["port"]
    item["host"] = with_port(host_of(host), params["port"]) if host else None
    if not up:
        return {**item, "state": "warn", "key": "web.reach_awg_down"}
    return {**item, "state": "ok", "key": "web.reach_awg_ok"}

"""The ways home from outside: IPv4, IPv6, a rented relay server and Tailscale.

What can be seen from inside is reported as such. Only a device that really connected
from outside proves a way works, so the wording never promises more than that.
"""

from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from pi_guard.config.settings import Settings

CGNAT = ipaddress.ip_network("100.64.0.0/10")  # shared by a provider, also used by Tailscale
RELAY_ADDRESS = "10.9.0.1"  # the relay server as the Pi sees it; see scripts/relay.sh
RELAY_MTU = 1340  # two WireGuard layers on the way
DEFAULT_PORT = 51820
TAILSCALE_TIMEOUT = 3


def with_port(host: str, port: str | int | None = None) -> str:
    """`host:port` as WireGuard wants it, keeping a port that was given."""
    if host.startswith("[") and "]:" in host:
        return host
    if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
        return host
    if ":" in host and not host.startswith("["):  # bare IPv6 address
        host = f"[{host}]"
    return f"{host}:{port or DEFAULT_PORT}"


def host_of(endpoint: str) -> str:
    if endpoint.startswith("["):
        return endpoint[1:].split("]", 1)[0]
    if endpoint.count(":") == 1:
        return endpoint.split(":", 1)[0]
    return endpoint


def endpoints(s: Settings, port: str | int | None = None) -> list[dict[str, Any]]:
    """Everything a VPN client can try, in that order."""
    ways: list[dict[str, Any]] = []
    if s.wireguard_endpoint:
        ways.append({"kind": "ipv4", "endpoint": with_port(s.wireguard_endpoint, port)})
    if s.wireguard_endpoint_v6:
        ways.append({"kind": "ipv6", "endpoint": with_port(s.wireguard_endpoint_v6, port)})
    if s.wireguard_relay:
        ways.append({"kind": "relay", "endpoint": with_port(s.wireguard_relay), "mtu": RELAY_MTU})
    return ways


def tailscale() -> dict[str, Any] | None:
    """None without Tailscale. The login link is for the dashboard only, never for logs."""
    program = shutil.which("tailscale")
    if program is None:
        return None
    try:
        done = subprocess.run(
            [program, "status", "--json"], capture_output=True, timeout=TAILSCALE_TIMEOUT,
            check=False,
        )  # fmt: skip
        data = json.loads(done.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"state": "unknown", "addresses": [], "login": None}
    return {
        "state": str(data.get("BackendState", "unknown")),
        "addresses": [str(a) for a in data.get("TailscaleIPs") or []],
        "login": data.get("AuthURL") or None,
    }


def addresses(s: Settings, net: dict[str, Any] | None) -> list[str]:
    """Other addresses of the dashboard, for when the home network is out of reach."""
    found = [s.dashboard_public_url.rstrip("/")] if s.dashboard_public_url else []
    if net and net["state"] == "Running":
        scheme = "https" if s.dashboard_tls else "http"
        found += [f"{scheme}://{a}:{s.dashboard_port}" for a in net["addresses"] if "." in a]
    return found


def own_ipv6(path: Path = Path("/proc/net/if_inet6")) -> list[str]:
    """This device's addresses that the internet could reach."""
    found = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        raw = line.split()[0]
        address = ipaddress.ip_address(":".join(raw[i : i + 4] for i in range(0, 32, 4)))
        if address.is_global:
            found.append(str(address))
    return found


def resolve(endpoint: str) -> tuple[list[str], list[str]]:
    """The IPv4 and IPv6 addresses behind a name. Blocks."""
    if not endpoint:
        return [], []
    try:
        rows = socket.getaddrinfo(host_of(endpoint), None, type=socket.SOCK_DGRAM)
    except OSError:
        return [], []
    found = {str(row[4][0]).split("%")[0] for row in rows}
    return (sorted(a for a in found if ":" not in a), sorted(a for a in found if ":" in a))


def carried(status: dict[str, Any] | None, public: str | None) -> dict[str, int]:
    """Devices connected right now per way. Coming from the home's own address proves nothing."""
    used = {"ipv4": 0, "ipv6": 0, "relay": 0}
    for peer in (status or {}).get("peers", []):
        if not peer.get("active") or not peer.get("endpoint"):
            continue
        address = peer["endpoint"].rsplit(":", 1)[0].strip("[]")
        if address == RELAY_ADDRESS:
            used["relay"] += 1
        elif address != public:
            used["ipv6" if ":" in address else "ipv4"] += 1
    return used


def ipv4_way(
    endpoint: str | None, found: list[str], public: str | None, used: int
) -> dict[str, Any]:
    way: dict[str, Any] = {"id": "ipv4", "host": endpoint, "used": used}
    if not endpoint:
        return {**way, "state": "off", "key": "web.reach_v4_off"}
    if not found:
        return {**way, "state": "warn", "key": "web.reach_v4_no_address"}
    address = ipaddress.ip_address(found[0])
    if address in CGNAT or address.is_private:
        return {**way, "state": "warn", "key": "web.reach_v4_shared", "address": found[0]}
    if public and public not in found:
        return {**way, "state": "warn", "key": "web.reach_v4_other", "address": found[0],
                "public": public}  # fmt: skip
    return {**way, "state": "ok", "address": found[0],
            "key": "web.reach_seen" if used else "web.reach_v4_ok"}  # fmt: skip


def ipv6_way(endpoint: str | None, found: list[str], own: list[str], used: int) -> dict[str, Any]:
    way: dict[str, Any] = {"id": "ipv6", "host": endpoint, "used": used}
    if not endpoint:
        if own:
            return {**way, "state": "off", "key": "web.reach_v6_possible", "address": own[0]}
        return {**way, "state": "off", "key": "web.reach_v6_none"}
    if not found:
        return {**way, "state": "warn", "key": "web.reach_v6_no_address"}
    if not set(found) & set(own):
        return {**way, "state": "warn", "key": "web.reach_v6_other", "address": found[0]}
    return {**way, "state": "ok", "address": found[0],
            "key": "web.reach_seen" if used else "web.reach_v6_ok"}  # fmt: skip


def relay_way(endpoint: str | None, status: dict[str, Any] | None, used: int) -> dict[str, Any]:
    way: dict[str, Any] = {"id": "relay", "host": endpoint, "used": used}
    if not endpoint:
        return {**way, "state": "off", "key": "web.reach_relay_off"}
    peers = (status or {}).get("peers", [])
    if not any(p.get("active") for p in peers):
        return {**way, "state": "warn", "key": "web.reach_relay_down"}
    return {**way, "state": "ok", "key": "web.reach_seen" if used else "web.reach_relay_ok"}


def tailscale_way(net: dict[str, Any] | None) -> dict[str, Any]:
    way: dict[str, Any] = {"id": "tailscale", "host": None, "used": 0}
    if net is None:
        return {**way, "state": "off", "key": "web.reach_ts_off"}
    if net["state"] == "Running":
        return {**way, "state": "ok", "key": "web.reach_ts_ok",
                "address": next(iter(net["addresses"]), None)}  # fmt: skip
    if net["state"] == "NeedsLogin":
        return {**way, "state": "warn", "key": "web.reach_ts_login", "login": net["login"]}
    return {**way, "state": "warn", "key": "web.reach_ts_stopped"}

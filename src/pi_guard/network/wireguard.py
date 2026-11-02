"""WireGuard status from a file written by a small root job.

Reading WireGuard state needs CAP_NET_ADMIN, which the pi-guard service does
not have. `deploy/wireguard/pi-guard-wg-status` runs as root on a timer and
writes `wg show <interface> dump` (without private keys) plus PiVPN client
names to /run/pi-guard/wireguard.status.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HANDSHAKE_ACTIVE_SECONDS = 180


@dataclass(frozen=True)
class Peer:
    name: str | None
    public_key: str
    endpoint: str | None
    allowed_ips: str
    latest_handshake: str | None
    received: int
    sent: int
    active: bool


def parse_status(text: str, now: datetime | None = None) -> dict[str, Any]:
    """Format: `name <client> <pubkey>` lines, then the `wg show dump` lines."""
    now = now or datetime.now(UTC)
    names: dict[str, str] = {}
    interface: dict[str, Any] = {}
    peers: list[Peer] = []
    written = None
    for line in text.splitlines():
        parts = line.split("\t") if "\t" in line else line.split()
        if not parts:
            continue
        if parts[0] == "written" and len(parts) == 2:
            written = parts[1]
        elif parts[0] == "name" and len(parts) >= 3:
            names[parts[2]] = parts[1]
        elif len(parts) == 4 and not interface:
            # interface line: private-key public-key listen-port fwmark (key redacted by the job)
            interface = {"public_key": parts[1], "listen_port": parts[2]}
        elif len(parts) == 8:
            pubkey, _psk, endpoint, allowed, handshake, rx, tx, _keepalive = parts
            seconds = int(handshake) if handshake.isdigit() else 0
            when = datetime.fromtimestamp(seconds, UTC) if seconds else None
            peers.append(Peer(
                names.get(pubkey), pubkey, None if endpoint == "(none)" else endpoint, allowed,
                when.isoformat() if when else None, int(rx), int(tx),
                bool(when and (now - when).total_seconds() < HANDSHAKE_ACTIVE_SECONDS),
            ))  # fmt: skip
    return {"interface": interface, "peers": [asdict(p) for p in peers], "written": written}


def read_status(path: Path) -> dict[str, Any] | None:
    try:
        return parse_status(path.read_text())
    except OSError:
        return None

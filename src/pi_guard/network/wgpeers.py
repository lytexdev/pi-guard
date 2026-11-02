"""WireGuard access for phones and other devices, managed from the dashboard.

pi-guard runs without root, so it never touches the WireGuard interface itself.
It keeps the clients in its database and writes their public data (public key,
preshared key, tunnel address) to `<data dir>/wireguard/peers`. A root job
(`deploy/wireguard/pi-guard-wg-apply`, started by a systemd path unit) checks
every line and loads the result into the interface.

Client private keys are stored encrypted (AES-GCM, key derived from the dashboard
secret key), so a device's QR code can be shown again later.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.security.files import atomic_write
from pi_guard.storage.db import Database

# Fixed, so the root job can check addresses without reading pi-guard's settings.
SUBNET = ipaddress.ip_network("10.8.0.0/24")
SERVER_ADDRESS = ipaddress.ip_address("10.8.0.1")
DEFAULT_PORT = 51820
PEERS_FILE = Path("wireguard") / "peers"
MAX_NAME = 32

_TABLE = """
CREATE TABLE IF NOT EXISTS wg_peers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    address TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL UNIQUE,
    private_key TEXT NOT NULL,
    preshared_key TEXT NOT NULL,
    full_tunnel INTEGER NOT NULL DEFAULT 1,
    created TEXT NOT NULL
);
"""


class PeerError(ValueError):
    """Message is a translation key for the dashboard."""


@dataclass(frozen=True)
class Server:
    public_key: str
    endpoint: str  # host or host:port
    dns: str | None
    home_network: str | None

    def endpoint_with_port(self) -> str:
        host = self.endpoint
        if host.startswith("[") and "]:" in host:
            return host
        if host.count(":") == 1 and host.rsplit(":", 1)[1].isdigit():
            return host
        if ":" in host:  # bare IPv6 address
            host = f"[{host}]"
        return f"{host}:{DEFAULT_PORT}"


def generate_keypair() -> tuple[str, str]:
    """(private, public) as WireGuard base64 strings."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    key = X25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    private = key.private_bytes_raw()
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def check_name(name: str) -> str:
    name = " ".join(name.split())
    if not 1 <= len(name) <= MAX_NAME or not all(c.isprintable() for c in name):
        raise PeerError("web.wg_bad_name")
    return name


class PeerStore:
    def __init__(self, db: Database, data_dir: Path, secret_key: bytes) -> None:
        self._db = db
        self._file = data_dir / PEERS_FILE
        self._key = hashlib.sha256(b"pi-guard wireguard key\0" + secret_key).digest()
        db.executescript(_TABLE)

    # sealed values ---------------------------------------------------------------------

    def _seal(self, value: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(12)
        sealed = AESGCM(self._key).encrypt(nonce, value.encode(), b"pi-guard-wg")
        return base64.b64encode(nonce + sealed).decode()

    def _open(self, value: str) -> str | None:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.b64decode(value)
        try:
            return AESGCM(self._key).decrypt(raw[:12], raw[12:], b"pi-guard-wg").decode()
        except InvalidTag:
            return None  # the dashboard secret key changed

    # peers -----------------------------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT id, name, address, public_key, full_tunnel, created FROM wg_peers ORDER BY name"
        ).fetchall()
        return [{**dict(r), "full_tunnel": bool(r["full_tunnel"])} for r in rows]

    def _free_address(self) -> str:
        taken = {r[0] for r in self._db.execute("SELECT address FROM wg_peers")}
        for host in SUBNET.hosts():
            if host != SERVER_ADDRESS and str(host) not in taken:
                return str(host)
        raise PeerError("web.wg_full")

    def add(self, name: str, full_tunnel: bool = True) -> int:
        name = check_name(name)
        if self._db.execute("SELECT 1 FROM wg_peers WHERE name = ?", (name,)).fetchone():
            raise PeerError("web.wg_name_taken")
        private, public = generate_keypair()
        psk = base64.b64encode(secrets.token_bytes(32)).decode()
        cursor = self._db.execute(
            "INSERT INTO wg_peers (name, address, public_key, private_key, preshared_key, "
            "full_tunnel, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, self._free_address(), public, self._seal(private), self._seal(psk),
             int(full_tunnel), utcnow().isoformat()),
        )  # fmt: skip
        self.write_file()
        return int(cursor.lastrowid or 0)

    def remove(self, peer_id: int) -> str | None:
        row = self._db.execute("SELECT name FROM wg_peers WHERE id = ?", (peer_id,)).fetchone()
        if row is None:
            return None
        self._db.execute("DELETE FROM wg_peers WHERE id = ?", (peer_id,))
        self.write_file()
        return str(row["name"])

    def name(self, peer_id: int) -> str | None:
        row = self._db.execute("SELECT name FROM wg_peers WHERE id = ?", (peer_id,)).fetchone()
        return str(row["name"]) if row else None

    def write_file(self) -> None:
        """Public part for the root job: `public-key<TAB>preshared-key<TAB>address/32`."""
        lines = []
        for row in self._db.execute(
            "SELECT public_key, preshared_key, address FROM wg_peers ORDER BY id"
        ):
            psk = self._open(row["preshared_key"])
            if psk:
                lines.append(f"{row['public_key']}\t{psk}\t{row['address']}/32\n")
        self._file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(self._file, "".join(lines).encode())

    def client_config(self, peer_id: int, server: Server) -> str | None:
        row = self._db.execute("SELECT * FROM wg_peers WHERE id = ?", (peer_id,)).fetchone()
        if row is None:
            return None
        private, psk = self._open(row["private_key"]), self._open(row["preshared_key"])
        if not private or not psk:
            raise PeerError("web.wg_key_lost")
        if row["full_tunnel"] or not server.home_network:
            routes = "0.0.0.0/0, ::/0"
        else:
            routes = f"{SUBNET}, {server.home_network}"
        lines = [
            "[Interface]",
            f"# {row['name']} (pi-guard)",
            f"PrivateKey = {private}",
            f"Address = {row['address']}/32",
        ]
        if server.dns:
            lines.append(f"DNS = {server.dns}")
        lines += [
            "",
            "[Peer]",
            f"PublicKey = {server.public_key}",
            f"PresharedKey = {psk}",
            f"Endpoint = {server.endpoint_with_port()}",
            f"AllowedIPs = {routes}",
            "PersistentKeepalive = 25",
        ]
        return "\n".join(lines) + "\n"

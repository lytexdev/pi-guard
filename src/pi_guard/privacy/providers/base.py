"""Provider adapter interface for upstream exits."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExitPeer:
    """One WireGuard peer used as the Pi's upstream exit (interface pgexit)."""

    interface_address: str  # e.g. 10.64.0.2/32
    private_key: str
    peer_public_key: str
    endpoint: str  # host:port
    allowed_ips: str = "0.0.0.0/0, ::/0"
    preshared_key: str | None = None
    dns: str | None = None
    mtu: int | None = 1280
    listen_port: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def conf_text(self) -> str:
        lines = [
            "[Interface]",
            f"PrivateKey = {self.private_key}",
            f"Address = {self.interface_address}",
        ]
        if self.dns:
            lines.append(f"DNS = {self.dns}")
        if self.mtu:
            lines.append(f"MTU = {self.mtu}")
        if self.listen_port:
            lines.append(f"ListenPort = {self.listen_port}")
        lines += [
            "",
            "[Peer]",
            f"PublicKey = {self.peer_public_key}",
            f"Endpoint = {self.endpoint}",
            f"AllowedIPs = {self.allowed_ips}",
            "PersistentKeepalive = 25",
        ]
        if self.preshared_key:
            lines.insert(-2, f"PresharedKey = {self.preshared_key}")
        return "\n".join(lines) + "\n"

    def peer_file_line(self) -> str:
        """Line for the root job: fields the job whitelist-checks."""
        # All-zero key = "no PSK" sentinel for the root job (still a valid WG key shape).
        psk = self.preshared_key or ("A" * 43 + "=")
        mtu = self.mtu or 1280
        return (
            f"{self.peer_public_key}\t{psk}\t{self.endpoint}\t"
            f"{self.allowed_ips.replace(' ', '')}\t{self.interface_address}\t"
            f"{self.private_key}\t{mtu}\n"
        )


class Provider(Protocol):
    id: str

    def capabilities(self) -> dict[str, bool]: ...

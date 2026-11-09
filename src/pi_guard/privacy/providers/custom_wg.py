"""Custom and imported WireGuard exit servers."""

from __future__ import annotations

import re
import secrets
import uuid
from typing import Any

from pi_guard.network.wgpeers import generate_keypair
from pi_guard.privacy.errors import PrivacyError
from pi_guard.privacy.providers.base import ExitPeer
from pi_guard.privacy.providers.import_wg import parse_wireguard_conf, valid_wg_key

_ENDPOINT_RE = re.compile(
    r"^(?:(?:\[[0-9a-fA-F:]+\]|[0-9a-fA-F:]+|[A-Za-z0-9.-]+))(?::(\d{1,5}))?$"
)
MAX_CUSTOM = 20


class CustomWireGuard:
    id = "custom"

    def capabilities(self) -> dict[str, bool]:
        return {"manual": True, "import": True, "multihop_native": False}

    def add_manual(self, store: Any, fields: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(str(fields.get("name") or "").split())
        if not 1 <= len(name) <= 64:
            raise PrivacyError("web.priv_server_name")
        endpoint = str(fields.get("endpoint") or "").strip()
        if not _ENDPOINT_RE.match(endpoint):
            raise PrivacyError("web.priv_endpoint")
        port = endpoint.rsplit(":")[-1] if ":" in endpoint and not endpoint.count(":") > 1 else ""
        if port.isdigit() and not 1 <= int(port) <= 65535:
            raise PrivacyError("web.priv_endpoint")
        public_key = str(fields.get("public_key") or "").strip()
        if not valid_wg_key(public_key):
            raise PrivacyError("web.priv_conf_keyfmt")
        private_key = str(fields.get("private_key") or "").strip()
        if private_key:
            if not valid_wg_key(private_key):
                raise PrivacyError("web.priv_conf_keyfmt")
        else:
            private_key, _ = generate_keypair()
        address = str(fields.get("address") or "").strip()
        if not address:
            raise PrivacyError("web.priv_address")
        psk = str(fields.get("preshared_key") or "").strip() or None
        if psk and not valid_wg_key(psk):
            raise PrivacyError("web.priv_conf_keyfmt")
        allowed = str(fields.get("allowed_ips") or "0.0.0.0/0, ::/0").strip()
        dns = str(fields.get("dns") or "").strip() or None
        try:
            mtu = int(fields["mtu"]) if fields.get("mtu") not in (None, "") else 1280
        except (TypeError, ValueError) as exc:
            raise PrivacyError("web.priv_conf_mtu") from exc
        if not 576 <= mtu <= 1500:
            raise PrivacyError("web.priv_conf_mtu")
        servers: list[dict[str, Any]] = store.data["providers"]["custom"]
        if len(servers) >= MAX_CUSTOM:
            raise PrivacyError("web.priv_custom_full")
        if any(s["name"].casefold() == name.casefold() for s in servers):
            raise PrivacyError("web.priv_server_taken")
        entry = {
            "id": str(uuid.uuid4()),
            "name": name,
            "endpoint": endpoint if ":" in endpoint[-6:] else f"{endpoint}:51820",
            "public_key": public_key,
            "private_key_sealed": store.seal(private_key),
            "preshared_key_sealed": store.seal(psk) if psk else None,
            "address": address if "/" in address else f"{address}/32",
            "allowed_ips": allowed,
            "dns": dns,
            "mtu": mtu,
            "source": "manual",
        }
        servers.append(entry)
        store.save()
        return {"id": entry["id"], "name": entry["name"]}

    def add_import(self, store: Any, text: str, name: str | None = None) -> dict[str, Any]:
        parsed = parse_wireguard_conf(text)
        return self.add_manual(
            store,
            {
                "name": name or f"Imported {secrets.token_hex(2)}",
                "endpoint": parsed["endpoint"],
                "public_key": parsed["public_key"],
                "private_key": parsed["private_key"],
                "preshared_key": parsed.get("preshared_key"),
                "address": parsed["address"],
                "allowed_ips": parsed["allowed_ips"],
                "dns": parsed.get("dns"),
                "mtu": parsed.get("mtu") or 1280,
            },
        )

    def remove(self, store: Any, server_id: str) -> str | None:
        servers: list[dict[str, Any]] = store.data["providers"]["custom"]
        for i, server in enumerate(servers):
            if server["id"] == server_id:
                name = str(server["name"])
                del servers[i]
                hops = store.data["chain"]["hops"]
                store.data["chain"]["hops"] = [h for h in hops if h.get("server_id") != server_id]
                store.save()
                return name
        return None

    def build_peer(self, store: Any, hop: dict[str, Any]) -> ExitPeer:
        server_id = hop.get("server_id")
        server = next((s for s in store.data["providers"]["custom"] if s["id"] == server_id), None)
        if server is None:
            raise PrivacyError("web.priv_server_missing")
        private = store.open(server.get("private_key_sealed"))
        if not private:
            raise PrivacyError("web.priv_key_lost")
        psk = store.open(server.get("preshared_key_sealed"))
        return ExitPeer(
            interface_address=server["address"],
            private_key=private,
            peer_public_key=server["public_key"],
            endpoint=server["endpoint"],
            allowed_ips=server.get("allowed_ips") or "0.0.0.0/0, ::/0",
            preshared_key=psk,
            dns=server.get("dns"),
            mtu=int(server.get("mtu") or 1280),
        )

"""Strict WireGuard configuration parser for imported exit configs."""

from __future__ import annotations

import base64
import re
from typing import Any

from pi_guard.privacy.errors import PrivacyError

_KEY_RE = re.compile(r"^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$")
_ALLOWED_IFACE = frozenset({"PrivateKey", "Address", "DNS", "MTU", "ListenPort", "Table"})
_ALLOWED_PEER = frozenset(
    {
        "PublicKey",
        "PresharedKey",
        "Endpoint",
        "AllowedIPs",
        "PersistentKeepalive",
    }
)
_FORBIDDEN = frozenset(
    {
        "PostUp",
        "PostDown",
        "PreUp",
        "PreDown",
        "SaveConfig",
        "FwMark",
        "Jc",
        "Jmin",
        "Jmax",
        "S1",
        "S2",
        "H1",
        "H2",
        "H3",
        "H4",
    }
)


def parse_wireguard_conf(text: str) -> dict[str, Any]:
    """Parse a single-peer WireGuard config. Rejects shell hooks and Amnezia keys."""
    if len(text) > 16_384:
        raise PrivacyError("web.priv_conf_big")
    section: str | None = None
    iface: dict[str, str] = {}
    peer: dict[str, str] = {}
    peers = 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip().casefold()
            if name == "interface":
                section = "interface"
            elif name == "peer":
                section = "peer"
                peers += 1
                if peers > 1:
                    raise PrivacyError("web.priv_conf_peers")
            else:
                raise PrivacyError("web.priv_conf_section")
            continue
        if "=" not in line or section is None:
            raise PrivacyError("web.priv_conf_line")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key in _FORBIDDEN or ";" in value or "`" in value or "$(" in value:
            raise PrivacyError("web.priv_conf_forbidden")
        if section == "interface":
            if key not in _ALLOWED_IFACE:
                raise PrivacyError("web.priv_conf_key")
            iface[key] = value
        else:
            if key not in _ALLOWED_PEER:
                raise PrivacyError("web.priv_conf_key")
            peer[key] = value
    if peers != 1:
        raise PrivacyError("web.priv_conf_peers")
    for field in ("PrivateKey", "Address"):
        if field not in iface:
            raise PrivacyError("web.priv_conf_iface")
    for field in ("PublicKey", "Endpoint", "AllowedIPs"):
        if field not in peer:
            raise PrivacyError("web.priv_conf_peer")
    for key_name in ("PrivateKey", "PublicKey"):
        value = iface.get(key_name) or peer.get(key_name) or ""
        if not _KEY_RE.match(value):
            raise PrivacyError("web.priv_conf_keyfmt")
    if "PresharedKey" in peer and not _KEY_RE.match(peer["PresharedKey"]):
        raise PrivacyError("web.priv_conf_keyfmt")
    # Reject values that look like options or paths.
    for value in (*iface.values(), *peer.values()):
        looks_bad = value.startswith("-") or ("/" in value and key_looks_path(value))
        if looks_bad and not _is_cidr_or_dns(value):
            raise PrivacyError("web.priv_conf_value")
    try:
        mtu = int(iface["MTU"]) if "MTU" in iface else None
    except ValueError as exc:
        raise PrivacyError("web.priv_conf_mtu") from exc
    return {
        "private_key": iface["PrivateKey"],
        "address": iface["Address"].split(",")[0].strip(),
        "dns": iface.get("DNS"),
        "mtu": mtu,
        "public_key": peer["PublicKey"],
        "preshared_key": peer.get("PresharedKey"),
        "endpoint": peer["Endpoint"],
        "allowed_ips": peer["AllowedIPs"],
    }


def key_looks_path(value: str) -> bool:
    return value.startswith("/") and not re.match(r"^[\d.:a-fA-F/]+$", value)


def _is_cidr_or_dns(value: str) -> bool:
    if re.match(r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$", value):
        return True
    if ":" in value:  # IPv6 or host:port
        return True
    return bool(re.match(r"^[A-Za-z0-9._-]+$", value))


def valid_wg_key(value: str) -> bool:
    if not _KEY_RE.match(value):
        return False
    try:
        return len(base64.b64decode(value)) == 32
    except ValueError:
        return False

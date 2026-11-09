"""Versioned privacy configuration with sealed secrets."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from copy import deepcopy
from pathlib import Path
from typing import Any

from pi_guard.privacy.errors import PrivacyError
from pi_guard.security.files import atomic_write, secure_dir

PRIVACY_CONFIG_VERSION = 1
FOLDER = Path("privacy")
CONFIG_NAME = "config.json"
EXIT_PEER = "exit.peer"
EXIT_CONF = "exit.conf"
NFT_PLAN = "nft.plan"
TOR_WANT = "tor.want"
RELAY_CACHE = "mullvad-relays.json"

DEFAULT: dict[str, Any] = {
    "version": PRIVACY_CONFIG_VERSION,
    "profile": "standard",
    "enabled": False,
    "kill_switch": "off",  # off | vpn | strict | custom
    "dns": {
        "mode": "system",  # system | custom | vpn_routed | provider | dot
        "servers": [],
        "strict": False,
        "dot_server": "",
    },
    "tor": {
        "enabled": False,
        "mode": "off",  # off | socks | vpn_then_tor
        "socks_port": 9050,
        "bridges": "",
        "use_bridges": False,
    },
    "obfuscation": {
        "mode": "off",  # off | auto | amneziawg | shadowsocks | xray
        "params": {},
    },
    "chain": {
        "active": False,
        "hops": [],  # [{id, provider, name, server_id, params}]
    },
    "providers": {
        "mullvad": {
            "account_sealed": None,
            "device_id": None,
            "device_name": None,
            "ipv4_address": None,
            "ipv6_address": None,
            "public_key": None,
            "private_key_sealed": None,
            "selected": None,  # {country, city, hostname, multihop_entry?}
        },
        "custom": [],  # [{id, name, endpoint, public_key, private_key_sealed, ...}]
    },
    "routing": {
        "scope": "all_full_tunnel",  # all_full_tunnel | selected_peers | destinations
        "peer_ids": [],
        "destinations": [],
        "bypass": [],
    },
    "custom_profiles": {},
}


class PrivacyStore:
    def __init__(self, data_dir: Path, secret_key: bytes) -> None:
        self._dir = secure_dir(data_dir / FOLDER)
        self._path = self._dir / CONFIG_NAME
        self._key = hashlib.sha256(b"pi-guard privacy key\0" + secret_key).digest()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return deepcopy(DEFAULT)
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PrivacyError("web.priv_config_bad") from exc
        return migrate(raw)

    def save(self) -> None:
        text = json.dumps(self._data, indent=2, sort_keys=True) + "\n"
        atomic_write(self._path, text.encode())

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def public_view(self) -> dict[str, Any]:
        """Config safe for the API: secrets redacted."""
        view = deepcopy(self._data)
        mullvad = view["providers"]["mullvad"]
        has_account = bool(mullvad.get("account_sealed"))
        mullvad["account_sealed"] = None
        mullvad["private_key_sealed"] = None
        mullvad["has_account"] = has_account
        mullvad["account_hint"] = "****" if has_account else None
        for server in view["providers"]["custom"]:
            server.pop("private_key_sealed", None)
            server.pop("preshared_key_sealed", None)
            server["has_private_key"] = True
        return view

    def seal(self, value: str) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = secrets.token_bytes(12)
        sealed = AESGCM(self._key).encrypt(nonce, value.encode(), b"pi-guard-privacy")
        return base64.b64encode(nonce + sealed).decode()

    def open(self, value: str | None) -> str | None:
        if not value:
            return None
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        raw = base64.b64decode(value)
        try:
            return AESGCM(self._key).decrypt(raw[:12], raw[12:], b"pi-guard-privacy").decode()
        except (InvalidTag, ValueError):
            return None

    def write_exit_peer(self, lines: str) -> None:
        atomic_write(self._dir / EXIT_PEER, lines.encode())

    def write_exit_conf(self, text: str) -> None:
        atomic_write(self._dir / EXIT_CONF, text.encode())

    def clear_exit(self) -> None:
        for name in (EXIT_PEER, EXIT_CONF):
            (self._dir / name).unlink(missing_ok=True)

    def write_nft_plan(self, text: str) -> None:
        atomic_write(self._dir / NFT_PLAN, text.encode())

    def write_tor_want(self, enabled: bool) -> None:
        path = self._dir / TOR_WANT
        if enabled:
            atomic_write(path, b"1\n")
        else:
            path.unlink(missing_ok=True)

    def relay_cache_path(self) -> Path:
        return self._dir / RELAY_CACHE

    def mark_installed(self) -> None:
        atomic_write(self._dir / "installed", b"1\n")


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """Bring older configs forward. New advanced features stay off."""
    data = deepcopy(DEFAULT)
    version = int(raw.get("version") or 0)
    if version > PRIVACY_CONFIG_VERSION:
        raise PrivacyError("web.priv_config_newer")
    for key, value in DEFAULT.items():
        if key == "version":
            continue
        if key not in raw:
            continue
        if isinstance(value, dict) and isinstance(raw[key], dict):
            merged = deepcopy(value)
            merged.update(
                {k: v for k, v in raw[key].items() if k in merged or key == "custom_profiles"}
            )
            if key == "providers":
                merged["mullvad"] = {
                    **DEFAULT["providers"]["mullvad"],
                    **raw[key].get("mullvad", {}),
                }
                merged["custom"] = list(raw[key].get("custom") or [])
            data[key] = merged
        else:
            data[key] = raw[key]
    data["version"] = PRIVACY_CONFIG_VERSION
    return data

"""Editable privacy profiles. Starting points, not guaranteed anonymity levels."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from pi_guard.privacy.errors import PrivacyError

PROFILE_IDS = ("standard", "enhanced", "maximum", "custom")

# Applied on top of the stored config when the user picks a profile.
PRESETS: dict[str, dict[str, Any]] = {
    "standard": {
        "enabled": False,
        "kill_switch": "off",
        "dns": {"mode": "system", "servers": [], "strict": False, "dot_server": ""},
        "tor": {
            "enabled": False,
            "mode": "off",
            "socks_port": 9050,
            "bridges": "",
            "use_bridges": False,
        },
        "obfuscation": {"mode": "off", "params": {}},
        "chain": {"active": False, "hops": []},
    },
    "enhanced": {
        "enabled": True,
        "kill_switch": "vpn",
        "dns": {"mode": "vpn_routed", "servers": [], "strict": False, "dot_server": ""},
        "tor": {
            "enabled": False,
            "mode": "off",
            "socks_port": 9050,
            "bridges": "",
            "use_bridges": False,
        },
        "obfuscation": {"mode": "off", "params": {}},
        "chain": {"active": False, "hops": []},
    },
    "maximum": {
        "enabled": True,
        "kill_switch": "strict",
        "dns": {"mode": "vpn_routed", "servers": [], "strict": True, "dot_server": ""},
        "tor": {
            "enabled": False,
            "mode": "off",
            "socks_port": 9050,
            "bridges": "",
            "use_bridges": False,
        },
        "obfuscation": {"mode": "off", "params": {}},
        # Hops stay empty: the user must pick a compatible exit. Strict fail-closed without a hop
        # blocks forwarding until they add one — intentional for "maximum".
        "chain": {"active": False, "hops": []},
    },
    "custom": {},
}


def apply_profile(config: dict[str, Any], profile_id: str) -> dict[str, Any]:
    if profile_id not in PROFILE_IDS:
        raise PrivacyError("web.priv_profile_bad")
    out = deepcopy(config)
    out["profile"] = profile_id
    if profile_id == "custom":
        saved = out.get("custom_profiles", {}).get("custom")
        if isinstance(saved, dict):
            for key in ("enabled", "kill_switch", "dns", "tor", "obfuscation", "chain", "routing"):
                if key in saved:
                    out[key] = deepcopy(saved[key])
        return out
    preset = PRESETS[profile_id]
    for key, value in preset.items():
        out[key] = deepcopy(value)
    return out


def save_custom(config: dict[str, Any]) -> dict[str, Any]:
    """Remember the current advanced settings under the custom profile."""
    out = deepcopy(config)
    out["profile"] = "custom"
    out.setdefault("custom_profiles", {})["custom"] = {
        "enabled": out.get("enabled"),
        "kill_switch": out.get("kill_switch"),
        "dns": deepcopy(out.get("dns") or {}),
        "tor": deepcopy(out.get("tor") or {}),
        "obfuscation": deepcopy(out.get("obfuscation") or {}),
        "chain": deepcopy(out.get("chain") or {}),
        "routing": deepcopy(out.get("routing") or {}),
    }
    return out


def describe(profile_id: str) -> dict[str, str]:
    return {
        "id": profile_id,
        "title": f"web.priv_profile_{profile_id}",
        "intro": f"web.priv_profile_{profile_id}_intro",
    }

"""The AmneziaWG layer as a privacy setting. Stock Raspberry Pi OS has no AmneziaWG."""

from __future__ import annotations

from typing import Any

from pi_guard.privacy.capabilities import Capability
from pi_guard.privacy.errors import PrivacyError

MODES = frozenset({"off", "amneziawg"})


def normalize(body: dict[str, Any], caps: dict[str, Capability]) -> dict[str, Any]:
    mode = str(body.get("mode") or "off")
    if mode not in MODES:
        raise PrivacyError("web.priv_obfs_mode")
    if mode == "amneziawg" and not caps["amneziawg"].available:
        raise PrivacyError(caps["amneziawg"].reason or "web.priv_need_amnezia")
    return {"mode": mode, "params": {}}


def update(store: Any, caps: dict[str, Capability], body: dict[str, Any]) -> dict[str, Any]:
    obfs = normalize(body, caps)
    store.data["obfuscation"] = obfs
    store.save()
    return obfs

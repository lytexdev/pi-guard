"""Traffic obfuscation capability probe. Stock Raspberry Pi has no AmneziaWG."""

from __future__ import annotations

from typing import Any

from pi_guard.privacy.capabilities import Capability
from pi_guard.privacy.errors import PrivacyError

MODES = frozenset({"off", "auto", "amneziawg", "shadowsocks", "xray"})


def normalize(body: dict[str, Any], caps: dict[str, Capability]) -> dict[str, Any]:
    mode = str(body.get("mode") or "off")
    if mode not in MODES:
        raise PrivacyError("web.priv_obfs_mode")
    if mode == "auto":
        mode = "amneziawg" if caps["amneziawg"].available else "off"
    if mode == "amneziawg" and not caps["amneziawg"].available:
        raise PrivacyError(caps["amneziawg"].reason or "web.priv_need_amnezia")
    if mode == "shadowsocks" and not caps["shadowsocks"].available:
        raise PrivacyError(caps["shadowsocks"].reason or "web.priv_ss_disabled")
    if mode == "xray" and not caps["xray_reality"].available:
        raise PrivacyError(caps["xray_reality"].reason or "web.priv_xray_disabled")
    raw_params = body.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    # Amnezia parameters only accepted when the capability is present.
    clean: dict[str, Any] = {}
    if mode == "amneziawg":
        for key in ("jc", "jmin", "jmax", "s1", "s2", "h1", "h2", "h3", "h4"):
            if key in params:
                try:
                    clean[key] = int(params[key])
                except (TypeError, ValueError) as exc:
                    raise PrivacyError("web.priv_obfs_params") from exc
    return {"mode": mode, "params": clean}


def describe(caps: dict[str, Capability]) -> dict[str, Any]:
    return {
        "modes": [
            {
                "id": "off",
                "available": True,
                "performance": "minimal",
                "client": "any",
            },
            {
                "id": "auto",
                "available": True,
                "performance": "variable",
                "client": "depends",
            },
            {
                "id": "amneziawg",
                "available": caps["amneziawg"].available,
                "performance": "moderate",
                "client": "amneziawg",
                "reason": caps["amneziawg"].reason,
            },
            {
                "id": "shadowsocks",
                "available": caps["shadowsocks"].available,
                "performance": "moderate",
                "client": "shadowsocks",
                "reason": caps["shadowsocks"].reason,
            },
            {
                "id": "xray",
                "available": caps["xray_reality"].available,
                "performance": "moderate",
                "client": "xray",
                "reason": caps["xray_reality"].reason,
            },
        ],
        "warning": "web.priv_obfs_warning",
    }


def update(store: Any, caps: dict[str, Capability], body: dict[str, Any]) -> dict[str, Any]:
    obfs = normalize(body, caps)
    store.data["obfuscation"] = obfs
    if store.data.get("profile") != "custom":
        store.data["profile"] = "custom"
    store.save()
    return obfs

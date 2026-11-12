"""Multi-hop chain: ordered upstream exits on the Pi."""

from __future__ import annotations

import uuid
from typing import Any

from pi_guard.privacy.compat import validate_chain
from pi_guard.privacy.errors import PrivacyError
from pi_guard.privacy.providers.custom_wg import CustomWireGuard
from pi_guard.privacy.providers.mullvad import MullvadProvider

MAX_HOPS = 3


def list_hops(store: Any) -> list[dict[str, Any]]:
    return list(store.data["chain"].get("hops") or [])


def set_hops(store: Any, hops: list[dict[str, Any]], caps: dict[str, Any]) -> list[dict[str, Any]]:
    cleaned = [_clean_hop(h) for h in hops]
    validate_chain(
        cleaned,
        caps,
        tor_mode=str(store.data.get("tor", {}).get("mode") or "off"),
        obfuscation=str(store.data.get("obfuscation", {}).get("mode") or "off"),
    )
    store.data["chain"]["hops"] = cleaned
    if store.data.get("profile") != "custom":
        store.data["profile"] = "custom"
    store.save()
    return cleaned


def add_hop(store: Any, hop: dict[str, Any], caps: dict[str, Any]) -> dict[str, Any]:
    hops = list_hops(store)
    hops.append(hop)
    set_hops(store, hops, caps)
    return hops[-1]


def remove_hop(store: Any, hop_id: str, caps: dict[str, Any]) -> None:
    hops = [h for h in list_hops(store) if h.get("id") != hop_id]
    set_hops(store, hops, caps)


def reorder(store: Any, order: list[str], caps: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {h["id"]: h for h in list_hops(store)}
    if set(order) != set(by_id):
        raise PrivacyError("web.priv_chain_order")
    return set_hops(store, [by_id[i] for i in order], caps)


def _clean_hop(hop: dict[str, Any]) -> dict[str, Any]:
    provider = str(hop.get("provider") or "").strip()
    if provider not in {"mullvad", "custom", "import"}:
        raise PrivacyError("web.priv_hop_provider")
    name = " ".join(str(hop.get("name") or provider).split())[:64]
    hop_id = str(hop.get("id") or uuid.uuid4())
    params = hop.get("params") if isinstance(hop.get("params"), dict) else {}
    server_id = hop.get("server_id")
    if provider in {"custom", "import"} and not server_id:
        raise PrivacyError("web.priv_server_missing")
    return {
        "id": hop_id,
        "provider": "custom" if provider == "import" else provider,
        "name": name or provider,
        "server_id": server_id,
        "params": params,
        "enabled": hop.get("enabled") is not False,
    }


def build_active_peer(store: Any) -> Any:
    """Build the ExitPeer for the first enabled hop.

    Nested hops beyond Mullvad native multi-hop are limited.
    """
    hops = [h for h in list_hops(store) if h.get("enabled") is not False]
    if not hops:
        raise PrivacyError("web.priv_chain_empty")
    if len(hops) > 1:
        # Only Mullvad native multi-hop (entry+exit in params) or a single custom hop is supported
        # as a real routing implementation. Two independent WG tunnels need nested namespaces;
        # refuse silent partial activation.
        kinds = {h["provider"] for h in hops}
        if kinds != {"mullvad"} or len(hops) != 2:
            raise PrivacyError("web.priv_chain_unsupported")
        entry, exit_hop = hops[0], hops[1]
        params = {
            **(exit_hop.get("params") or {}),
            "entry_hostname": (entry.get("params") or {}).get("hostname"),
            "entry_ipv4": (entry.get("params") or {}).get("ipv4"),
            "entry_public_key": (entry.get("params") or {}).get("public_key"),
            # Mullvad multi-hop goes to the entry's address on the exit's own port; the
            # entry's port would lead to another exit.
            "multihop_port": (exit_hop.get("params") or {}).get("multihop_port"),
        }
        return MullvadProvider().build_peer(store, {"params": params})
    hop = hops[0]
    if hop["provider"] == "mullvad":
        return MullvadProvider().build_peer(store, hop)
    return CustomWireGuard().build_peer(store, hop)

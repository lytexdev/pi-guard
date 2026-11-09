"""DNS privacy settings for VPN clients and the Pi's upstream path."""

from __future__ import annotations

from typing import Any

from pi_guard.privacy.compat import validate_dns
from pi_guard.privacy.errors import PrivacyError

MODES = frozenset({"system", "custom", "vpn_routed", "provider", "dot"})


def normalize(dns: dict[str, Any]) -> dict[str, Any]:
    mode = str(dns.get("mode") or "system")
    if mode not in MODES and mode not in {"doh", "doq"}:
        raise PrivacyError("web.priv_dns_mode")
    servers = [str(s).strip() for s in (dns.get("servers") or []) if str(s).strip()]
    return {
        "mode": mode,
        "servers": servers[:8],
        "strict": bool(dns.get("strict")),
        "dot_server": str(dns.get("dot_server") or "").strip()[:253],
    }


def resolve_servers(
    dns: dict[str, Any],
    *,
    pihole: str | None,
    provider_dns: str | None,
) -> list[str]:
    """Addresses to push into client configs / exit interface."""
    mode = dns.get("mode") or "system"
    if mode == "system":
        return [pihole] if pihole else []
    if mode == "custom":
        return list(dns.get("servers") or [])
    if mode == "vpn_routed":
        return [pihole or "10.8.0.1"]
    if mode == "provider":
        if not provider_dns:
            raise PrivacyError("web.priv_dns_no_provider")
        return [provider_dns]
    if mode == "dot":
        # Clients still talk to the Pi; the Pi (when installed) forwards via DoT.
        return [pihole or "10.8.0.1"]
    return []


def leak_policy(dns: dict[str, Any]) -> dict[str, Any]:
    """What the firewall/root job should enforce."""
    strict = bool(dns.get("strict"))
    return {
        "block_outside_dns": strict or dns.get("mode") in {"vpn_routed", "provider", "dot"},
        "block_ipv6_dns": strict,
        "allow_fallback": not strict,
        "strict": strict,
    }


def update(store: Any, caps: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    dns = normalize(body)
    validate_dns(dns, caps)
    store.data["dns"] = dns
    if store.data.get("profile") != "custom":
        store.data["profile"] = "custom"
    store.save()
    return dns

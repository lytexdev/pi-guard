"""Compatibility checks for privacy chains and profiles."""

from __future__ import annotations

from typing import Any

from pi_guard.privacy.capabilities import Capability
from pi_guard.privacy.errors import PrivacyError

# provider kinds that need a WireGuard exit interface
WG_PROVIDERS = frozenset({"custom", "mullvad", "import"})


def validate_chain(
    hops: list[dict[str, Any]],
    caps: dict[str, Capability],
    *,
    tor_mode: str = "off",
    obfuscation: str = "off",
) -> None:
    if len(hops) > 3:
        raise PrivacyError("web.priv_chain_too_long")
    ids = [h.get("id") for h in hops]
    if len(ids) != len(set(ids)):
        raise PrivacyError("web.priv_chain_dup")
    providers = [str(h.get("provider") or "") for h in hops]
    if tor_mode == "vpn_then_tor" and any(p in WG_PROVIDERS for p in providers):
        # Device → Pi → WG exit → Tor is unsupported: Tor cannot carry the WG UDP exit cleanly
        # after another full tunnel without a dedicated TCP bridge. Block for honesty.
        raise PrivacyError("web.priv_tor_with_wg_exit")
    if tor_mode == "vpn_then_tor" and hops:
        raise PrivacyError("web.priv_tor_with_hops")
    for hop in hops:
        provider = str(hop.get("provider") or "")
        if provider == "mullvad" and not caps["mullvad"].available:
            raise PrivacyError(caps["mullvad"].reason or "web.priv_need_wg")
        if provider in {"custom", "import"} and not caps["custom_exit"].available:
            raise PrivacyError(caps["custom_exit"].reason or "web.priv_need_wg")
        if provider == "tor":
            raise PrivacyError("web.priv_tor_not_a_hop")
        endpoint = str(hop.get("endpoint") or hop.get("params", {}).get("endpoint") or "")
        allowed = str(hop.get("params", {}).get("allowed_ips") or "0.0.0.0/0")
        if endpoint and _overlaps_home(endpoint):
            raise PrivacyError("web.priv_loop_home")
        if "10.8.0.0/24" in allowed.replace(" ", ""):
            raise PrivacyError("web.priv_loop_subnet")
    if obfuscation == "amneziawg" and not caps["amneziawg"].available:
        raise PrivacyError(caps["amneziawg"].reason or "web.priv_need_amnezia")
    if obfuscation == "shadowsocks" and not caps["shadowsocks"].available:
        raise PrivacyError(caps["shadowsocks"].reason or "web.priv_ss_disabled")
    if obfuscation == "xray" and not caps["xray_reality"].available:
        raise PrivacyError(caps["xray_reality"].reason or "web.priv_xray_disabled")
    # Two Mullvad hops without native multihop params need distinct endpoints
    mullvad_hops = [h for h in hops if h.get("provider") == "mullvad"]
    if len(mullvad_hops) > 2:
        raise PrivacyError("web.priv_mullvad_too_many")
    if len(mullvad_hops) == 2:
        a = (mullvad_hops[0].get("params") or {}).get("hostname")
        b = (mullvad_hops[1].get("params") or {}).get("hostname")
        if a and b and a == b:
            raise PrivacyError("web.priv_mullvad_same")


def validate_dns(dns: dict[str, Any], caps: dict[str, Capability]) -> None:
    mode = str(dns.get("mode") or "system")
    if mode == "dot" and not caps["dns_dot"].available:
        raise PrivacyError(caps["dns_dot"].reason or "web.priv_need_dot")
    if mode == "doh":
        raise PrivacyError(caps["dns_doh"].reason or "web.priv_doh_later")
    if mode == "doq":
        raise PrivacyError(caps["dns_doq"].reason or "web.priv_doq_later")
    if mode == "custom":
        servers = dns.get("servers") or []
        if not servers:
            raise PrivacyError("web.priv_dns_need_servers")
        for server in servers:
            if not _looks_like_host(str(server)):
                raise PrivacyError("web.priv_dns_bad")
    if dns.get("strict") and mode == "system":
        raise PrivacyError("web.priv_dns_strict_system")


def validate_kill_switch(mode: str, caps: dict[str, Capability]) -> None:
    if mode not in {"off", "vpn", "strict", "custom"}:
        raise PrivacyError("web.priv_ks_bad")
    if mode != "off" and not caps["kill_switch"].available:
        raise PrivacyError(caps["kill_switch"].reason or "web.priv_need_nft")


def validate_tor(tor: dict[str, Any], caps: dict[str, Capability]) -> None:
    mode = str(tor.get("mode") or "off")
    if mode == "off" and not tor.get("enabled"):
        return
    if not caps["tor"].available:
        raise PrivacyError(caps["tor"].reason or "web.priv_need_tor")
    if mode not in {"off", "socks", "vpn_then_tor"}:
        raise PrivacyError("web.priv_tor_mode_bad")
    if tor.get("use_bridges") and not caps["tor"].available:
        raise PrivacyError("web.priv_need_tor")


def _overlaps_home(endpoint: str) -> bool:
    host = endpoint.rsplit("%", 1)[0]
    if host.startswith("["):
        host = host[1:].split("]")[0]
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    return host in {"10.8.0.1", "127.0.0.1", "::1", "localhost"}


def _looks_like_host(value: str) -> bool:
    value = value.strip()
    if not value or len(value) > 253:
        return False
    if value.count(":") > 1:  # IPv6
        return all(c in "0123456789abcdefABCDEF:" for c in value)
    if all(c.isdigit() or c == "." for c in value):
        parts = value.split(".")
        return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    return all(c.isalnum() or c in ".-" for c in value)

"""Pi-side upstream fail-closed plan for nftables (applied by a root job)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

TABLE = "pi_guard_privacy"
EXIT_IFACE = "pgexit"
HOME_IFACE = "wg0"
# Management ports that must stay reachable from the LAN even in strict mode.
DASHBOARD_PORTS = (8080, 8443)
SSH_PORT = 22


@dataclass(frozen=True)
class Plan:
    """Fail-closed plan: drop wg0→WAN unless via exit; optional Tor uid outbound."""

    mode: str = "off"  # off | vpn | strict | custom
    exit_up: bool = False
    tor: bool = False
    allow_lan_mgmt: bool = True
    home_network: str | None = None
    wan_ifaces: tuple[str, ...] = ()


def render(plan: Plan) -> str:
    """Pure string. The root job feeds this to `nft -f -` atomically."""
    lines = [
        f"add table inet {TABLE}",
        f"delete table inet {TABLE}",
        f"table inet {TABLE} {{",
    ]
    # VPN clients leave through the exit with its address: Mullvad and other exits know
    # only that one. Needed with or without a kill switch.
    nat = (
        [
            "  chain postrouting {",
            "    type nat hook postrouting priority srcnat; policy accept;",
            f'    oifname "{EXIT_IFACE}" masquerade',
            "  }",
        ]
        if plan.exit_up
        else []
    )
    if plan.mode == "off":
        lines += [*nat, "}"]
        return "\n".join(lines) + "\n"

    # Forwarding from VPN clients: fail closed when strict/vpn and an exit is expected.
    fwd = [
        "  chain forward {",
        "    type filter hook forward priority filter; policy "
        + ("drop" if plan.mode in {"vpn", "strict", "custom"} else "accept")
        + ";",
        "    ct state established,related accept",
        # The kill switch is about VPN clients only: containers and other forwarding pass.
        f'    iifname != "{HOME_IFACE}" accept',
        f'    iifname "{HOME_IFACE}" oifname "{EXIT_IFACE}" accept',
        f'    iifname "{EXIT_IFACE}" oifname "{HOME_IFACE}" ct state established,related accept',
    ]
    if plan.home_network:  # the home network stays reachable from the VPN
        fwd.append(f'    iifname "{HOME_IFACE}" ip daddr {plan.home_network} accept')
    if plan.tor:
        fwd.append(f'    iifname "{HOME_IFACE}" meta skuid debian-tor accept')
        fwd.append("    meta skuid debian-tor accept")
    if plan.mode == "vpn" and not plan.exit_up and not plan.tor:
        # VPN mode without an active exit: do not lock the home WAN (owner still setting up).
        fwd = [
            "  chain forward {",
            "    type filter hook forward priority filter; policy accept;",
        ]
    if plan.mode == "strict" and not plan.exit_up and not plan.tor:
        # Strict: drop client internet until an exit is up. LAN to Pi stays in input chain.
        pass
    fwd.append("  }")

    inp = [
        "  chain input {",
        "    type filter hook input priority filter; policy accept;",
        '    iif "lo" accept',
        "    ct state established,related accept",
    ]
    if plan.allow_lan_mgmt and plan.home_network:
        ports = ", ".join(str(p) for p in (*DASHBOARD_PORTS, SSH_PORT))
        inp.append(f"    ip saddr {plan.home_network} tcp dport {{ {ports} }} accept")
    inp.append("  }")

    out = [
        "  chain output {",
        "    type filter hook output priority filter; policy "
        + ("drop" if plan.mode == "strict" and plan.exit_up else "accept")
        + ";",
        '    oif "lo" accept',
        "    ct state established,related accept",
        f'    oifname "{EXIT_IFACE}" accept',
        f'    oifname "{HOME_IFACE}" accept',
        "    udp sport 68 udp dport 67 accept",
        "    udp sport 546 udp dport 547 accept",
        "    icmpv6 type { nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert } accept",
    ]
    if plan.tor:
        out.append("    meta skuid debian-tor accept")
    if plan.allow_lan_mgmt and plan.home_network:
        out.append(f"    ip daddr {plan.home_network} accept")
    # Always allow WireGuard handshake traffic to the exit endpoint via mark later; for now
    # accept UDP when exit is configuring.
    if plan.mode == "strict":
        out.append("    udp dport 51820 accept")
        out.append("    meta l4proto { tcp, udp } th dport 53 accept")
    out.append("  }")

    lines += fwd + inp + out + nat + ["}"]
    return "\n".join(lines) + "\n"


def apply_or_relaxed(plan: Plan, load: Callable[[Plan], None]) -> Plan:
    """If the kernel refuses a rule (missing module), retry without tor uid match."""
    try:
        load(plan)
        return plan
    except Exception:
        if not plan.tor:
            raise
        relaxed = Plan(
            mode=plan.mode,
            exit_up=plan.exit_up,
            tor=False,
            allow_lan_mgmt=plan.allow_lan_mgmt,
            home_network=plan.home_network,
            wan_ifaces=plan.wan_ifaces,
        )
        load(relaxed)
        return relaxed

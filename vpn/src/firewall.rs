//! The kill switch. Rules stay in the kernel by themselves: if this service crashes or is killed,
//! nothing lifts them, so the system fails closed. Only a deliberate step removes them.

use crate::sys::run_input;

pub const TABLE: &str = "pi_guard_vpn";
pub const IFACE: &str = "pgvpn";
pub const MARK: u32 = 0xca6d;
pub const SPLIT_GROUP: &str = "pi-guard-split";

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Plan {
    /// Block everything past the tunnel.
    pub block: bool,
    /// Keep printers and the like at home reachable.
    pub allow_lan: bool,
    /// Apps in the split group go past the tunnel.
    pub split: bool,
    /// While looking up the server's name, only for root, only for a moment.
    pub resolving: bool,
}

const LAN4: &str = "{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 224.0.0.0/4 }";
const LAN6: &str = "{ fe80::/10, fc00::/7, ff00::/8 }";

pub fn render(plan: &Plan) -> String {
    let mut text = format!("add table inet {TABLE}\ndelete table inet {TABLE}\ntable inet {TABLE} {{\n");
    if plan.split {
        // Mark before the route is chosen, and hide the tunnel's address on the way out.
        text += &format!(
            "  chain split_out {{\n    type route hook output priority mangle; policy accept;\n    \
             socket cgroupv2 level 1 \"{SPLIT_GROUP}\" meta mark set {MARK}\n  }}\n  \
             chain split_nat {{\n    type nat hook postrouting priority srcnat; policy accept;\n    \
             meta mark {MARK} oifname != \"{IFACE}\" masquerade\n  }}\n"
        );
    }
    if plan.block {
        let mut output = format!(
            "    oif \"lo\" accept\n    oifname \"{IFACE}\" accept\n    meta mark {MARK} accept\n    \
             udp sport 68 udp dport 67 accept\n    udp sport 546 udp dport 547 accept\n    \
             icmpv6 type {{ nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert }} accept\n"
        );
        let mut input = format!(
            "    iif \"lo\" accept\n    ct state established,related accept\n    \
             udp sport 67 udp dport 68 accept\n    udp sport 547 udp dport 546 accept\n    \
             icmpv6 type {{ nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert }} accept\n"
        );
        if plan.allow_lan {
            output += &format!("    ip daddr {LAN4} accept\n    ip6 daddr {LAN6} accept\n");
            input += &format!("    ip saddr {LAN4} accept\n    ip6 saddr {LAN6} accept\n");
        }
        if plan.resolving {
            output += "    meta skuid 0 meta l4proto { tcp, udp } th dport 53 accept\n";
        }
        text += &format!(
            "  chain out {{\n    type filter hook output priority filter; policy drop;\n{output}  }}\n  \
             chain in {{\n    type filter hook input priority filter; policy drop;\n{input}  }}\n  \
             chain through {{\n    type filter hook forward priority filter; policy drop;\n    \
             oifname \"{IFACE}\" accept\n    iifname \"{IFACE}\" ct state established,related accept\n  }}\n"
        );
    }
    text + "}\n"
}

pub fn apply(plan: &Plan) -> Result<(), String> {
    run_input("nft", &["-f", "-"], render(plan).as_bytes())
}

/// Only for a deliberate step: everything open again.
pub fn clear() -> Result<(), String> {
    run_input("nft", &["-f", "-"], format!("add table inet {TABLE}\ndelete table inet {TABLE}\n").as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_kill_switch_drops_by_default_in_every_direction() {
        let text = render(&Plan { block: true, ..Plan::default() });
        assert_eq!(text.matches("policy drop").count(), 3);
        assert!(text.contains("table inet"), "must cover IPv4 and IPv6 alike");
    }

    #[test]
    fn the_home_network_stays_closed_unless_asked() {
        assert!(!render(&Plan { block: true, ..Plan::default() }).contains("192.168.0.0/16"));
        assert!(render(&Plan { block: true, allow_lan: true, ..Plan::default() }).contains("192.168.0.0/16"));
    }

    #[test]
    fn dns_past_the_tunnel_only_while_resolving_and_only_for_root() {
        let plain = render(&Plan { block: true, ..Plan::default() });
        assert!(!plain.contains("dport 53"));
        let resolving = render(&Plan { block: true, resolving: true, ..Plan::default() });
        assert!(resolving.contains("meta skuid 0 meta l4proto { tcp, udp } th dport 53 accept"));
    }

    #[test]
    fn without_a_plan_nothing_is_blocked() {
        assert!(!render(&Plan::default()).contains("policy drop"));
    }

    #[test]
    fn replacing_the_rules_is_one_step() {
        let text = render(&Plan { block: true, ..Plan::default() });
        assert!(text.starts_with("add table inet pi_guard_vpn\ndelete table"));
    }
}

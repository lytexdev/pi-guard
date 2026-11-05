//! Bringing the tunnel up and down, the way wg-quick does it: the tunnel gets its own routing table,
//! and only packets of WireGuard itself, marked, may use the normal one.

use crate::config::Tunnel;
use crate::firewall::{IFACE, MARK};
use crate::sys::{quiet, read, run, run_input, set_preshared_key};

pub struct Stats {
    pub handshake: u64,
    pub received: u64,
    pub sent: u64,
}

fn table() -> String {
    MARK.to_string()
}

pub fn exists() -> bool {
    run("ip", &["link", "show", "dev", IFACE]).is_ok()
}

pub fn down() {
    let table = table();
    for family in ["-4", "-6"] {
        quiet("ip", &[family, "rule", "del", "not", "fwmark", &table, "table", &table]);
        quiet("ip", &[family, "rule", "del", "table", "main", "suppress_prefixlength", "0"]);
    }
    quiet("resolvectl", &["revert", IFACE]);
    quiet("ip", &["link", "del", "dev", IFACE]);
}

pub fn up(tunnel: &Tunnel, endpoint: &str, preshared_key: &str) -> Result<(), String> {
    down();
    run("ip", &["link", "add", "dev", IFACE, "type", "wireguard"])?;
    run_input("wg", &["set", IFACE, "private-key", "/dev/stdin", "fwmark", &table()], tunnel.private_key.as_bytes())?;
    let allowed = tunnel.allowed_ips.join(",");
    run("wg", &["set", IFACE, "peer", &tunnel.peer_key, "endpoint", endpoint, "allowed-ips", &allowed, "persistent-keepalive", "25"])?;
    set_preshared_key(IFACE, &tunnel.peer_key, preshared_key)?;
    run("ip", &["address", "add", &tunnel.address, "dev", IFACE])?;
    run("ip", &["link", "set", "dev", IFACE, "mtu", &tunnel.mtu.to_string(), "up"])?;
    routes(tunnel)?;
    if !tunnel.dns.is_empty() {
        let mut args = vec!["dns", IFACE];
        args.extend(tunnel.dns.iter().map(String::as_str));
        quiet("resolvectl", &args);
        quiet("resolvectl", &["domain", IFACE, "~."]);
        quiet("resolvectl", &["default-route", IFACE, "yes"]);
    }
    Ok(())
}

fn routes(tunnel: &Tunnel) -> Result<(), String> {
    let table = table();
    if tunnel.full() {
        for family in ["-4", "-6"] {
            run("ip", &[family, "route", "add", "default", "dev", IFACE, "table", &table])?;
            run("ip", &[family, "rule", "add", "not", "fwmark", &table, "table", &table])?;
            run("ip", &[family, "rule", "add", "table", "main", "suppress_prefixlength", "0"])?;
        }
        return Ok(());
    }
    for route in &tunnel.allowed_ips {
        let family = if route.contains(':') { "-6" } else { "-4" };
        run("ip", &[family, "route", "add", route, "dev", IFACE])?;
    }
    Ok(())
}

pub fn stats() -> Option<Stats> {
    let text = read("wg", &["show", IFACE, "dump"]).ok()?;
    let line = text.lines().nth(1)?;
    let parts: Vec<&str> = line.split('\t').collect();
    Some(Stats {
        handshake: parts.get(4)?.parse().ok()?,
        received: parts.get(5)?.parse().ok()?,
        sent: parts.get(6)?.parse().ok()?,
    })
}

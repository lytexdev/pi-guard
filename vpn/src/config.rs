//! The access as pi-guard hands it out. Everything is checked here, because these values end up
//! as arguments of commands run as root. No value ever passes through a shell.

use serde::{Deserialize, Serialize};
use std::net::{IpAddr, ToSocketAddrs};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Tunnel {
    pub private_key: String,
    pub address: String,
    pub dns: Vec<String>,
    pub mtu: u32,
    pub peer_key: String,
    pub preshared_key: String,
    pub endpoint: String,
    pub allowed_ips: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Way {
    pub kind: String,
    pub endpoint: String,
    #[serde(default)]
    pub mtu: u32,
}

pub fn valid_key(key: &str) -> bool {
    key.len() == 44
        && key.ends_with('=')
        && key[..43].bytes().all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/')
}

fn valid_cidr(text: &str) -> bool {
    let Some((address, bits)) = text.split_once('/') else { return false };
    let Ok(bits) = bits.parse::<u8>() else { return false };
    match address.parse::<IpAddr>() {
        Ok(IpAddr::V4(_)) => bits <= 32,
        Ok(IpAddr::V6(_)) => bits <= 128,
        Err(_) => false,
    }
}

pub fn valid_endpoint(text: &str) -> bool {
    let Some((host, port)) = text.rsplit_once(':') else { return false };
    if port.parse::<u16>().is_err() {
        return false;
    }
    if let Some(inner) = host.strip_prefix('[').and_then(|h| h.strip_suffix(']')) {
        return inner.parse::<IpAddr>().is_ok();
    }
    !host.is_empty()
        && host.len() <= 253
        && host.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'.' || b == b'-')
        && !host.starts_with('-')
}

impl Tunnel {
    pub fn parse(text: &str) -> Result<Tunnel, String> {
        let mut tunnel = Tunnel {
            private_key: String::new(), address: String::new(), dns: vec![], mtu: 1420,
            peer_key: String::new(), preshared_key: String::new(), endpoint: String::new(),
            allowed_ips: vec![],
        };
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') || line.starts_with('[') {
                continue;
            }
            let (key, value) = line.split_once('=').ok_or("bad line in the access")?;
            let value = value.trim();
            match key.trim() {
                "PrivateKey" => tunnel.private_key = value.into(),
                "Address" => tunnel.address = value.into(),
                "DNS" => tunnel.dns = value.split(',').map(|v| v.trim().to_string()).collect(),
                "MTU" => tunnel.mtu = value.parse().map_err(|_| "bad MTU")?,
                "PublicKey" => tunnel.peer_key = value.into(),
                "PresharedKey" => tunnel.preshared_key = value.into(),
                "Endpoint" => tunnel.endpoint = value.into(),
                "AllowedIPs" => tunnel.allowed_ips = value.split(',').map(|v| v.trim().to_string()).collect(),
                "PersistentKeepalive" => {}
                _ => return Err("unknown entry in the access".into()),
            }
        }
        tunnel.check()?;
        Ok(tunnel)
    }

    fn check(&self) -> Result<(), String> {
        for key in [&self.private_key, &self.peer_key, &self.preshared_key] {
            if !valid_key(key) {
                return Err("bad key in the access".into());
            }
        }
        if !valid_cidr(&self.address) || !self.address.ends_with("/32") {
            return Err("bad address in the access".into());
        }
        if !valid_endpoint(&self.endpoint) {
            return Err("bad endpoint in the access".into());
        }
        if self.allowed_ips.is_empty() || !self.allowed_ips.iter().all(|c| valid_cidr(c)) {
            return Err("bad routes in the access".into());
        }
        if !self.dns.iter().all(|d| d.parse::<IpAddr>().is_ok()) {
            return Err("bad DNS in the access".into());
        }
        if !(576..=1500).contains(&self.mtu) {
            return Err("bad MTU in the access".into());
        }
        Ok(())
    }

    pub fn full(&self) -> bool {
        self.allowed_ips.iter().any(|c| c == "0.0.0.0/0")
    }
}

/// `host:port` with the host as an address, as WireGuard wants it.
pub fn resolve(endpoint: &str) -> Option<String> {
    let address = endpoint.to_socket_addrs().ok()?.next()?;
    Some(address.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    const KEY: &str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";

    fn text() -> String {
        format!("[Interface]\n# name\nPrivateKey = {KEY}\nAddress = 10.8.0.3/32\nDNS = 10.8.0.1\n\n[Peer]\nPublicKey = {KEY}\nPresharedKey = {KEY}\nEndpoint = home.example:51820\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 25\n")
    }

    #[test]
    fn a_real_access_is_understood() {
        let tunnel = Tunnel::parse(&text()).unwrap();
        assert_eq!(tunnel.address, "10.8.0.3/32");
        assert!(tunnel.full());
    }

    #[test]
    fn nothing_can_smuggle_in_an_option_or_a_command() {
        for bad in [
            text().replace("home.example:51820", "x;reboot:51820"),
            text().replace("home.example:51820", "--help:51820"),
            text().replace("10.8.0.3/32", "10.8.0.3/32 dev lo"),
            text().replace("10.8.0.1", "10.8.0.1; rm"),
            text().replace("0.0.0.0/0, ::/0", "$(id)"),
            text().replace(KEY, "short"),
            text() + "PostUp = id\n",
        ] {
            assert!(Tunnel::parse(&bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn endpoints() {
        assert!(valid_endpoint("home.example:51820") && valid_endpoint("[2001:db8::1]:9"));
        assert!(!valid_endpoint("home.example") && !valid_endpoint("a b:1") && !valid_endpoint("h:99999"));
    }
}

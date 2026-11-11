//! Configuration of both sides, as JSON. The server decides what is allowed: a client can
//! only open the ports the server lists for it, and never one below 1024.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};

pub const MIN_PORT: u16 = 1024;
pub const MAX_CLIENTS: usize = 64;
pub const MAX_FORWARDS: usize = 32;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AllowedPort {
    pub port: u16,
    /// Where the public port listens; 0.0.0.0 for everyone, 127.0.0.1 for this server only.
    #[serde(default = "any_address")]
    pub bind: String,
}

fn any_address() -> String {
    "0.0.0.0".into()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ClientEntry {
    pub name: String,
    /// SHA-256 of the token, hex. The token itself never needs to be on the server.
    pub token_sha256: String,
    #[serde(default)]
    pub ports: Vec<AllowedPort>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ServerConfig {
    pub listen: String,
    pub cert: PathBuf,
    pub key: PathBuf,
    #[serde(default)]
    pub status: Option<PathBuf>,
    #[serde(default)]
    pub clients: Vec<ClientEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Forward {
    pub remote_port: u16,
    /// The service on the client's side, such as 127.0.0.1:80 or 192.168.1.20:8123.
    pub local: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ClientConfig {
    pub server: String,
    /// SHA-256 of the server's certificate, as `pgproxy init` prints it.
    pub fingerprint: String,
    pub name: String,
    pub token: String,
    #[serde(default)]
    pub forwards: Vec<Forward>,
    #[serde(default)]
    pub status: Option<PathBuf>,
}

pub fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 40
        && name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
}

fn hex64(text: &str) -> bool {
    text.len() == 64 && text.chars().all(|c| c.is_ascii_hexdigit())
}

impl ServerConfig {
    pub fn load(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let config: Self = serde_json::from_str(&text).map_err(|e| format!("config: {e}"))?;
        config.check()?;
        Ok(config)
    }

    pub fn check(&self) -> Result<(), String> {
        self.listen
            .parse::<SocketAddr>()
            .map_err(|_| format!("listen: not an address: {}", self.listen))?;
        if self.clients.len() > MAX_CLIENTS {
            return Err(format!("at most {MAX_CLIENTS} clients"));
        }
        let mut names = HashSet::new();
        let mut ports = HashSet::new();
        let control_port = self
            .listen
            .parse::<SocketAddr>()
            .map(|a| a.port())
            .unwrap_or(0);
        for client in &self.clients {
            if !valid_name(&client.name) {
                return Err(format!("client name: {:?}", client.name));
            }
            if !names.insert(client.name.as_str()) {
                return Err(format!("client {} is listed twice", client.name));
            }
            if !hex64(&client.token_sha256) {
                return Err(format!(
                    "client {}: token_sha256 must be 64 hex characters",
                    client.name
                ));
            }
            if client.ports.len() > MAX_FORWARDS {
                return Err(format!(
                    "client {}: at most {MAX_FORWARDS} ports",
                    client.name
                ));
            }
            for allowed in &client.ports {
                if allowed.port < MIN_PORT {
                    return Err(format!(
                        "port {}: below {MIN_PORT} is not allowed",
                        allowed.port
                    ));
                }
                if allowed.port == control_port {
                    return Err(format!("port {} is the control port", allowed.port));
                }
                format!("{}:{}", allowed.bind, allowed.port)
                    .parse::<SocketAddr>()
                    .map_err(|_| format!("bind: not an address: {}", allowed.bind))?;
                if !ports.insert(allowed.port) {
                    return Err(format!("port {} is given to two clients", allowed.port));
                }
            }
        }
        Ok(())
    }

    pub fn client(&self, name: &str) -> Option<&ClientEntry> {
        self.clients.iter().find(|c| c.name == name)
    }
}

impl ClientConfig {
    pub fn load(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let config: Self = serde_json::from_str(&text).map_err(|e| format!("config: {e}"))?;
        config.check()?;
        Ok(config)
    }

    pub fn check(&self) -> Result<(), String> {
        if !valid_name(&self.name) {
            return Err(format!("name: {:?}", self.name));
        }
        if self.token.len() < 32 || self.token.len() > 128 {
            return Err("token: 32 to 128 characters".into());
        }
        if crate::tls::parse_fingerprint(&self.fingerprint).is_none() {
            return Err("fingerprint: SHA-256 as hex, with or without colons".into());
        }
        if self.forwards.len() > MAX_FORWARDS {
            return Err(format!("at most {MAX_FORWARDS} forwards"));
        }
        let mut seen = HashSet::new();
        for forward in &self.forwards {
            if !seen.insert(forward.remote_port) {
                return Err(format!("remote port {} twice", forward.remote_port));
            }
            if forward.local.is_empty() || forward.local.len() > 200 || !forward.local.contains(':')
            {
                return Err(format!(
                    "local: host:port expected, got {:?}",
                    forward.local
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn server() -> ServerConfig {
        ServerConfig {
            listen: "0.0.0.0:7000".into(),
            cert: "c".into(),
            key: "k".into(),
            status: None,
            clients: vec![ClientEntry {
                name: "arch".into(),
                token_sha256: "a".repeat(64),
                ports: vec![AllowedPort {
                    port: 8080,
                    bind: "0.0.0.0".into(),
                }],
            }],
        }
    }

    #[test]
    fn a_good_server_config_passes() {
        assert_eq!(server().check(), Ok(()));
    }

    #[test]
    fn low_ports_twice_given_ports_and_bad_names_are_refused() {
        let mut low = server();
        low.clients[0].ports[0].port = 22;
        assert!(low.check().is_err());
        let mut twice = server();
        let mut other = twice.clients[0].clone();
        other.name = "pi".into();
        twice.clients.push(other);
        assert!(twice.check().unwrap_err().contains("two clients"));
        let mut name = server();
        name.clients[0].name = "../etc".into();
        assert!(name.check().is_err());
        let mut control = server();
        control.clients[0].ports[0].port = 7000;
        assert!(control.check().is_err());
        let mut token = server();
        token.clients[0].token_sha256 = "secret".into();
        assert!(token.check().is_err());
    }

    #[test]
    fn unknown_fields_are_refused() {
        let text = r#"{"listen":"0.0.0.0:7000","cert":"c","key":"k","clients":[],"open":true}"#;
        assert!(serde_json::from_str::<ServerConfig>(text).is_err());
    }
}

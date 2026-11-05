//! Post-quantum keys with Rosenpass. It runs through the tunnel itself, so no second port is needed
//! anywhere, and every way home works. Every two minutes or so both sides get a new key, which
//! WireGuard mixes into its own: recording the traffic today does not help a quantum computer later.

use crate::sys::set_preshared_key;
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

pub const PORT: u16 = 9999;
pub const PUBLIC_KEY_SIZE: u64 = 524_160;

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

/// The key file a line of Rosenpass names, if it says a new key is there.
pub fn exchanged(line: &str) -> Option<PathBuf> {
    let rest = line.strip_prefix("output-key peer ")?;
    let start = rest.find("key-file \"")? + 10;
    let end = rest[start..].find('"')? + start;
    rest[end..].ends_with("\" exchanged").then(|| PathBuf::from(&rest[start..end]))
}

pub fn valid_psk(key: &str) -> bool {
    crate::config::valid_key(key)
}

pub fn spawn(binary: &str, config: &Path) -> Result<Child, String> {
    Command::new(binary).arg("exchange-config").arg(config).env_clear()
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|e| format!("rosenpass: {e}"))
}

pub fn gen_keys(binary: &str, public: &Path, secret: &Path) -> Result<(), String> {
    let status = Command::new(binary).arg("gen-keys").arg("-p").arg(public).arg("-s").arg(secret)
        .env_clear().stdout(Stdio::null()).stderr(Stdio::null()).status().map_err(|e| e.to_string())?;
    if status.success() { Ok(()) } else { Err("rosenpass could not make keys".into()) }
}

#[derive(Default)]
pub struct Shared {
    pub last: u64,
    pub key: Option<String>,
}

/// This computer's side.
pub struct Client {
    child: Child,
    pub shared: Arc<Mutex<Shared>>,
}

impl Client {
    pub fn start(binary: &str, folder: &Path, server_ip: &str, interface: &str, peer: &str) -> Result<Client, String> {
        let out = folder.join("key.out");
        let config = folder.join("rosenpass.toml");
        fs::write(&config, format!(
            "public_key = \"{0}/client.pk\"\nsecret_key = \"{0}/client.sk\"\nlisten = []\nverbosity = \"Quiet\"\n\n\
             [[peers]]\npublic_key = \"{0}/server.pk\"\nendpoint = \"{server_ip}:{PORT}\"\nkey_out = \"{1}\"\n",
            folder.display(), out.display()
        )).map_err(|e| e.to_string())?;
        let mut child = spawn(binary, &config)?;
        let stdout = child.stdout.take().ok_or("no output")?;
        let shared = Arc::new(Mutex::new(Shared::default()));
        let (mine, interface, peer, saved) =
            (shared.clone(), interface.to_string(), peer.to_string(), folder.join("last.psk"));
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                let Some(file) = exchanged(&line) else { continue };
                let Ok(key) = fs::read_to_string(&file) else { continue };
                let key = key.trim().to_string();
                if valid_psk(&key) && set_preshared_key(&interface, &peer, &key).is_ok() {
                    // Kept for reconnecting: until the Pi falls back, only this key opens the tunnel.
                    let _ = fs::write(&saved, format!("{} {key}", now()));
                    let mut state = mine.lock().unwrap();
                    state.last = now();
                    state.key = Some(key);
                }
            }
        });
        Ok(Client { child, shared })
    }

    pub fn alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_key_is_recognised() {
        let line = "output-key peer vGHk= key-file \"/run/x/a.out\" exchanged";
        assert_eq!(exchanged(line), Some(PathBuf::from("/run/x/a.out")));
        assert_eq!(exchanged("output-key peer x key-file \"/a\" stale"), None);
        assert_eq!(exchanged("something else"), None);
    }
}

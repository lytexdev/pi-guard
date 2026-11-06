//! What is left of Rosenpass, for devices that have not updated to `exchange` yet.

use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
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

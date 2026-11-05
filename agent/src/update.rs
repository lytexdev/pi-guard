//! New versions from pi-guard, which only passes them on: they must carry the signature of the
//! key below, whose private half never comes to a pi-guard. Older versions are refused too, so
//! nobody can bring back an old fault with a once valid file.

use crate::config::Config;
use crate::{host_and_port, tls_config};
use ring::{digest, signature};
use rustls::pki_types::ServerName;
use serde::Deserialize;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::Command;
use std::sync::Arc;
use std::thread::{self, sleep};
use std::time::Duration;

const PUBLIC_KEY: &str = "7cdf1b203f7747803d04eaf7cdc52350c684f6e5b3b0ea64cd1636b593b8b716";
const FIRST_CHECK: Duration = Duration::from_secs(60);
const CHECK_EVERY: Duration = Duration::from_secs(6 * 3600);
const MAX_SIZE: usize = 60_000_000;

#[derive(Deserialize, Default)]
struct Manifest {
    #[serde(default)]
    version: String,
    #[serde(default)]
    sha256: String,
    #[serde(default)]
    signature: String,
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    if text.len() % 2 != 0 {
        return None;
    }
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(text.get(i..i + 2)?, 16).ok()).collect()
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn parts(version: &str) -> Option<[u64; 3]> {
    let mut numbers = version.split('.').map(|p| p.parse::<u64>().ok());
    let result = [numbers.next()??, numbers.next()??, numbers.next()??];
    numbers.next().is_none().then_some(result)
}

fn newer(candidate: &str, current: &str) -> bool {
    matches!((parts(candidate), parts(current)), (Some(a), Some(b)) if a > b)
}

fn signed(manifest: &Manifest, platform: &str, key: &[u8]) -> bool {
    let message = format!("pi-guard-update|agent|{platform}|{}|{}", manifest.version, manifest.sha256);
    let Some(sig) = unhex(&manifest.signature) else { return false };
    signature::UnparsedPublicKey::new(&signature::ED25519, key).verify(message.as_bytes(), &sig).is_ok()
}

fn get(config: &Config, tls: &Arc<rustls::ClientConfig>, path: &str) -> Result<Vec<u8>, String> {
    let (host, port) = host_and_port(&config.url)?;
    let name = ServerName::try_from(host.clone()).map_err(|e| e.to_string())?;
    let connection = rustls::ClientConnection::new(tls.clone(), name).map_err(|e| e.to_string())?;
    let socket = TcpStream::connect((host.as_str(), port)).map_err(|e| e.to_string())?;
    socket.set_read_timeout(Some(Duration::from_secs(60))).map_err(|e| e.to_string())?;
    let mut stream = rustls::StreamOwned::new(connection, socket);
    write!(stream, "GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n").map_err(|e| e.to_string())?;
    let mut answer = Vec::new();
    let mut buffer = [0u8; 65536];
    loop {
        match stream.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => answer.extend_from_slice(&buffer[..count]),
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => break, // what counts is the signature
            Err(e) => return Err(e.to_string()),
        }
        if answer.len() > MAX_SIZE {
            return Err("too large".into());
        }
    }
    let split = answer.windows(4).position(|w| w == b"\r\n\r\n").ok_or("no answer")?;
    let head = String::from_utf8_lossy(&answer[..split]);
    if !head.lines().next().is_some_and(|line| line.contains(" 200")) {
        return Err(format!("answered: {}", head.lines().next().unwrap_or("")));
    }
    Ok(answer[split + 4..].to_vec())
}

fn install(file: &[u8], version: &str) -> Result<(), String> {
    let current = std::env::current_exe().map_err(|e| e.to_string())?;
    let next = current.with_extension("new");
    std::fs::write(&next, file).map_err(|e| e.to_string())?;
    std::fs::set_permissions(&next, std::fs::Permissions::from_mode(0o755)).map_err(|e| e.to_string())?;
    if !works(&next, version) {
        let _ = std::fs::remove_file(&next);
        return Err("the new version does not run here".into());
    }
    std::fs::rename(&next, &current).map_err(|e| e.to_string())
}

/// The running one stays until the new one proved it starts on this very system.
fn works(file: &Path, version: &str) -> bool {
    Command::new(file).arg("--version").output().is_ok_and(|out| {
        out.status.success() && String::from_utf8_lossy(&out.stdout).trim() == version
    })
}

fn check(config: &Config) -> Result<bool, String> {
    let Some(tls) = tls_config(config)? else { return Ok(false) };
    let platform = format!("linux-{}", std::env::consts::ARCH);
    let base = format!("/api/updates/agent/{platform}");
    let manifest: Manifest = serde_json::from_slice(&get(config, &tls, &base)?).map_err(|e| e.to_string())?;
    if !newer(&manifest.version, env!("CARGO_PKG_VERSION")) {
        return Ok(false);
    }
    let key = unhex(PUBLIC_KEY).ok_or("bad key")?;
    if !signed(&manifest, &platform, &key) {
        return Err(format!("{} has no valid signature, refused", manifest.version));
    }
    let file = get(config, &tls, &format!("{base}/file"))?;
    if hex(digest::digest(&digest::SHA256, &file).as_ref()) != manifest.sha256 {
        return Err("the file does not match its signature, refused".into());
    }
    install(&file, &manifest.version)?;
    eprintln!("updated to {}, restarting", manifest.version);
    Ok(true)
}

pub fn start(config: Config) {
    thread::spawn(move || {
        sleep(FIRST_CHECK);
        loop {
            match check(&config) {
                Ok(true) => std::process::exit(0), // systemd starts the new version
                Ok(false) => {}
                Err(error) => eprintln!("update: {error}"),
            }
            sleep(CHECK_EVERY);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use ring::rand::SystemRandom;
    use ring::signature::{Ed25519KeyPair, KeyPair};

    fn release(pair: &Ed25519KeyPair, product: &str, version: &str) -> Manifest {
        let sha256 = "ab".repeat(32);
        let message = format!("pi-guard-update|{product}|linux-x86_64|{version}|{sha256}");
        Manifest { version: version.into(), sha256, signature: hex(pair.sign(message.as_bytes()).as_ref()) }
    }

    fn pair() -> Ed25519KeyPair {
        let bytes = Ed25519KeyPair::generate_pkcs8(&SystemRandom::new()).unwrap();
        Ed25519KeyPair::from_pkcs8(bytes.as_ref()).unwrap()
    }

    #[test]
    fn a_signed_release_counts() {
        let key = pair();
        assert!(signed(&release(&key, "agent", "1.2.3"), "linux-x86_64", key.public_key().as_ref()));
    }

    #[test]
    fn anyone_elses_signature_does_not() {
        let (ours, theirs) = (pair(), pair());
        assert!(!signed(&release(&theirs, "agent", "1.2.3"), "linux-x86_64", ours.public_key().as_ref()));
    }

    #[test]
    fn nothing_signed_can_be_changed() {
        let key = pair();
        let public = key.public_key().as_ref().to_vec();
        let mut other_file = release(&key, "agent", "1.2.3");
        other_file.sha256 = "cd".repeat(32);
        assert!(!signed(&other_file, "linux-x86_64", &public));
        let mut other_version = release(&key, "agent", "1.2.3");
        other_version.version = "9.9.9".into();
        assert!(!signed(&other_version, "linux-x86_64", &public));
        assert!(!signed(&release(&key, "agent", "1.2.3"), "linux-aarch64", &public));
    }

    #[test]
    fn a_release_of_another_product_does_not_count() {
        let key = pair();
        assert!(!signed(&release(&key, "desktop", "1.2.3"), "linux-x86_64", key.public_key().as_ref()));
    }

    #[test]
    fn only_newer_versions() {
        assert!(newer("0.2.1", "0.2.0") && newer("0.10.0", "0.9.0") && newer("1.0.0", "0.9.9"));
        assert!(!newer("0.2.0", "0.2.0") && !newer("0.1.9", "0.2.0"));
        assert!(!newer("", "0.2.0") && !newer("1.2", "0.2.0") && !newer("1.2.3.4", "0.2.0") && !newer("x.y.z", "0.2.0"));
    }

    #[test]
    fn a_broken_file_is_not_taken_for_working() {
        assert!(!works(Path::new("/does/not/exist"), "1.0.0"));
        assert!(!works(Path::new("/bin/false"), "1.0.0"));
    }

    #[test]
    fn the_built_in_key_is_a_key() {
        assert_eq!(unhex(PUBLIC_KEY).unwrap().len(), 32);
    }
}

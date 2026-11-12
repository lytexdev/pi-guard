//! The Pi's side of Rosenpass. pi-guard runs without privileges and only drops files here, so
//! nothing in them is trusted: names must be keys, and files are copied before they are used.
//!
//! The rule that keeps people from being locked out: a key only counts while a device is around.
//! If its tunnel goes silent, the Pi returns to the device's starting key, because a device that
//! restarted may only know that one, and Rosenpass runs inside the tunnel.

use crate::b64;
use crate::config::valid_key;
use crate::pq::{self, exchanged, now, valid_psk, PORT, PUBLIC_KEY_SIZE};
use crate::sys::{read, set_preshared_key, set_preshared_key_with};
use std::collections::{BTreeMap, HashMap};
use std::fs;
use pi_guard_kem::{pi, KEY_SIZE};
use std::io::{BufRead, BufReader, Read};
use std::net::{IpAddr, TcpListener, TcpStream};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::Child;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, sleep};
use std::time::Duration;

const INTERFACE: &str = "wg0";
/// The same devices over AmneziaWG (scripts/amneziawg.sh), when set up. Its keys must follow wg0's,
/// and a device greeting only there is not silent.
const HIDDEN: &str = "awg0";
const LISTEN: &str = "10.8.0.1";
const DATA: &str = "/var/lib/pi-guard/wireguard";
const OWN: &str = "/etc/pi-guard-vpn";
const WORK: &str = "/run/pi-guard-vpn-server";
// What pi-guard may read. Root never writes into pi-guard's own folders: someone with its rights
// could leave a link there, and root would overwrite whatever it points to.
const PUBLIC: &str = "/run/pi-guard-vpn-public";
const MAX_PEERS_FILE: u64 = 1_000_000;
const STALE_SECONDS: u64 = 200; // WireGuard renews its session every two minutes while connected
const TICK: Duration = Duration::from_secs(15);

const WAIT: Duration = Duration::from_secs(8);
const MOST_AT_ONCE: usize = 4;
const MIN_PAUSE: u64 = 20; // per device: a key every two minutes is the plan, not a loop

type Keys = Arc<Mutex<HashMap<String, (String, u64)>>>;

#[cfg(test)]
fn hex(key: &str) -> Option<String> {
    let raw = crate::b64::decode(key)?;
    Some(raw.iter().map(|b| format!("{b:02x}")).collect())
}

fn from_hex(text: &str) -> Option<String> {
    if text.len() != 64 {
        return None;
    }
    let raw: Option<Vec<u8>> = (0..64).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).ok()).collect();
    Some(crate::b64::encode(&raw?))
}

/// Starting keys, from the file pi-guard hands to the root job anyway.
fn starting_keys() -> BTreeMap<String, String> {
    let mut text = String::new();
    if let Ok(file) = fs::File::open(format!("{DATA}/peers")) {
        let _ = file.take(MAX_PEERS_FILE).read_to_string(&mut text);
    }
    text.lines().filter_map(|line| {
        let mut parts = line.split('\t');
        let (key, psk) = (parts.next()?, parts.next()?);
        (valid_key(key) && valid_key(psk)).then(|| (key.to_string(), psk.to_string()))
    }).collect()
}

/// A public key uploaded for a device, safely: no links, the exact size, and a copy of our own.
fn take_public_key(file: &Path, target: &Path) -> bool {
    let opened = fs::OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW).open(file);
    let Ok(mut source) = opened else { return false };
    if source.metadata().map_or(true, |m| !m.is_file() || m.len() != PUBLIC_KEY_SIZE) {
        return false;
    }
    let Ok(mut copy) = fs::File::create(target) else { return false };
    std::io::copy(&mut source, &mut copy).is_ok_and(|n| n == PUBLIC_KEY_SIZE)
}

fn devices() -> Vec<String> {
    let starting = starting_keys();
    let Ok(entries) = fs::read_dir(format!("{DATA}/pq/peers")) else { return vec![] };
    let mut found: Vec<String> = entries.filter_map(Result::ok)
        .filter_map(|e| e.file_name().to_str()?.strip_suffix(".pub").map(str::to_string))
        .filter(|name| from_hex(name).is_some_and(|key| starting.contains_key(&key)))
        .collect();
    found.sort();
    found
}

fn stop(child: &mut Option<Child>) {
    if let Some(mut running) = child.take() {
        let _ = running.kill();
        let _ = running.wait();
    }
}

fn start(binary: &str, names: &[String], keys: &Keys) -> Option<Child> {
    let work = Path::new(WORK);
    let mut config = format!(
        "public_key = \"{OWN}/server.pk\"\nsecret_key = \"{OWN}/server.sk\"\nlisten = [\"{LISTEN}:{PORT}\"]\nverbosity = \"Quiet\"\n"
    );
    let mut usable = 0;
    for name in names {
        let copy = work.join(format!("{name}.pk"));
        if take_public_key(&Path::new(DATA).join("pq/peers").join(format!("{name}.pub")), &copy) {
            config += &format!("\n[[peers]]\npublic_key = \"{}\"\nkey_out = \"{WORK}/{name}.out\"\n", copy.display());
            usable += 1;
        }
    }
    if usable == 0 {
        return None;
    }
    let file = work.join("rosenpass.toml");
    fs::write(&file, config).ok()?;
    let mut child = pq::spawn(binary, &file).map_err(|e| eprintln!("{e}")).ok()?;
    let stdout = child.stdout.take()?;
    let keys = keys.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let Some(path) = exchanged(&line) else { continue };
            take(&path, &keys);
        }
    });
    Some(child)
}

fn hidden_up() -> bool {
    Path::new("/sys/class/net").join(HIDDEN).exists()
}

/// On wg0, and on awg0 at once, so a device there does not lose its tunnel until the sync copies it.
fn set_key(peer: &str, psk: &str) -> Result<(), String> {
    set_preshared_key(INTERFACE, peer, psk)?;
    if hidden_up() {
        let _ = set_preshared_key_with("awg", HIDDEN, peer, psk);
    }
    Ok(())
}

fn take(path: &PathBuf, keys: &Keys) {
    let Some(name) = path.file_stem().and_then(|n| n.to_str()) else { return };
    let (Some(peer), Ok(text)) = (from_hex(name), fs::read_to_string(path)) else { return };
    let psk = text.trim().to_string();
    if valid_psk(&psk) && set_key(&peer, &psk).is_ok() {
        keys.lock().unwrap().insert(peer, (psk, now()));
    }
}

/// Which device owns an address in the tunnel. WireGuard vouches for it: a packet from anyone else
/// with that address does not get through.
fn peer_at(allowed_ips: &str, address: &IpAddr) -> Option<String> {
    let own = format!("{address}/32");
    allowed_ips.lines().find_map(|line| {
        let (key, addresses) = line.split_once('\t')?;
        addresses.split_whitespace().any(|a| a == own).then(|| key.to_string())
    })
}

fn exchange(mut stream: TcpStream, keys: &Keys, last: &Mutex<HashMap<String, u64>>) -> Option<()> {
    stream.set_read_timeout(Some(WAIT)).ok()?;
    stream.set_write_timeout(Some(WAIT)).ok()?;
    let address = stream.peer_addr().ok()?.ip();
    let allowed = read("wg", &["show", INTERFACE, "allowed-ips"]).ok()?;
    let peer = peer_at(&allowed, &address)?;
    {
        let mut last = last.lock().unwrap();
        let time = last.entry(peer.clone()).or_insert(0);
        if now().saturating_sub(*time) < MIN_PAUSE {
            return None;
        }
        *time = now();
    }
    let start = b64::decode(starting_keys().get(&peer)?)?;
    let key = pi(&mut stream, &<[u8; KEY_SIZE]>::try_from(start).ok()?).ok()?;
    let psk = b64::encode(key.as_slice());
    set_key(&peer, &psk).ok()?;
    keys.lock().unwrap().insert(peer, (psk, now()));
    Some(())
}

/// Reachable only inside the tunnel: it listens on the tunnel's own address.
fn listen(keys: Keys) {
    let last: Arc<Mutex<HashMap<String, u64>>> = Arc::default();
    let running = Arc::new(AtomicUsize::new(0));
    loop {
        let Ok(listener) = TcpListener::bind((LISTEN, pi_guard_kem::PORT)) else {
            sleep(TICK); // the tunnel is not up yet
            continue;
        };
        for stream in listener.incoming().map_while(Result::ok) {
            if !Path::new(DATA).join("pq/enabled").exists() || running.load(Ordering::Acquire) >= MOST_AT_ONCE {
                continue;
            }
            running.fetch_add(1, Ordering::AcqRel);
            let (keys, last, running) = (keys.clone(), last.clone(), running.clone());
            thread::spawn(move || {
                exchange(stream, &keys, &last);
                running.fetch_sub(1, Ordering::AcqRel);
            });
        }
    }
}

fn parse_handshakes(text: &str, into: &mut HashMap<String, u64>) {
    for line in text.lines() {
        let Some((key, time)) = line.split_once('\t') else { continue };
        let Ok(time) = time.parse::<u64>() else { continue };
        let seen = into.entry(key.to_string()).or_insert(0);
        *seen = (*seen).max(time);
    }
}

/// The newest greeting of each device, over WireGuard or AmneziaWG.
fn handshakes() -> HashMap<String, u64> {
    let mut seen = HashMap::new();
    parse_handshakes(&read("wg", &["show", INTERFACE, "latest-handshakes"]).unwrap_or_default(), &mut seen);
    if hidden_up() {
        parse_handshakes(&read("awg", &["show", HIDDEN, "latest-handshakes"]).unwrap_or_default(), &mut seen);
    }
    seen
}

fn write_status(active: usize) {
    let _ = fs::write(format!("{PUBLIC}/status"), format!("{{\"active\": {active}, \"written\": {}}}\n", now()));
}

pub fn run(binary: &str) -> ! {
    let own = Path::new(OWN);
    fs::create_dir_all(own).ok();
    if !own.join("server.sk").exists() {
        if let Err(error) = pq::gen_keys(binary, &own.join("server.pk"), &own.join("server.sk")) {
            eprintln!("{error}");
            std::process::exit(1);
        }
        let _ = fs::set_permissions(own.join("server.sk"), fs::Permissions::from_mode(0o600));
    }
    fs::create_dir_all(WORK).ok();
    let _ = fs::set_permissions(WORK, fs::Permissions::from_mode(0o700));
    fs::create_dir_all(PUBLIC).ok();
    let _ = fs::set_permissions(PUBLIC, fs::Permissions::from_mode(0o755));
    let _ = fs::copy(own.join("server.pk"), format!("{PUBLIC}/server.pub"));
    let _ = fs::set_permissions(format!("{PUBLIC}/server.pub"), fs::Permissions::from_mode(0o644));

    let keys: Keys = Arc::default();
    let listening = keys.clone();
    thread::spawn(move || listen(listening));
    let mut child: Option<Child> = None;
    let mut running: Vec<String> = vec![];
    loop {
        let enabled = Path::new(DATA).join("pq/enabled").exists();
        let wanted = if enabled { devices() } else { vec![] };
        let dead = child.as_mut().is_some_and(|c| !matches!(c.try_wait(), Ok(None)));
        if wanted != running || dead {
            stop(&mut child);
            child = if wanted.is_empty() { None } else { start(binary, &wanted, &keys) };
            running = wanted;
        }
        let seen = handshakes();
        let starting = starting_keys();
        let mut held = keys.lock().unwrap();
        held.retain(|peer, (psk, _)| {
            let silent = now().saturating_sub(seen.get(peer).copied().unwrap_or(0)) > STALE_SECONDS;
            if silent || !enabled {
                if let Some(first) = starting.get(peer) {
                    let _ = set_key(peer, first);
                }
                return false;
            }
            // The root job that loads devices writes starting keys; put the current one back.
            let _ = set_key(peer, psk);
            true
        });
        write_status(held.len());
        drop(held);
        sleep(TICK);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_device_greeting_over_amneziawg_is_not_silent() {
        let mut seen = HashMap::new();
        parse_handshakes("KEYA=\t100\nKEYB=\t900\n", &mut seen); // wg0
        parse_handshakes("KEYA=\t700\nKEYB=\t0\nbroken\n", &mut seen); // awg0
        assert_eq!(seen.get("KEYA=").copied(), Some(700));
        assert_eq!(seen.get("KEYB=").copied(), Some(900));
    }

    #[test]
    fn a_device_is_found_by_its_address_in_the_tunnel() {
        let text = "keyA=\t10.8.0.2/32\nkeyB=\t10.8.0.3/32 fd00::3/128\nkeyC=\t(none)\n";
        assert_eq!(peer_at(text, &"10.8.0.3".parse().unwrap()).as_deref(), Some("keyB="));
        assert_eq!(peer_at(text, &"10.8.0.9".parse().unwrap()), None);
    }

    #[test]
    fn an_address_is_not_mistaken_for_a_longer_one() {
        assert_eq!(peer_at("keyA=\t10.8.0.20/32\n", &"10.8.0.2".parse().unwrap()), None);
    }

    #[test]
    fn a_whole_network_of_a_device_does_not_count() {
        assert_eq!(peer_at("keyA=\t10.8.0.0/24\n", &"10.8.0.0".parse().unwrap()), None);
    }

    #[test]
    fn device_names_are_keys_and_nothing_else() {
        let key = "fN8bID93R4A9BOr3zcUjUMaE9uWzsOpkzRY2tZO4txY=";
        let name = hex(key).unwrap();
        assert_eq!(from_hex(&name).unwrap(), key);
        assert_eq!(from_hex("../../etc/passwd"), None);
        assert_eq!(from_hex(&"zz".repeat(32)), None);
    }

    #[test]
    fn a_link_or_a_wrong_size_is_not_taken() {
        let dir = std::env::temp_dir().join(format!("pq-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let (good, small, link) = (dir.join("g"), dir.join("s"), dir.join("l"));
        fs::write(&good, vec![0u8; PUBLIC_KEY_SIZE as usize]).unwrap();
        fs::write(&small, b"short").unwrap();
        let _ = std::os::unix::fs::symlink(&good, &link);
        let target = dir.join("t");
        assert!(take_public_key(&good, &target));
        assert!(!take_public_key(&small, &target));
        assert!(!take_public_key(&link, &target));
        fs::remove_dir_all(dir).unwrap();
    }
}

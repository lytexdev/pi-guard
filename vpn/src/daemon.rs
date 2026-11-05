//! The service: keeps the wanted state, and answers the app on a local socket.

use crate::config::{resolve, valid_endpoint, Tunnel, Way};
use crate::firewall::{self, Plan, IFACE, SPLIT_GROUP};
use crate::pq::{self, now, valid_psk};
use crate::{b64, tunnel};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::io::AsRawFd;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread::{self, sleep};
use std::time::Duration;

pub const SOCKET: &str = "/run/pi-guard-vpn.sock";
const STATE: &str = "/var/lib/pi-guard-vpn";
const USERS: &str = "/etc/pi-guard-vpn/users";
const CGROUP: &str = "/sys/fs/cgroup";
const HANDSHAKE_FRESH: u64 = 180;
const TRY_SECONDS: u64 = 12;
const MAX_REQUEST: u64 = 2_000_000;
const LAST_KEY_VALID: u64 = 190; // the Pi returns to the starting key a little later

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct Options {
    pub kill_switch: bool,
    pub lockdown: bool,
    pub allow_lan: bool,
    pub auto_connect: bool,
    pub quantum: bool,
}

impl Default for Options {
    fn default() -> Self {
        Options { kill_switch: true, lockdown: false, allow_lan: true, auto_connect: false, quantum: true }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
struct Stored {
    config: Option<Tunnel>,
    ways: Vec<Way>,
    options: Options,
    wanted: bool,
    resolved: HashMap<String, String>,
}

struct Service {
    stored: Stored,
    rosenpass: String,
    attempt: usize,
    tried_at: u64,
    way: Option<Way>,
    pq: Option<pq::Client>,
    error: Option<String>,
    applied: Option<Plan>,
}

fn folder() -> PathBuf {
    PathBuf::from(STATE)
}

impl Service {
    fn save(&self) {
        let path = folder().join("state.json");
        if let Ok(text) = serde_json::to_vec(&self.stored) {
            let temporary = folder().join("state.tmp");
            if fs::write(&temporary, text).is_ok() {
                let _ = fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600));
                let _ = fs::rename(&temporary, path);
            }
        }
    }

    fn plan(&self, resolving: bool) -> Plan {
        let o = &self.stored.options;
        Plan {
            block: o.lockdown || (o.kill_switch && self.stored.wanted),
            allow_lan: o.allow_lan,
            split: tunnel::exists() && Path::new(CGROUP).join(SPLIT_GROUP).is_dir(),
            resolving,
        }
    }

    fn firewall(&mut self, resolving: bool) {
        let plan = self.plan(resolving);
        if self.applied.as_ref() == Some(&plan) {
            return;
        }
        match firewall::apply(&plan) {
            Ok(()) => self.applied = Some(plan),
            Err(error) => self.error = Some(error),
        }
    }

    /// Way and key to try. Ways go round, and for each the last key, then the starting one.
    fn candidate(&self, tunnel: &Tunnel) -> (Way, String) {
        let ways = if self.stored.ways.is_empty() {
            vec![Way { kind: "ipv4".into(), endpoint: tunnel.endpoint.clone(), mtu: 0 }]
        } else {
            self.stored.ways.clone()
        };
        let last = last_key();
        let keys: Vec<String> = last.into_iter().chain([tunnel.preshared_key.clone()]).collect();
        let index = self.attempt % (ways.len() * keys.len());
        (ways[index / keys.len()].clone(), keys[index % keys.len()].clone())
    }

    fn address(&mut self, endpoint: &str) -> Option<String> {
        // What worked before comes first: under a kill switch the name may not resolve.
        if let Some(known) = self.stored.resolved.get(endpoint) {
            return Some(known.clone());
        }
        self.firewall(true);
        let found = resolve(endpoint);
        self.firewall(false);
        if let Some(address) = &found {
            self.stored.resolved.insert(endpoint.to_string(), address.clone());
        }
        found
    }

    fn attempt_now(&mut self) {
        let Some(config) = self.stored.config.clone() else {
            self.error = Some("no access yet".into());
            return;
        };
        let (way, key) = self.candidate(&config);
        self.attempt += 1;
        self.tried_at = now();
        let Some(address) = self.address(&way.endpoint) else {
            self.error = Some(format!("cannot find {}", way.endpoint));
            return;
        };
        self.pq = None;
        let mut settings = config;
        if way.mtu != 0 {
            settings.mtu = way.mtu;
        }
        match tunnel::up(&settings, &address, &key) {
            Ok(()) => {
                self.way = Some(way);
                self.error = None;
            }
            Err(error) => self.error = Some(error),
        }
        self.applied = None;
        self.firewall(false);
    }

    fn fresh(&self) -> bool {
        tunnel::stats().is_some_and(|s| s.handshake > 0 && now().saturating_sub(s.handshake) < HANDSHAKE_FRESH)
    }

    fn quantum(&mut self) {
        let wanted = self.stored.options.quantum && folder().join("server.pk").exists();
        if !wanted {
            self.pq = None;
            return;
        }
        if self.pq.as_mut().is_some_and(pq::Client::alive) {
            return;
        }
        let Some(config) = &self.stored.config else { return };
        let server = config.dns.first().cloned().unwrap_or_else(|| "10.8.0.1".into());
        match pq::Client::start(&self.rosenpass, &folder(), &server, IFACE, &config.peer_key) {
            Ok(client) => self.pq = Some(client),
            Err(error) => self.error = Some(error),
        }
    }

    fn tick(&mut self) {
        if !self.stored.wanted {
            self.pq = None;
            self.firewall(false);
            return;
        }
        if !tunnel::exists() {
            self.attempt_now();
            return;
        }
        if self.fresh() {
            self.attempt = 0;
            self.quantum();
        } else if now().saturating_sub(self.tried_at) >= TRY_SECONDS {
            self.attempt_now();
        }
        self.firewall(false);
    }

    fn status(&self) -> Value {
        let stats = tunnel::stats();
        let fresh = self.fresh();
        let shared = self.pq.as_ref().map(|c| c.shared.lock().unwrap().last).unwrap_or(0);
        json!({
            "state": if !self.stored.wanted { "off" } else if fresh { "on" } else { "connecting" },
            "way": self.way.as_ref().filter(|_| fresh).map(|w| w.kind.clone()),
            "handshake": stats.as_ref().map(|s| s.handshake), "received": stats.as_ref().map(|s| s.received),
            "sent": stats.as_ref().map(|s| s.sent), "options": self.stored.options,
            "blocking": self.plan(false).block, "configured": self.stored.config.is_some(),
            "quantum": { "active": shared > 0 && now().saturating_sub(shared) < 300, "last": shared,
                         "ready": folder().join("server.pk").exists() },
            "error": self.error,
        })
    }

    fn connect(&mut self) -> Value {
        self.stored.wanted = true;
        self.attempt = 0;
        self.save();
        self.applied = None;
        self.firewall(false); // closed before anything else happens
        self.attempt_now();
        self.save();
        self.status()
    }

    fn disconnect(&mut self) -> Value {
        self.stored.wanted = false;
        self.pq = None;
        tunnel::down();
        self.way = None;
        self.applied = None;
        self.firewall(false);
        self.save();
        self.status()
    }

    fn handle(&mut self, request: &Value, peer_pid: i32) -> Value {
        match request["cmd"].as_str().unwrap_or("") {
            "status" => self.status(),
            "connect" => self.connect(),
            "disconnect" => self.disconnect(),
            "configure" => self.configure(request),
            "options" => self.set_options(request),
            "public_key" => match fs::read(folder().join("client.pk")) {
                Ok(data) => json!({ "key": b64::encode(&data) }),
                Err(_) => json!({ "error": "no key" }),
            },
            "split" => split(peer_pid),
            _ => json!({ "error": "unknown command" }),
        }
    }

    fn configure(&mut self, request: &Value) -> Value {
        let Some(text) = request["config"].as_str() else { return json!({ "error": "no access" }) };
        let tunnel = match Tunnel::parse(text) {
            Ok(tunnel) => tunnel,
            Err(error) => return json!({ "error": error }),
        };
        let ways: Vec<Way> = serde_json::from_value(request["ways"].clone()).unwrap_or_default();
        if !ways.iter().all(|w| valid_endpoint(&w.endpoint) && (w.mtu == 0 || (576..=1500).contains(&w.mtu))) {
            return json!({ "error": "bad way" });
        }
        if let Some(server) = request["server_key"].as_str() {
            match b64::decode(server) {
                Some(data) if data.len() as u64 == pq::PUBLIC_KEY_SIZE => {
                    if fs::write(folder().join("server.pk"), data).is_err() {
                        return json!({ "error": "cannot store the key" });
                    }
                }
                _ => return json!({ "error": "bad server key" }),
            }
        }
        self.stored.config = Some(tunnel);
        self.stored.ways = ways;
        self.stored.resolved.clear();
        self.save();
        self.status()
    }

    fn set_options(&mut self, request: &Value) -> Value {
        match serde_json::from_value::<Options>(request["options"].clone()) {
            Ok(options) => {
                self.stored.options = options;
                self.save();
                self.applied = None;
                self.firewall(false);
                self.status()
            }
            Err(_) => json!({ "error": "bad options" }),
        }
    }
}

fn last_key() -> Option<String> {
    let text = fs::read_to_string(folder().join("last.psk")).ok()?;
    let (time, key) = text.split_once(' ')?;
    let age = now().saturating_sub(time.parse().ok()?);
    (age < LAST_KEY_VALID && valid_psk(key.trim())).then(|| key.trim().to_string())
}

/// Moves the process that asked past the tunnel. Only itself, which the kernel vouches for.
fn split(pid: i32) -> Value {
    let group = Path::new(CGROUP).join(SPLIT_GROUP);
    if fs::create_dir_all(&group).is_err() {
        return json!({ "error": "no cgroup" });
    }
    match fs::write(group.join("cgroup.procs"), pid.to_string()) {
        Ok(()) => json!({ "ok": true }),
        Err(error) => json!({ "error": error.to_string() }),
    }
}

fn peer(stream: &UnixStream) -> Option<(u32, i32)> {
    let mut credentials = libc::ucred { pid: 0, uid: 0, gid: 0 };
    let mut size = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: the buffer and its size belong together and outlive the call.
    let result = unsafe {
        libc::getsockopt(stream.as_raw_fd(), libc::SOL_SOCKET, libc::SO_PEERCRED,
                         std::ptr::addr_of_mut!(credentials).cast(), &mut size)
    };
    (result == 0).then_some((credentials.uid, credentials.pid))
}

fn allowed(uid: u32) -> bool {
    uid == 0 || fs::read_to_string(USERS).is_ok_and(|text| text.lines().any(|l| l.trim() == uid.to_string()))
}

fn serve(stream: UnixStream, service: &Arc<Mutex<Service>>) {
    let Some((uid, pid)) = peer(&stream) else { return };
    let mut writer = match stream.try_clone() {
        Ok(writer) => writer,
        Err(_) => return,
    };
    if !allowed(uid) {
        let _ = writeln!(writer, "{}", json!({ "error": "not allowed" }));
        return;
    }
    let mut line = String::new();
    if BufReader::new(stream.take(MAX_REQUEST)).read_line(&mut line).is_err() {
        return;
    }
    let answer = match serde_json::from_str::<Value>(&line) {
        Ok(request) => service.lock().unwrap().handle(&request, pid),
        Err(_) => json!({ "error": "bad request" }),
    };
    let _ = writeln!(writer, "{answer}");
}

pub fn run(rosenpass: String) -> ! {
    let state = folder();
    fs::create_dir_all(&state).ok();
    let _ = fs::set_permissions(&state, fs::Permissions::from_mode(0o700));
    let stored: Stored = fs::read(state.join("state.json")).ok()
        .and_then(|text| serde_json::from_slice(&text).ok()).unwrap_or_default();
    if !state.join("client.sk").exists() {
        if let Err(error) = pq::gen_keys(&rosenpass, &state.join("client.pk"), &state.join("client.sk")) {
            eprintln!("{error}");
        }
    }
    let _ = fs::create_dir_all(Path::new(CGROUP).join(SPLIT_GROUP));
    let mut service = Service {
        stored, rosenpass, attempt: 0, tried_at: 0, way: None, pq: None, error: None, applied: None,
    };
    // First thing at every start, before the network is up: lockdown must never have a gap.
    service.firewall(false);
    if service.stored.options.auto_connect {
        service.stored.wanted = true;
    }
    let service = Arc::new(Mutex::new(service));
    let _ = fs::remove_file(SOCKET);
    let listener = UnixListener::bind(SOCKET).unwrap_or_else(|e| {
        eprintln!("{SOCKET}: {e}");
        std::process::exit(1);
    });
    let _ = fs::set_permissions(SOCKET, fs::Permissions::from_mode(0o666)); // who may use it: see allowed()
    let watcher = service.clone();
    thread::spawn(move || loop {
        watcher.lock().unwrap().tick();
        sleep(Duration::from_secs(3));
    });
    for stream in listener.incoming().flatten() {
        let service = service.clone();
        thread::spawn(move || serve(stream, &service));
    }
    std::process::exit(1)
}

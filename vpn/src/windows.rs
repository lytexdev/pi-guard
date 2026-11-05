//! The VPN on Windows. It drives the official WireGuard client instead of bringing a driver of its
//! own: signed, widely used, and for a tunnel of all traffic it blocks everything past it with the
//! Windows Filtering Platform by itself. That is the kill switch here. Lockdown, split tunneling
//! and Rosenpass are not available with it, and the app does not offer them.

use crate::config::{resolve, valid_endpoint, Tunnel, Way};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::ffi::{c_void, OsString};
use std::fs;
use std::os::windows::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::sync::{Arc, Mutex};
use std::thread::{self, sleep};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use windows_service::service::{
    ServiceAccess, ServiceControl, ServiceControlAccept, ServiceErrorControl, ServiceExitCode,
    ServiceInfo, ServiceStartType, ServiceState, ServiceStatus, ServiceType,
};
use windows_service::service_control_handler::{self, ServiceControlHandlerResult};
use windows_service::service_manager::{ServiceManager, ServiceManagerAccess};
use windows_service::{define_windows_service, service_dispatcher};
use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::Security::Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW;
use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
use windows_sys::Win32::Storage::FileSystem::{FlushFileBuffers, ReadFile, WriteFile, PIPE_ACCESS_DUPLEX};
use windows_sys::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_READMODE_BYTE,
    PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_UNLIMITED_INSTANCES, PIPE_WAIT,
};

const NAME: &str = "PiGuardVPN";
const TUNNEL: &str = "pgvpn";
const PIPE: &str = r"\\.\pipe\pi-guard-vpn";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const HANDSHAKE_FRESH: u64 = 180;
const TRY_SECONDS: u64 = 25; // a tunnel service takes a moment to come up
const MAX_REQUEST: usize = 2_000_000;

fn folder() -> PathBuf {
    let base = std::env::var_os("ProgramData").unwrap_or_else(|| OsString::from(r"C:\ProgramData"));
    PathBuf::from(base).join("PiGuard")
}

fn wireguard(name: &str) -> PathBuf {
    let base = std::env::var_os("ProgramFiles").unwrap_or_else(|| OsString::from(r"C:\Program Files"));
    PathBuf::from(base).join("WireGuard").join(name)
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

fn hidden(program: &PathBuf, args: &[&str]) -> Result<String, String> {
    let out = Command::new(program).args(args).creation_flags(CREATE_NO_WINDOW).output()
        .map_err(|e| format!("{}: {e}", program.display()))?;
    if out.status.success() {
        return Ok(String::from_utf8_lossy(&out.stdout).into_owned());
    }
    Err(String::from_utf8_lossy(&out.stderr).trim().to_string())
}

/// The folder holds private keys: only the system and administrators may read it.
fn lock_folder() -> Result<(), String> {
    fs::create_dir_all(folder()).map_err(|e| e.to_string())?;
    let path = folder().display().to_string();
    hidden(&PathBuf::from("icacls"), &[&path, "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"])?;
    Ok(())
}

#[derive(Default, Serialize, Deserialize)]
#[serde(default)]
struct Stored {
    config: Option<Tunnel>,
    ways: Vec<Way>,
    wanted: bool,
    auto_connect: bool,
}

struct Service {
    stored: Stored,
    attempt: usize,
    tried_at: u64,
    way: Option<Way>,
    error: Option<String>,
}

/// The access with one way filled in, for the WireGuard service.
fn render(tunnel: &Tunnel, endpoint: &str, mtu: u32) -> String {
    let dns = if tunnel.dns.is_empty() { String::new() } else { format!("DNS = {}\n", tunnel.dns.join(", ")) };
    format!(
        "[Interface]\nPrivateKey = {}\nAddress = {}\n{dns}MTU = {mtu}\n\n[Peer]\nPublicKey = {}\nPresharedKey = {}\nEndpoint = {endpoint}\nAllowedIPs = {}\nPersistentKeepalive = 25\n",
        tunnel.private_key, tunnel.address, tunnel.peer_key, tunnel.preshared_key, tunnel.allowed_ips.join(", ")
    )
}

fn handshake() -> Option<(u64, u64, u64)> {
    let text = hidden(&wireguard("wg.exe"), &["show", TUNNEL, "dump"]).ok()?;
    let parts: Vec<String> = text.lines().nth(1)?.split('\t').map(str::to_string).collect();
    Some((parts.get(4)?.parse().ok()?, parts.get(5)?.parse().ok()?, parts.get(6)?.parse().ok()?))
}

impl Service {
    fn save(&self) {
        if let Ok(text) = serde_json::to_vec(&self.stored) {
            let _ = fs::write(folder().join("state.json"), text);
        }
    }

    fn down(&self) {
        let _ = hidden(&wireguard("wireguard.exe"), &["/uninstalltunnelservice", TUNNEL]);
    }

    fn attempt_now(&mut self) {
        let Some(config) = self.stored.config.clone() else {
            self.error = Some("no access yet".into());
            return;
        };
        let ways = if self.stored.ways.is_empty() {
            vec![Way { kind: "ipv4".into(), endpoint: config.endpoint.clone(), mtu: 0 }]
        } else {
            self.stored.ways.clone()
        };
        let way = ways[self.attempt % ways.len()].clone();
        self.attempt += 1;
        self.tried_at = now();
        self.down();
        let Some(address) = resolve(&way.endpoint) else {
            self.error = Some(format!("cannot find {}", way.endpoint));
            return;
        };
        let mtu = if way.mtu == 0 { config.mtu } else { way.mtu };
        let path = folder().join(format!("{TUNNEL}.conf"));
        if let Err(error) = fs::write(&path, render(&config, &address, mtu)) {
            self.error = Some(error.to_string());
            return;
        }
        match hidden(&wireguard("wireguard.exe"), &["/installtunnelservice", &path.display().to_string()]) {
            Ok(_) => {
                self.way = Some(way);
                self.error = None;
            }
            Err(error) => self.error = Some(error),
        }
    }

    fn fresh(&self) -> bool {
        handshake().is_some_and(|(time, _, _)| time > 0 && now().saturating_sub(time) < HANDSHAKE_FRESH)
    }

    fn tick(&mut self) {
        if !self.stored.wanted {
            return;
        }
        if self.fresh() {
            self.attempt = 0;
        } else if now().saturating_sub(self.tried_at) >= TRY_SECONDS {
            self.attempt_now();
        }
    }

    fn status(&self) -> Value {
        let stats = handshake();
        let fresh = self.fresh();
        json!({
            "state": if !self.stored.wanted { "off" } else if fresh { "on" } else { "connecting" },
            "way": self.way.as_ref().filter(|_| fresh).map(|w| w.kind.clone()),
            "handshake": stats.map(|s| s.0), "received": stats.map(|s| s.1), "sent": stats.map(|s| s.2),
            "options": { "kill_switch": true, "lockdown": false, "allow_lan": false,
                         "auto_connect": self.stored.auto_connect, "quantum": false },
            "features": ["auto_connect"], "blocking": fresh, "configured": self.stored.config.is_some(),
            "quantum": { "active": false, "last": 0, "ready": false }, "error": self.error,
        })
    }

    fn handle(&mut self, request: &Value) -> Value {
        match request["cmd"].as_str().unwrap_or("") {
            "status" => self.status(),
            "connect" => {
                self.stored.wanted = true;
                self.attempt = 0;
                self.save();
                self.attempt_now();
                self.status()
            }
            "disconnect" => {
                self.stored.wanted = false;
                self.way = None;
                self.down();
                self.save();
                self.status()
            }
            "configure" => self.configure(request),
            "options" => {
                self.stored.auto_connect = request["options"]["auto_connect"].as_bool().unwrap_or(false);
                self.save();
                self.status()
            }
            "public_key" => json!({ "error": "no Rosenpass on Windows" }),
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
        self.stored.config = Some(tunnel);
        self.stored.ways = ways;
        self.save();
        self.status()
    }
}

fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain([0]).collect()
}

/// Only the system, administrators and the user who installed this may talk to the service.
fn create_pipe(user_sid: &str) -> Result<HANDLE, String> {
    let sddl = wide(&format!("D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;{user_sid})"));
    let mut descriptor: *mut c_void = std::ptr::null_mut();
    // SAFETY: the string ends with a zero, and the descriptor is only read by the system.
    let converted = unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), 1, &mut descriptor, std::ptr::null_mut())
    };
    if converted == 0 {
        return Err("no pipe permissions".into());
    }
    let attributes = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    let name = wide(PIPE);
    // SAFETY: all pointers are valid for the duration of the call.
    let handle = unsafe {
        CreateNamedPipeW(name.as_ptr(), PIPE_ACCESS_DUPLEX,
                         PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                         PIPE_UNLIMITED_INSTANCES, 65536, 65536, 0, &attributes)
    };
    if handle == INVALID_HANDLE_VALUE {
        // SAFETY: no arguments.
        return Err(format!("pipe: {}", unsafe { GetLastError() }));
    }
    Ok(handle)
}

fn serve(pipe: HANDLE, service: &Arc<Mutex<Service>>) {
    let mut text = Vec::new();
    let mut buffer = [0u8; 65536];
    while !text.contains(&b'\n') && text.len() < MAX_REQUEST {
        let mut read = 0u32;
        // SAFETY: the buffer outlives the call and its size is given.
        let done = unsafe { ReadFile(pipe, buffer.as_mut_ptr(), buffer.len() as u32, &mut read, std::ptr::null_mut()) };
        if done == 0 || read == 0 {
            break;
        }
        text.extend_from_slice(&buffer[..read as usize]);
    }
    let answer = match serde_json::from_slice::<Value>(text.split(|b| *b == b'\n').next().unwrap_or(&[])) {
        Ok(request) => service.lock().unwrap().handle(&request),
        Err(_) => json!({ "error": "bad request" }),
    };
    let reply = format!("{answer}\n");
    let mut written = 0u32;
    // SAFETY: the text outlives the call and the handle is open.
    unsafe {
        WriteFile(pipe, reply.as_ptr(), reply.len() as u32, &mut written, std::ptr::null_mut());
        FlushFileBuffers(pipe);
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
    }
}

fn listen(service: Arc<Mutex<Service>>, user_sid: String) {
    loop {
        let Ok(pipe) = create_pipe(&user_sid) else {
            sleep(Duration::from_secs(1));
            continue;
        };
        // SAFETY: the handle was just created.
        unsafe { ConnectNamedPipe(pipe, std::ptr::null_mut()) };
        let service = service.clone();
        let address = pipe as usize; // a handle is not Send; it is only used by this one thread
        thread::spawn(move || serve(address as HANDLE, &service));
    }
}

define_windows_service!(entry, service_main);

fn service_main(_: Vec<OsString>) {
    let (sender, receiver) = std::sync::mpsc::channel();
    let handler = move |control| match control {
        ServiceControl::Stop | ServiceControl::Shutdown => {
            let _ = sender.send(());
            ServiceControlHandlerResult::NoError
        }
        ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
        _ => ServiceControlHandlerResult::NotImplemented,
    };
    let Ok(status) = service_control_handler::register(NAME, handler) else { return };
    let report = |state, accepted| {
        let _ = status.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS, current_state: state, controls_accepted: accepted,
            exit_code: ServiceExitCode::Win32(0), checkpoint: 0, wait_hint: Duration::default(), process_id: None,
        });
    };
    report(ServiceState::Running, ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN);
    let _ = lock_folder();
    let stored: Stored = fs::read(folder().join("state.json")).ok()
        .and_then(|text| serde_json::from_slice(&text).ok()).unwrap_or_default();
    let user_sid = fs::read_to_string(folder().join("user")).unwrap_or_default().trim().to_string();
    let mut service = Service { stored, attempt: 0, tried_at: 0, way: None, error: None };
    if service.stored.auto_connect {
        service.stored.wanted = true;
    }
    let service = Arc::new(Mutex::new(service));
    let (watched, listened) = (service.clone(), service);
    thread::spawn(move || loop {
        watched.lock().unwrap().tick();
        sleep(Duration::from_secs(5));
    });
    thread::spawn(move || listen(listened, user_sid));
    let _ = receiver.recv();
    report(ServiceState::Stopped, ServiceControlAccept::empty());
}

/// As an administrator, once. `user_sid` is who may control it afterwards.
fn install(user_sid: &str) -> Result<(), String> {
    if !user_sid.starts_with("S-1-") || !user_sid.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
        return Err("usage: pi-guard-vpn install USER-SID".into());
    }
    lock_folder()?;
    fs::write(folder().join("user"), user_sid).map_err(|e| e.to_string())?;
    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CREATE_SERVICE).map_err(|e| e.to_string())?;
    let info = ServiceInfo {
        name: OsString::from(NAME), display_name: OsString::from("PiGuard VPN"),
        service_type: ServiceType::OWN_PROCESS, start_type: ServiceStartType::AutoStart,
        error_control: ServiceErrorControl::Normal,
        executable_path: std::env::current_exe().map_err(|e| e.to_string())?,
        launch_arguments: vec![OsString::from("service")], dependencies: vec![],
        account_name: None, account_password: None,
    };
    let service = manager.create_service(&info, ServiceAccess::START).map_err(|e| e.to_string())?;
    service.start::<&str>(&[]).map_err(|e| e.to_string())
}

fn uninstall() -> Result<(), String> {
    let _ = hidden(&wireguard("wireguard.exe"), &["/uninstalltunnelservice", TUNNEL]);
    let manager = ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT).map_err(|e| e.to_string())?;
    let service = manager.open_service(NAME, ServiceAccess::STOP | ServiceAccess::DELETE).map_err(|e| e.to_string())?;
    let _ = service.stop();
    service.delete().map_err(|e| e.to_string())?;
    let _ = fs::remove_dir_all(folder());
    Ok(())
}

pub fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let result = match args.first().map(String::as_str) {
        Some("service") => service_dispatcher::start(NAME, entry).map_err(|e| e.to_string()),
        Some("install") => install(args.get(1).map_or("", String::as_str)),
        Some("uninstall") => uninstall(),
        Some("--version") => {
            println!("{}", env!("CARGO_PKG_VERSION"));
            Ok(())
        }
        _ => Err("usage: pi-guard-vpn install USER-SID | uninstall".into()),
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_access_is_written_for_the_wireguard_service() {
        let key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
        let tunnel = Tunnel {
            private_key: key.into(), address: "10.8.0.3/32".into(), dns: vec!["10.8.0.1".into()], mtu: 1420,
            peer_key: key.into(), preshared_key: key.into(), endpoint: "x.example:1".into(),
            allowed_ips: vec!["0.0.0.0/0".into(), "::/0".into()],
        };
        let text = render(&tunnel, "1.2.3.4:51820", 1340);
        assert!(text.contains("Endpoint = 1.2.3.4:51820") && text.contains("MTU = 1340"));
        // What makes WireGuard for Windows block everything past the tunnel.
        assert!(text.contains("AllowedIPs = 0.0.0.0/0, ::/0"));
        assert_eq!(Tunnel::parse(&text).unwrap().endpoint, "1.2.3.4:51820");
    }
}

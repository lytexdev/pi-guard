//! pi-guard agent: connects this computer to pi-guard.
//!
//! The connection goes out from here to the Pi, so nothing has to be opened on this computer.
//! It only understands the fixed commands below. There is deliberately no way to run an
//! arbitrary command: even someone who took over the Pi gets no shell on this machine.
//! Power and Docker commands can each be switched off in the configuration file.

mod commands;
mod config;
mod maintenance;
mod update;

use std::net::TcpStream;
use std::sync::Arc;
use std::thread::sleep;
use std::time::{Duration, Instant};

use rustls::pki_types::pem::PemObject;
use rustls::pki_types::CertificateDer;
use serde_json::{json, Value};
use tungstenite::client::IntoClientRequest;
use tungstenite::http::HeaderValue;
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Connector, Message, WebSocket};

use config::Config;

const REPORT_EVERY: Duration = Duration::from_secs(30);
const MAX_BACKOFF: u64 = 60;
type Socket = WebSocket<MaybeTlsStream<TcpStream>>;

pub fn tls_config(config: &Config) -> Result<Option<Arc<rustls::ClientConfig>>, String> {
    let Some(path) = &config.ca_file else { return Ok(None) };
    let pem = std::fs::read(path).map_err(|e| format!("cannot read {path}: {e}"))?;
    let mut roots = rustls::RootCertStore::empty();
    for cert in CertificateDer::pem_slice_iter(&pem) {
        roots.add(cert.map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    }
    let tls = rustls::ClientConfig::builder().with_root_certificates(roots).with_no_client_auth();
    Ok(Some(Arc::new(tls)))
}

fn tls_connector(config: &Config) -> Result<Option<Connector>, String> {
    Ok(tls_config(config)?.map(Connector::Rustls))
}

pub fn host_and_port(url: &str) -> Result<(String, u16), String> {
    let (scheme, rest) = url.split_once("://").ok_or("URL without scheme")?;
    let authority = rest.split('/').next().unwrap_or(rest);
    let default = if scheme == "wss" { 443 } else { 80 };
    match authority.rsplit_once(':') {
        Some((host, port)) => Ok((host.to_string(), port.parse().map_err(|_| "bad port")?)),
        None => Ok((authority.to_string(), default)),
    }
}

fn connect(config: &Config) -> Result<Socket, String> {
    let url = format!("{}/api/agent/ws", config.websocket_url()?);
    let mut request = url.as_str().into_client_request().map_err(|e| e.to_string())?;
    let bearer = format!("Bearer {}", config.token);
    let value = HeaderValue::from_str(&bearer).map_err(|_| "token has invalid characters")?;
    request.headers_mut().insert("Authorization", value);
    let (host, port) = host_and_port(&url)?;
    let stream = TcpStream::connect((host.as_str(), port)).map_err(|e| e.to_string())?;
    let (socket, _) =
        tungstenite::client_tls_with_config(request, stream, None, tls_connector(config)?)
            .map_err(|e| e.to_string())?;
    let timeout = Some(Duration::from_secs(1));
    match socket.get_ref() {
        MaybeTlsStream::Plain(s) => s.set_read_timeout(timeout),
        MaybeTlsStream::Rustls(s) => s.get_ref().set_read_timeout(timeout),
        _ => Ok(()),
    }
    .map_err(|e| e.to_string())?;
    Ok(socket)
}

fn send(socket: &mut Socket, value: &Value) -> Result<(), String> {
    socket.send(Message::text(value.to_string())).map_err(|e| e.to_string())
}

fn session(config: &Config) -> Result<(), String> {
    let mut socket = connect(config)?;
    eprintln!("connected to {}", config.url);
    send(&mut socket, &json!({"type": "status", "data": commands::status(config)}))?;
    let mut last_report = Instant::now();
    loop {
        if last_report.elapsed() >= REPORT_EVERY {
            send(&mut socket, &json!({"type": "status", "data": commands::status(config)}))?;
            last_report = Instant::now();
        }
        match socket.read() {
            Ok(Message::Text(text)) => {
                let Ok(request) = serde_json::from_str::<Value>(text.as_str()) else { continue };
                let id = request["id"].clone();
                let outcome = commands::run(config, &request);
                let (ok, data) = match &outcome.result {
                    Ok(data) => (true, data.clone()),
                    Err(error) => (false, json!(error)),
                };
                send(&mut socket, &json!({"type": "reply", "id": id, "ok": ok, "data": data}))?;
                socket.flush().map_err(|e| e.to_string())?;
                // Power commands run after the answer has left.
                if let Some(action) = outcome.after {
                    sleep(Duration::from_secs(1));
                    action();
                }
            }
            Ok(Message::Close(_)) => return Err("closed by the Pi".into()),
            Ok(_) => {}
            Err(tungstenite::Error::Io(e))
                if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {}
            Err(e) => return Err(e.to_string()),
        }
    }
}

fn main() {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let path = std::env::args().nth(1).unwrap_or_else(|| "/etc/pi-guard-agent/agent.conf".into());
    if path == "--version" {
        println!("{}", env!("CARGO_PKG_VERSION")); // a new version must answer this before it is used
        return;
    }
    let config = match Config::load(&path) {
        Ok(config) => config,
        Err(error) => {
            eprintln!("configuration: {error}");
            std::process::exit(2);
        }
    };
    if config.auto_update {
        update::start(config.clone());
    }
    let mut backoff = 2;
    loop {
        let started = Instant::now();
        if let Err(error) = session(&config) {
            eprintln!("connection: {error}");
        }
        if started.elapsed() > Duration::from_secs(60) {
            backoff = 2;
        }
        sleep(Duration::from_secs(backoff));
        backoff = (backoff * 2).min(MAX_BACKOFF);
    }
}

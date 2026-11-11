//! The side behind NAT. It connects out to the server, keeps a control connection, and opens
//! one data connection for each visitor the server announces.

use crate::config::ClientConfig;
use crate::proto::{self, Control, Hello, Welcome, VERSION};
use crate::status;
use serde_json::json;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::BufReader;
use tokio::net::TcpStream;
use tokio_rustls::client::TlsStream;
use tokio_rustls::TlsConnector;

const CONNECT: Duration = Duration::from_secs(10);
const SILENT_LIMIT: Duration = Duration::from_secs(60);
const MAX_BACKOFF: u64 = 60;

#[derive(Default)]
struct Shared {
    connected: Mutex<Option<u64>>,
    accepted: Mutex<Vec<u16>>,
    refused: Mutex<Option<String>>,
    connections: AtomicU64,
    bytes_in: AtomicU64,
    bytes_out: AtomicU64,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

async fn connect(
    config: &ClientConfig,
    connector: &TlsConnector,
) -> std::io::Result<TlsStream<TcpStream>> {
    let tcp = tokio::time::timeout(CONNECT, TcpStream::connect(&config.server)).await??;
    let _ = tcp.set_nodelay(true);
    // The name is not checked: the pinned fingerprint is what is trusted.
    let name = rustls::pki_types::ServerName::try_from("pgproxy").expect("valid name");
    tokio::time::timeout(CONNECT, connector.connect(name, tcp)).await?
}

pub async fn run(config: ClientConfig) -> Result<(), String> {
    let connector = TlsConnector::from(crate::tls::client_config(&config.fingerprint)?);
    let config = Arc::new(config);
    let shared = Arc::new(Shared::default());
    if let Some(path) = config.status.clone() {
        let (config, shared) = (config.clone(), shared.clone());
        tokio::spawn(async move {
            loop {
                let value = json!({
                    "role": "client", "server": config.server, "name": config.name, "time": now(),
                    "connected_since": *shared.connected.lock().unwrap(),
                    "ports": *shared.accepted.lock().unwrap(),
                    "wanted": config.forwards.iter().map(|f| f.remote_port).collect::<Vec<_>>(),
                    "refused": shared.refused.lock().unwrap().clone(),
                    "connections": shared.connections.load(Ordering::Relaxed),
                    "bytes_in": shared.bytes_in.load(Ordering::Relaxed),
                    "bytes_out": shared.bytes_out.load(Ordering::Relaxed),
                });
                status::write(&path, &value);
                tokio::time::sleep(Duration::from_secs(5)).await;
            }
        });
    }
    let targets: Arc<HashMap<u16, String>> = Arc::new(
        config
            .forwards
            .iter()
            .map(|f| (f.remote_port, f.local.clone()))
            .collect(),
    );
    let mut backoff = 1;
    loop {
        match session(&config, &connector, &shared, &targets).await {
            Ok(true) => backoff = 1,
            Ok(false) => backoff = MAX_BACKOFF, // refused: asking again at once will not help
            Err(e) => eprintln!("connection to {} failed: {e}", config.server),
        }
        *shared.connected.lock().unwrap() = None;
        shared.accepted.lock().unwrap().clear();
        tokio::time::sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(MAX_BACKOFF);
    }
}

/// Ok(true) after a session that worked for a while, Ok(false) if the server said no.
async fn session(
    config: &Arc<ClientConfig>,
    connector: &TlsConnector,
    shared: &Arc<Shared>,
    targets: &Arc<HashMap<u16, String>>,
) -> std::io::Result<bool> {
    let tls = connect(config, connector).await?;
    let mut tls = BufReader::new(tls);
    let hello = Hello::Control {
        v: VERSION,
        name: config.name.clone(),
        token: config.token.clone(),
        ports: config.forwards.iter().map(|f| f.remote_port).collect(),
    };
    proto::write_json(&mut tls, &hello).await?;
    let line = tokio::time::timeout(CONNECT, proto::read_line(&mut tls))
        .await??
        .unwrap_or_default();
    let welcome: Welcome = serde_json::from_str(&line).map_err(std::io::Error::other)?;
    *shared.refused.lock().unwrap() = welcome.error.clone();
    if !welcome.ok {
        eprintln!("server refused: {}", welcome.error.unwrap_or_default());
        return Ok(false);
    }
    if let Some(error) = &welcome.error {
        eprintln!("some ports were refused: {error}");
    }
    eprintln!(
        "connected to {} with ports {:?}",
        config.server, welcome.ports
    );
    *shared.connected.lock().unwrap() = Some(now());
    *shared.accepted.lock().unwrap() = welcome.ports.clone();
    loop {
        let line = match tokio::time::timeout(SILENT_LIMIT, proto::read_line(&mut tls)).await {
            Ok(line) => line?,
            Err(_) => return Ok(true), // silent server: connect again
        };
        let Some(line) = line else { return Ok(true) };
        match serde_json::from_str::<Control>(&line) {
            Ok(Control::Ping { n }) => proto::write_json(&mut tls, &Control::Pong { n }).await?,
            Ok(Control::Open { id, port }) => {
                // Only ports this side asked for: the server cannot choose a target here.
                let Some(target) = targets.get(&port).cloned() else {
                    continue;
                };
                let (config, connector, shared) =
                    (config.clone(), connector.clone(), shared.clone());
                tokio::spawn(async move {
                    if let Err(e) = visitor(&config, &connector, &shared, id, &target).await {
                        eprintln!("forward to {target} failed: {e}");
                    }
                });
            }
            _ => {}
        }
    }
}

async fn visitor(
    config: &ClientConfig,
    connector: &TlsConnector,
    shared: &Shared,
    id: u64,
    target: &str,
) -> std::io::Result<()> {
    let mut local = tokio::time::timeout(CONNECT, TcpStream::connect(target)).await??;
    let _ = local.set_nodelay(true);
    let mut tls = connect(config, connector).await?;
    let hello = Hello::Data {
        v: VERSION,
        name: config.name.clone(),
        token: config.token.clone(),
        id,
    };
    proto::write_json(&mut tls, &hello).await?;
    shared.connections.fetch_add(1, Ordering::Relaxed);
    let (from_visitor, to_visitor) = tokio::io::copy_bidirectional(&mut tls, &mut local).await?;
    shared.bytes_in.fetch_add(from_visitor, Ordering::Relaxed);
    shared.bytes_out.fetch_add(to_visitor, Ordering::Relaxed);
    Ok(())
}

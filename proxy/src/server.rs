//! The side with the public address. Clients connect out to it; for every visitor on one of
//! their ports, the client is asked to open a data connection, and the two are joined.

use crate::config::ServerConfig;
use crate::proto::{self, Control, Hello, Welcome, VERSION};
use crate::status;
use serde_json::json;
use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use tokio::task::JoinHandle;
use tokio_rustls::TlsAcceptor;

const HANDSHAKE: Duration = Duration::from_secs(10);
const PENDING: Duration = Duration::from_secs(10);
const PING_EVERY: Duration = Duration::from_secs(20);
const SILENT_LIMIT: Duration = Duration::from_secs(60);
const MAX_PENDING: usize = 64;
const FAILURE_WINDOW: Duration = Duration::from_secs(60);
const MAX_FAILURES: u32 = 10;

struct Session {
    epoch: u64,
    /// Held so the control loop stays open while the session lasts, also without ports.
    _tx: mpsc::Sender<Control>,
    ports: Vec<u16>,
    address: SocketAddr,
    since: u64,
    listeners: Vec<JoinHandle<()>>,
}

struct Pending {
    client: String,
    stream: TcpStream,
    created: Instant,
}

#[derive(Default)]
struct Counters {
    connections: AtomicU64,
    bytes_in: AtomicU64,
    bytes_out: AtomicU64,
}

struct State {
    config: ServerConfig,
    sessions: Mutex<HashMap<String, Session>>,
    pending: Mutex<HashMap<u64, Pending>>,
    failures: Mutex<HashMap<IpAddr, (Instant, u32)>>,
    counters: Mutex<HashMap<String, Arc<Counters>>>,
}

fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

impl State {
    fn blocked(&self, ip: IpAddr) -> bool {
        let mut failures = self.failures.lock().unwrap();
        failures.retain(|_, (start, _)| start.elapsed() < FAILURE_WINDOW);
        failures
            .get(&ip)
            .is_some_and(|(_, count)| *count >= MAX_FAILURES)
    }

    fn failed(&self, ip: IpAddr) {
        let mut failures = self.failures.lock().unwrap();
        let entry = failures.entry(ip).or_insert((Instant::now(), 0));
        entry.1 += 1;
    }

    fn counters(&self, name: &str) -> Arc<Counters> {
        self.counters
            .lock()
            .unwrap()
            .entry(name.to_string())
            .or_default()
            .clone()
    }

    fn snapshot(&self) -> serde_json::Value {
        let sessions = self.sessions.lock().unwrap();
        let clients: Vec<_> = self
            .config
            .clients
            .iter()
            .map(|c| {
                let session = sessions.get(&c.name);
                let counters = self.counters(&c.name);
                json!({
                    "name": c.name,
                    "connected": session.is_some(),
                    "address": session.map(|s| s.address.ip().to_string()),
                    "since": session.map(|s| s.since),
                    "ports": session.map(|s| s.ports.clone()).unwrap_or_default(),
                    "allowed": c.ports.iter().map(|p| p.port).collect::<Vec<_>>(),
                    "connections": counters.connections.load(Ordering::Relaxed),
                    "bytes_in": counters.bytes_in.load(Ordering::Relaxed),
                    "bytes_out": counters.bytes_out.load(Ordering::Relaxed),
                })
            })
            .collect();
        json!({"role": "server", "listen": self.config.listen, "time": now(), "clients": clients})
    }
}

pub async fn run(config: ServerConfig) -> Result<(), String> {
    let tls = crate::tls::server_config(&config.cert, &config.key)?;
    let acceptor = TlsAcceptor::from(tls);
    let listener = TcpListener::bind(&config.listen)
        .await
        .map_err(|e| format!("{}: {e}", config.listen))?;
    eprintln!("pgproxy server listening on {}", config.listen);
    let state = Arc::new(State {
        config,
        sessions: Mutex::new(HashMap::new()),
        pending: Mutex::new(HashMap::new()),
        failures: Mutex::new(HashMap::new()),
        counters: Mutex::new(HashMap::new()),
    });
    if let Some(path) = state.config.status.clone() {
        let state = state.clone();
        tokio::spawn(async move {
            loop {
                status::write(&path, &state.snapshot());
                tokio::time::sleep(Duration::from_secs(5)).await;
            }
        });
    }
    {
        let state = state.clone();
        tokio::spawn(async move {
            loop {
                tokio::time::sleep(PENDING).await;
                state
                    .pending
                    .lock()
                    .unwrap()
                    .retain(|_, p| p.created.elapsed() < PENDING);
            }
        });
    }
    loop {
        let (stream, address) = match listener.accept().await {
            Ok(pair) => pair,
            Err(_) => {
                tokio::time::sleep(Duration::from_millis(100)).await;
                continue;
            }
        };
        if state.blocked(address.ip()) {
            continue; // dropping the connection is the answer
        }
        let (state, acceptor) = (state.clone(), acceptor.clone());
        tokio::spawn(async move {
            let _ = serve(state, acceptor, stream, address).await;
        });
    }
}

async fn serve(
    state: Arc<State>,
    acceptor: TlsAcceptor,
    stream: TcpStream,
    address: SocketAddr,
) -> std::io::Result<()> {
    let _ = stream.set_nodelay(true);
    let tls = tokio::time::timeout(HANDSHAKE, acceptor.accept(stream)).await??;
    let mut tls = BufReader::new(tls);
    let line = tokio::time::timeout(HANDSHAKE, proto::read_line(&mut tls))
        .await??
        .unwrap_or_default();
    let hello: Hello = match serde_json::from_str(&line) {
        Ok(hello) => hello,
        Err(_) => {
            state.failed(address.ip());
            return Ok(());
        }
    };
    let (name, token, version) = match &hello {
        Hello::Control { name, token, v, .. } | Hello::Data { name, token, v, .. } => {
            (name.clone(), token.clone(), *v)
        }
    };
    let known = state.config.client(&name).map(|c| c.token_sha256.clone());
    if version != VERSION
        || !known
            .as_deref()
            .is_some_and(|hash| proto::token_matches(&token, hash))
    {
        state.failed(address.ip());
        tokio::time::sleep(Duration::from_secs(1)).await;
        let refusal = Welcome {
            ok: false,
            ports: vec![],
            error: Some("unauthorized".into()),
        };
        let _ = proto::write_json(&mut tls, &refusal).await;
        return Ok(());
    }
    match hello {
        Hello::Control { ports, .. } => control(state, tls, name, ports, address).await,
        Hello::Data { id, .. } => data(state, tls, name, id).await,
    }
}

async fn control<S>(
    state: Arc<State>,
    mut tls: BufReader<S>,
    name: String,
    wanted: Vec<u16>,
    address: SocketAddr,
) -> std::io::Result<()>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let entry = state.config.client(&name).cloned().expect("checked before");
    // A new connection of the same client replaces the old one, which may be a dead network.
    if let Some(old) = state.sessions.lock().unwrap().remove(&name) {
        old.listeners.iter().for_each(JoinHandle::abort);
    }
    let (tx, mut rx) = mpsc::channel::<Control>(64);
    let epoch = proto::random_u64();
    let mut ports = Vec::new();
    let mut listeners = Vec::new();
    let mut refused = Vec::new();
    for port in wanted.iter().copied().take(crate::config::MAX_FORWARDS) {
        let Some(allowed) = entry.ports.iter().find(|p| p.port == port) else {
            refused.push(format!("{port}: not allowed for {name}"));
            continue;
        };
        match TcpListener::bind((allowed.bind.as_str(), port)).await {
            Ok(listener) => {
                ports.push(port);
                listeners.push(tokio::spawn(visitors(
                    state.clone(),
                    listener,
                    name.clone(),
                    port,
                    tx.clone(),
                )));
            }
            Err(e) => refused.push(format!("{port}: {e}")),
        }
    }
    let error = (!refused.is_empty()).then(|| refused.join("; "));
    proto::write_json(
        &mut tls,
        &Welcome {
            ok: true,
            ports: ports.clone(),
            error,
        },
    )
    .await?;
    eprintln!(
        "{name} connected from {} with ports {ports:?}",
        address.ip()
    );
    state.sessions.lock().unwrap().insert(
        name.clone(),
        Session {
            epoch,
            _tx: tx,
            ports,
            address,
            since: now(),
            listeners,
        },
    );
    let result = control_loop(&mut tls, &mut rx).await;
    let mut sessions = state.sessions.lock().unwrap();
    if sessions.get(&name).is_some_and(|s| s.epoch == epoch) {
        if let Some(session) = sessions.remove(&name) {
            session.listeners.iter().for_each(JoinHandle::abort);
        }
        eprintln!("{name} disconnected");
    }
    result
}

async fn control_loop<S>(
    tls: &mut BufReader<S>,
    rx: &mut mpsc::Receiver<Control>,
) -> std::io::Result<()>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let mut ping = tokio::time::interval(PING_EVERY);
    let mut heard = Instant::now();
    let mut n = 0u64;
    loop {
        tokio::select! {
            message = rx.recv() => match message {
                Some(message) => proto::write_json(tls, &message).await?,
                None => return Ok(()),
            },
            line = proto::read_line(tls) => match line? {
                Some(_) => heard = Instant::now(), // pongs: alive is all they say
                None => return Ok(()),
            },
            _ = ping.tick() => {
                if heard.elapsed() > SILENT_LIMIT {
                    return Ok(());
                }
                n += 1;
                proto::write_json(tls, &Control::Ping { n }).await?;
            }
        }
    }
}

async fn visitors(
    state: Arc<State>,
    listener: TcpListener,
    name: String,
    port: u16,
    tx: mpsc::Sender<Control>,
) {
    loop {
        let Ok((stream, _)) = listener.accept().await else {
            tokio::time::sleep(Duration::from_millis(100)).await; // out of file handles: wait
            continue;
        };
        let _ = stream.set_nodelay(true);
        let id = proto::random_u64();
        {
            let mut pending = state.pending.lock().unwrap();
            if pending.values().filter(|p| p.client == name).count() >= MAX_PENDING {
                continue; // the client does not keep up; this visitor is turned away
            }
            pending.insert(
                id,
                Pending {
                    client: name.clone(),
                    stream,
                    created: Instant::now(),
                },
            );
        }
        if tx.send(Control::Open { id, port }).await.is_err() {
            state.pending.lock().unwrap().remove(&id);
            return;
        }
    }
}

async fn data<S>(
    state: Arc<State>,
    mut tls: BufReader<S>,
    name: String,
    id: u64,
) -> std::io::Result<()>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let found = {
        let mut pending = state.pending.lock().unwrap();
        match pending.get(&id) {
            // Another client must not take over this visitor, even with a valid token of its own.
            Some(p) if p.client == name => pending.remove(&id),
            _ => None,
        }
    };
    let Some(Pending { mut stream, .. }) = found else {
        return tls.shutdown().await;
    };
    let counters = state.counters(&name);
    counters.connections.fetch_add(1, Ordering::Relaxed);
    let result = tokio::io::copy_bidirectional(&mut stream, &mut tls).await;
    if let Ok((to_client, to_visitor)) = result {
        counters.bytes_in.fetch_add(to_client, Ordering::Relaxed);
        counters.bytes_out.fetch_add(to_visitor, Ordering::Relaxed);
    }
    Ok(())
}

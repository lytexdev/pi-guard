//! Both sides on this machine: a visitor reaches a local service through server and client,
//! and what the server does not allow stays closed.

use crate::config::{AllowedPort, ClientConfig, ClientEntry, Forward, ServerConfig};
use crate::proto::{self, Hello, Welcome};
use std::path::Path;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

async fn echo() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let (mut stream, _) = listener.accept().await.unwrap();
            tokio::spawn(async move {
                let mut buffer = [0u8; 1024];
                while let Ok(n) = stream.read(&mut buffer).await {
                    if n == 0 || stream.write_all(&buffer[..n]).await.is_err() {
                        break;
                    }
                }
            });
        }
    });
    port
}

struct Setup {
    control: u16,
    public: u16,
    fingerprint: String,
    token: String,
}

async fn start(dir: &Path) -> Setup {
    let fingerprint = crate::tls::generate(
        &dir.join("cert.pem"),
        &dir.join("key.pem"),
        vec!["pgproxy".into()],
    )
    .unwrap();
    let (control, public) = (free_port(), free_port());
    let token = proto::new_token();
    let config = ServerConfig {
        listen: format!("127.0.0.1:{control}"),
        cert: dir.join("cert.pem"),
        key: dir.join("key.pem"),
        status: Some(dir.join("status.json")),
        clients: vec![ClientEntry {
            name: "pi".into(),
            token_sha256: proto::token_hash(&token),
            ports: vec![AllowedPort {
                port: public,
                bind: "127.0.0.1".into(),
            }],
        }],
    };
    config.check().unwrap();
    tokio::spawn(crate::server::run(config));
    tokio::time::sleep(Duration::from_millis(200)).await;
    Setup {
        control,
        public,
        fingerprint,
        token,
    }
}

fn temp(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("pgproxy-test-{name}-{}", proto::random_u64()));
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[tokio::test(flavor = "multi_thread")]
async fn a_visitor_reaches_the_service_behind_the_client() {
    let dir = temp("e2e");
    let setup = start(&dir).await;
    let service = echo().await;
    let client = ClientConfig {
        server: format!("127.0.0.1:{}", setup.control),
        fingerprint: setup.fingerprint.clone(),
        name: "pi".into(),
        token: setup.token.clone(),
        forwards: vec![Forward {
            remote_port: setup.public,
            local: format!("127.0.0.1:{service}"),
        }],
        status: Some(dir.join("client.json")),
    };
    client.check().unwrap();
    tokio::spawn(crate::client::run(client));
    let mut visitor = None;
    for _ in 0..50 {
        tokio::time::sleep(Duration::from_millis(100)).await;
        if let Ok(stream) = TcpStream::connect(("127.0.0.1", setup.public)).await {
            visitor = Some(stream);
            break;
        }
    }
    let mut visitor = visitor.expect("public port opens once the client is connected");
    visitor.write_all(b"hello through the proxy").await.unwrap();
    let mut answer = vec![0u8; 23];
    tokio::time::timeout(Duration::from_secs(5), visitor.read_exact(&mut answer))
        .await
        .unwrap()
        .unwrap();
    assert_eq!(&answer, b"hello through the proxy");
    tokio::time::sleep(Duration::from_secs(6)).await;
    let status = std::fs::read_to_string(dir.join("status.json")).unwrap();
    assert!(status.contains("\"connected\": true"), "{status}");
    assert!(
        !status.contains(&setup.token),
        "the status never holds a token"
    );
    std::fs::remove_dir_all(dir).ok();
}

async fn hello(
    setup: &Setup,
    message: &Hello,
    fingerprint: &str,
) -> std::io::Result<Option<Welcome>> {
    let connector =
        tokio_rustls::TlsConnector::from(crate::tls::client_config(fingerprint).unwrap());
    let tcp = TcpStream::connect(("127.0.0.1", setup.control)).await?;
    let tls = connector
        .connect(
            rustls::pki_types::ServerName::try_from("pgproxy").unwrap(),
            tcp,
        )
        .await?;
    let mut tls = BufReader::new(tls);
    proto::write_json(&mut tls, message).await?;
    let line = tokio::time::timeout(Duration::from_secs(5), proto::read_line(&mut tls)).await??;
    Ok(line.and_then(|l| serde_json::from_str(&l).ok()))
}

#[tokio::test(flavor = "multi_thread")]
async fn wrong_tokens_other_ports_and_other_certificates_are_refused() {
    let dir = temp("refuse");
    let setup = start(&dir).await;
    let wrong = Hello::Control {
        v: 1,
        name: "pi".into(),
        token: "x".repeat(64),
        ports: vec![setup.public],
    };
    let answer = hello(&setup, &wrong, &setup.fingerprint)
        .await
        .unwrap()
        .unwrap();
    assert!(!answer.ok);
    let unknown = Hello::Control {
        v: 1,
        name: "nobody".into(),
        token: setup.token.clone(),
        ports: vec![],
    };
    assert!(
        !hello(&setup, &unknown, &setup.fingerprint)
            .await
            .unwrap()
            .unwrap()
            .ok
    );
    let other_port = free_port();
    let greedy = Hello::Control {
        v: 1,
        name: "pi".into(),
        token: setup.token.clone(),
        ports: vec![other_port],
    };
    let answer = hello(&setup, &greedy, &setup.fingerprint)
        .await
        .unwrap()
        .unwrap();
    assert!(answer.ok && answer.ports.is_empty() && answer.error.unwrap().contains("not allowed"));
    assert!(TcpStream::connect(("127.0.0.1", other_port)).await.is_err());
    // A server with another certificate is not trusted, whatever it says.
    let stranger = crate::tls::fingerprint(b"another certificate");
    assert!(hello(&setup, &wrong, &stranger).await.is_err());
    std::fs::remove_dir_all(dir).ok();
}

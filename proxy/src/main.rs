//! pgproxy: reach a service behind NAT through a server you choose, like frp, but small.
//!
//!   pgproxy init DIR [NAME...]   make the server's key and certificate, print the fingerprint
//!   pgproxy token                print a new token and the hash that goes into the server config
//!   pgproxy server CONFIG.json   run the server
//!   pgproxy client CONFIG.json   run a client
//!   pgproxy check server|client CONFIG.json

mod client;
mod config;
mod proto;
mod server;
mod status;
#[cfg(test)]
mod tests;
mod tls;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

fn usage() -> ExitCode {
    eprintln!("usage: pgproxy init DIR [NAME...] | token | server CONFIG | client CONFIG | check server|client CONFIG");
    ExitCode::from(2)
}

fn fail(message: String) -> ExitCode {
    eprintln!("pgproxy: {message}");
    ExitCode::FAILURE
}

#[tokio::main]
async fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    match args.as_slice() {
        ["init", dir, names @ ..] => {
            let dir = PathBuf::from(dir);
            if let Err(e) = std::fs::create_dir_all(&dir) {
                return fail(format!("{}: {e}", dir.display()));
            }
            let (cert, key) = (dir.join("cert.pem"), dir.join("key.pem"));
            if key.exists() {
                return fail(format!(
                    "{} exists; remove it to make a new key",
                    key.display()
                ));
            }
            let mut names: Vec<String> = names.iter().map(|n| n.to_string()).collect();
            if names.is_empty() {
                names.push("pgproxy".into());
            }
            match tls::generate(&cert, &key, names) {
                Ok(fingerprint) => {
                    println!("{fingerprint}");
                    ExitCode::SUCCESS
                }
                Err(e) => fail(e),
            }
        }
        ["fingerprint", cert] => match tls::read_certificate(Path::new(cert)) {
            Ok(der) => {
                println!("{}", tls::fingerprint(der.as_ref()));
                ExitCode::SUCCESS
            }
            Err(e) => fail(e),
        },
        ["token"] => {
            let token = proto::new_token();
            println!("token:        {token}");
            println!("token_sha256: {}", proto::token_hash(&token));
            ExitCode::SUCCESS
        }
        ["check", "server", path] => match config::ServerConfig::load(Path::new(path)) {
            Ok(_) => ExitCode::SUCCESS,
            Err(e) => fail(e),
        },
        ["check", "client", path] => match config::ClientConfig::load(Path::new(path)) {
            Ok(_) => ExitCode::SUCCESS,
            Err(e) => fail(e),
        },
        ["server", path] => match config::ServerConfig::load(Path::new(path)) {
            Ok(config) => match server::run(config).await {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => fail(e),
            },
            Err(e) => fail(e),
        },
        ["client", path] => match config::ClientConfig::load(Path::new(path)) {
            Ok(config) => match client::run(config).await {
                Ok(()) => ExitCode::SUCCESS,
                Err(e) => fail(e),
            },
            Err(e) => fail(e),
        },
        _ => usage(),
    }
}

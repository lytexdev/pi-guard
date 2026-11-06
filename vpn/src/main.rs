//! pi-guard-vpn: the VPN service of pi-guard.
//!
//!   pi-guard-vpn daemon              the service on a computer, as root
//!   pi-guard-vpn pq-server           Rosenpass on the Pi, as root
//!   pi-guard-vpn ctl '{"cmd":...}'   talk to the service
//!   pi-guard-vpn run -- PROGRAM ...  start a program past the VPN
//!   pi-guard-vpn unlock              emergency: remove tunnel and firewall rules, as root

mod b64;
mod config;
mod exchange;
#[cfg(windows)]
mod windows;

#[cfg(unix)]
mod daemon;
#[cfg(unix)]
mod firewall;
#[cfg(unix)]
mod pq;
#[cfg(unix)]
mod server;
#[cfg(unix)]
mod sys;
#[cfg(unix)]
mod tunnel;

#[cfg(unix)]
use std::io::{BufRead, BufReader, Write};
#[cfg(unix)]
use std::os::unix::net::UnixStream;
#[cfg(unix)]
use std::os::unix::process::CommandExt;
#[cfg(unix)]
use std::process::{exit, Command};

#[cfg(unix)]
const ROSENPASS: &str = "/usr/local/lib/pi-guard-vpn/rosenpass";

#[cfg(unix)]
fn ask(request: &str) -> Result<String, String> {
    let mut stream = UnixStream::connect(daemon::SOCKET).map_err(|e| format!("the service: {e}"))?;
    writeln!(stream, "{request}").map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(stream).read_line(&mut line).map_err(|e| e.to_string())?;
    Ok(line)
}

#[cfg(windows)]
fn main() {
    windows::main();
}

#[cfg(unix)]
fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let binary = std::env::var("PI_GUARD_ROSENPASS").unwrap_or_else(|_| ROSENPASS.into());
    match args.first().map(String::as_str) {
        Some("daemon") => daemon::run(binary),
        Some("pq-server") => server::run(&binary),
        Some("ctl") => match ask(args.get(1).map_or("{\"cmd\":\"status\"}", String::as_str)) {
            Ok(answer) => print!("{answer}"),
            Err(error) => {
                eprintln!("{error}");
                exit(1);
            }
        },
        Some("run") => {
            let program = args.iter().skip(1).skip_while(|a| *a == "--").collect::<Vec<_>>();
            let Some((first, rest)) = program.split_first() else {
                eprintln!("usage: pi-guard-vpn run -- PROGRAM");
                exit(2);
            };
            match ask("{\"cmd\":\"split\"}") {
                Ok(answer) if answer.contains("\"ok\"") => {}
                other => {
                    // Better not to start it than to send it through a way nobody asked for.
                    eprintln!("not started: {other:?}");
                    exit(1);
                }
            }
            let error = Command::new(first).args(rest).exec();
            eprintln!("{first}: {error}");
            exit(1);
        }
        Some("unlock") => {
            // For an emergency, at the console: everything open again, whatever the service wants.
            tunnel::down();
            if let Err(error) = firewall::clear() {
                eprintln!("{error}");
                exit(1);
            }
            println!("open again; a running service closes it if lockdown is on");
        }
        Some("--version") => println!("{}", env!("CARGO_PKG_VERSION")),
        _ => {
            eprintln!("usage: pi-guard-vpn daemon | pq-server | ctl JSON | run -- PROGRAM");
            exit(2);
        }
    }
}

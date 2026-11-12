//! Running system tools. Arguments go straight to the program, never through a shell.

use std::io::Write;
use std::process::{Command, Stdio};

const PATH: &str = "/usr/sbin:/usr/bin:/sbin:/bin";

fn command(program: &str, args: &[&str]) -> Command {
    let mut command = Command::new(program);
    command.args(args).env_clear().env("PATH", PATH).env("LC_ALL", "C");
    command
}

pub fn run(program: &str, args: &[&str]) -> Result<(), String> {
    let out = command(program, args).stdin(Stdio::null()).output().map_err(|e| format!("{program}: {e}"))?;
    if out.status.success() {
        return Ok(());
    }
    Err(format!("{program}: {}", String::from_utf8_lossy(&out.stderr).trim()))
}

pub fn quiet(program: &str, args: &[&str]) {
    let _ = run(program, args);
}

pub fn read(program: &str, args: &[&str]) -> Result<String, String> {
    let out = command(program, args).stdin(Stdio::null()).output().map_err(|e| format!("{program}: {e}"))?;
    if out.status.success() {
        return Ok(String::from_utf8_lossy(&out.stdout).into_owned());
    }
    Err(format!("{program}: {}", String::from_utf8_lossy(&out.stderr).trim()))
}

pub fn run_input(program: &str, args: &[&str], input: &[u8]) -> Result<(), String> {
    let mut child = command(program, args).stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::piped())
        .spawn().map_err(|e| format!("{program}: {e}"))?;
    child.stdin.take().ok_or("no stdin")?.write_all(input).map_err(|e| e.to_string())?;
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    if out.status.success() {
        return Ok(());
    }
    Err(format!("{program}: {}", String::from_utf8_lossy(&out.stderr).trim()))
}

/// A secret for `wg`, through a pipe instead of the argument list, where every user could read it.
pub fn set_preshared_key(interface: &str, peer: &str, key: &str) -> Result<(), String> {
    set_preshared_key_with("wg", interface, peer, key)
}

/// The same for another tool of the WireGuard family, such as AmneziaWG's `awg`.
pub fn set_preshared_key_with(tool: &str, interface: &str, peer: &str, key: &str) -> Result<(), String> {
    run_input(tool, &["set", interface, "peer", peer, "preshared-key", "/dev/stdin"], key.as_bytes())
}

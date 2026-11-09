//! The complete list of what the Pi can ask for. Container names are checked and passed as
//! single arguments, never through a shell.

use std::process::{Command, Stdio};

use serde_json::{json, Value};

use crate::config::Config;

pub struct Outcome {
    pub result: Result<Value, String>,
    pub after: Option<Box<dyn FnOnce()>>,
}

fn done(result: Result<Value, String>) -> Outcome {
    Outcome { result, after: None }
}

fn output(program: &str, args: &[&str]) -> Result<String, String> {
    let out = Command::new(program).args(args).stdin(Stdio::null()).output().map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        Err(String::from_utf8_lossy(&out.stderr).chars().take(300).collect())
    }
}

fn read(path: &str) -> String {
    std::fs::read_to_string(path).unwrap_or_default()
}

fn os_name() -> String {
    read("/etc/os-release")
        .lines()
        .find_map(|l| l.strip_prefix("PRETTY_NAME="))
        .map(|v| v.trim_matches('"').to_string())
        .unwrap_or_else(|| "Linux".into())
}

fn memory() -> (u64, u64) {
    let text = read("/proc/meminfo");
    let value = |key: &str| -> u64 {
        text.lines()
            .find_map(|l| l.strip_prefix(key))
            .and_then(|l| l.split_whitespace().next()?.parse().ok())
            .unwrap_or(0)
    };
    (value("MemTotal:") / 1024, value("MemAvailable:") / 1024)
}

fn disk() -> Value {
    let text = output("df", &["-P", "-m", "/"]).unwrap_or_default();
    let fields: Vec<&str> = text.lines().nth(1).unwrap_or("").split_whitespace().collect();
    match fields.as_slice() {
        [_, total, _, free, ..] => json!({"total_mb": total.parse::<u64>().unwrap_or(0),
                                          "free_mb": free.parse::<u64>().unwrap_or(0)}),
        _ => Value::Null,
    }
}

fn docker_available() -> bool {
    Command::new("docker").arg("version").stdout(Stdio::null()).stderr(Stdio::null()).status()
        .map(|s| s.success()).unwrap_or(false)
}

pub fn status(config: &Config) -> Value {
    let (total, available) = memory();
    let uptime = read("/proc/uptime").split_whitespace().next().and_then(|v| v.parse::<f64>().ok());
    json!({
        "hostname": read("/etc/hostname").trim(),
        "os": os_name(),
        "kernel": read("/proc/sys/kernel/osrelease").trim(),
        "uptime": uptime.unwrap_or(0.0) as u64,
        "load": read("/proc/loadavg").split_whitespace().next().unwrap_or("0"),
        "memory_total_mb": total,
        "memory_available_mb": available,
        "disk": disk(),
        "agent": env!("CARGO_PKG_VERSION"),
        "can_power": config.allow_power,
        "can_docker": config.allow_docker && docker_available(),
        "can_logs": config.allow_logs,
        "can_upgrade": config.allow_upgrade,
        "compose_dirs": if config.allow_docker { config.compose_dirs.len() } else { 0 },
        "git_repos": config.git_repos.len(),
    })
}

/// Docker names and ids: letters, digits and `_.-`, starting with a letter or digit.
pub fn valid_container(name: &str) -> bool {
    let mut chars = name.chars();
    let first_ok = chars.next().is_some_and(|c| c.is_ascii_alphanumeric());
    first_ok && name.len() <= 128
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'))
}

fn docker_list() -> Result<Value, String> {
    let text = output("docker", &["ps", "-a", "--format", "{{json .}}"])?;
    let rows: Vec<Value> = text
        .lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .map(|c| json!({"id": c["ID"], "name": c["Names"], "image": c["Image"], "state": c["State"],
                        "status": c["Status"], "ports": c["Ports"]}))
        .collect();
    Ok(json!(rows))
}

fn docker(request: &Value) -> Result<Value, String> {
    let action = request["action"].as_str().unwrap_or("");
    if action == "list" {
        return docker_list();
    }
    let container = request["container"].as_str().unwrap_or("");
    if !valid_container(container) {
        return Err("bad container name".into());
    }
    match action {
        "start" | "stop" | "restart" => output("docker", &[action, container]).map(|_| json!(true)),
        // The container only: named volumes keep their data, images stay.
        "remove" => output("docker", &["rm", "--force", container]).map(|_| json!(true)),
        "logs" => {
            let out = Command::new("docker").args(["logs", "--tail", "200", container]).output()
                .map_err(|e| e.to_string())?;
            let text = [out.stdout, out.stderr].concat();
            Ok(json!(String::from_utf8_lossy(&text)))
        }
        _ => Err("unknown docker action".into()),
    }
}

fn power(command: &str) -> Outcome {
    let args: &'static [&'static str] = match command {
        "shutdown" => &["poweroff"],
        "reboot" => &["reboot"],
        "suspend" => &["suspend"],
        _ => return done(Err("unknown command".into())),
    };
    Outcome {
        result: Ok(json!(true)),
        after: Some(Box::new(move || {
            let _ = Command::new("systemctl").args(args).status();
        })),
    }
}

pub fn run(config: &Config, request: &Value) -> Outcome {
    if let Some(result) = crate::maintenance::run(config, request) {
        return done(result);
    }
    match request["cmd"].as_str().unwrap_or("") {
        "status" => done(Ok(status(config))),
        "lock" if config.allow_power => {
            done(output("loginctl", &["lock-sessions"]).map(|_| json!(true)))
        }
        "shutdown" | "reboot" | "suspend" if config.allow_power => {
            power(request["cmd"].as_str().unwrap_or(""))
        }
        "docker" if config.allow_docker => done(docker(request)),
        "lock" | "shutdown" | "reboot" | "suspend" | "docker" => {
            done(Err("not allowed on this computer".into()))
        }
        _ => done(Err("unknown command".into())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config(power: bool, docker: bool) -> Config {
        Config { url: "https://x".into(), token: "t".into(), ca_file: None, auto_update: true,
                 allow_power: power, allow_docker: docker, allow_logs: false, allow_upgrade: false,
                 compose_dirs: vec![], git_repos: vec![] }
    }

    #[test]
    fn nothing_like_a_shell() {
        assert!(!valid_container("a; rm -rf /"));
        assert!(!valid_container("$(reboot)"));
        assert!(!valid_container("--privileged"));
        assert!(!valid_container(""));
        assert!(valid_container("frigate_1"));
    }

    #[test]
    fn unknown_commands_do_nothing() {
        for cmd in ["exec", "shell", "sh", ""] {
            let outcome = run(&config(true, true), &json!({"cmd": cmd, "args": "rm -rf /"}));
            assert!(outcome.result.is_err() && outcome.after.is_none());
        }
    }

    #[test]
    fn switches_are_respected() {
        let outcome = run(&config(false, true), &json!({"cmd": "shutdown"}));
        assert!(outcome.result.is_err() && outcome.after.is_none());
        let outcome = run(&config(true, false), &json!({"cmd": "docker", "action": "list"}));
        assert!(outcome.result.is_err());
    }

    #[test]
    fn shutdown_waits_for_the_answer() {
        let outcome = run(&config(true, true), &json!({"cmd": "shutdown"}));
        assert!(outcome.result.is_ok() && outcome.after.is_some());
    }

    #[test]
    fn reports_status() {
        let data = status(&config(true, false));
        assert!(data["hostname"].is_string() && data["memory_total_mb"].as_u64().unwrap() > 0);
        assert_eq!(data["can_docker"], false);
    }
}

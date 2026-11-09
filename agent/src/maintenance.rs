//! Looking after the computer from pi-guard: system information, a security check, logs,
//! updates of the system, of Docker Compose projects and of Git repositories.
//!
//! Reading is always allowed. Changing anything needs its switch in agent.conf, set on this
//! computer: ALLOW_LOGS, ALLOW_UPGRADE, DOCKER_COMPOSE_DIRS and GIT_REPOS. The Pi can only name
//! a folder from those lists, never a path or a command of its own, and nothing runs through a
//! shell. Git runs without hooks, so a repository cannot make root run its code.
//! Long work runs as one job at a time in the background; the Pi asks for its state.

use std::fs::File;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::config::Config;

const JOB_LOG: &str = "/var/lib/pi-guard-agent/job.log";
const LOG_LINES: &str = "200";

static JOB: Mutex<Option<Job>> = Mutex::new(None);

#[derive(Clone)]
struct Job {
    name: String,
    started: u64,
    finished: Option<u64>,
    exit: Option<i32>,
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn output(program: &str, args: &[&str]) -> Option<String> {
    let out = Command::new(program).args(args).stdin(Stdio::null()).output().ok()?;
    Some(String::from_utf8_lossy(&[out.stdout, out.stderr].concat()).into_owned())
}

fn has(program: &str) -> bool {
    Command::new("sh").args(["-c", "command -v \"$0\" >/dev/null", program]).status()
        .map(|s| s.success()).unwrap_or(false)
}

fn read(path: &str) -> String {
    std::fs::read_to_string(path).unwrap_or_default()
}

// --- reading ---------------------------------------------------------------------------------

pub fn info() -> Value {
    let cpu = read("/proc/cpuinfo");
    let model = cpu.lines().find_map(|l| l.split_once(':').filter(|(k, _)| {
        k.trim() == "model name" || k.trim() == "Model"
    }).map(|(_, v)| v.trim().to_string())).unwrap_or_default();
    let cores = cpu.lines().filter(|l| l.starts_with("processor")).count();
    let temperatures: Vec<f64> = std::fs::read_dir("/sys/class/thermal").map(|dir| {
        dir.filter_map(|e| e.ok())
            .filter_map(|e| read(&format!("{}/temp", e.path().display())).trim().parse::<f64>().ok())
            .map(|milli| (milli / 100.0).round() / 10.0)
            .collect()
    }).unwrap_or_default();
    let failed: Vec<String> = output("systemctl", &["--failed", "--no-legend", "--plain"])
        .unwrap_or_default().lines().filter_map(|l| l.split_whitespace().next().map(String::from))
        .take(20).collect();
    let addresses: Vec<String> = output("ip", &["-brief", "address"]).unwrap_or_default().lines()
        .filter(|l| !l.starts_with("lo ")).map(|l| l.split_whitespace().collect::<Vec<_>>().join(" "))
        .take(10).collect();
    json!({"cpu": model, "cores": cores, "temperatures": temperatures,
           "failed_units": failed, "addresses": addresses})
}

/// The package manager and what it would update, without changing anything.
pub fn updates() -> Value {
    if has("apt") {
        let text = output("apt", &["list", "--upgradable"]).unwrap_or_default();
        let packages: Vec<&str> = text.lines().filter(|l| l.contains("[upgradable"))
            .filter_map(|l| l.split('/').next()).collect();
        return json!({"manager": "apt", "count": packages.len(), "packages": &packages[..packages.len().min(50)]});
    }
    if has("pacman") {
        let text = if has("checkupdates") { output("checkupdates", &[]) } else { output("pacman", &["-Qu"]) }
            .unwrap_or_default();
        let packages: Vec<&str> = text.lines().filter_map(|l| l.split_whitespace().next()).collect();
        return json!({"manager": "pacman", "count": packages.len(), "packages": &packages[..packages.len().min(50)]});
    }
    if has("dnf") {
        let text = output("dnf", &["-q", "check-update"]).unwrap_or_default();
        let packages: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty() && !l.starts_with(' '))
            .filter_map(|l| l.split_whitespace().next()).collect();
        return json!({"manager": "dnf", "count": packages.len(), "packages": &packages[..packages.len().min(50)]});
    }
    json!({"manager": null, "count": 0, "packages": []})
}

fn sshd_setting(text: &str, key: &str) -> Option<String> {
    text.lines().map(str::trim).filter(|l| !l.starts_with('#'))
        .find_map(|l| {
            let mut parts = l.split_whitespace();
            (parts.next()?.eq_ignore_ascii_case(key)).then(|| parts.next().unwrap_or("").to_lowercase())
        })
}

/// Firewall, SSH, open ports, failed logins and waiting updates, with what deserves a look.
pub fn security() -> Value {
    let mut warnings = Vec::new();
    let firewall = if has("ufw") {
        let text = output("ufw", &["status"]).unwrap_or_default();
        json!({"tool": "ufw", "active": text.contains("Status: active")})
    } else if has("firewall-cmd") {
        let text = output("firewall-cmd", &["--state"]).unwrap_or_default();
        json!({"tool": "firewalld", "active": text.trim() == "running"})
    } else if has("nft") {
        let text = output("nft", &["list", "ruleset"]).unwrap_or_default();
        json!({"tool": "nftables", "active": text.contains("hook input")})
    } else {
        json!({"tool": null, "active": false})
    };
    if firewall["active"] != json!(true) {
        warnings.push("firewall");
    }
    let mut sshd = read("/etc/ssh/sshd_config");
    if let Ok(dir) = std::fs::read_dir("/etc/ssh/sshd_config.d") {
        for entry in dir.flatten() {
            sshd = read(&entry.path().display().to_string()) + "\n" + &sshd; // first match wins
        }
    }
    let root = sshd_setting(&sshd, "PermitRootLogin").unwrap_or_else(|| "prohibit-password".into());
    let passwords = sshd_setting(&sshd, "PasswordAuthentication").unwrap_or_else(|| "yes".into());
    let ssh = Path::new("/etc/ssh/sshd_config").exists();
    if ssh && root == "yes" {
        warnings.push("ssh_root_password");
    }
    if ssh && passwords == "yes" {
        warnings.push("ssh_passwords");
    }
    let ports: Vec<String> = output("ss", &["-tlnH"]).unwrap_or_default().lines()
        .filter_map(|l| l.split_whitespace().nth(3).map(String::from))
        .filter(|a| !a.starts_with("127.") && !a.starts_with("[::1]")).take(40).collect();
    let failed_logins = output("journalctl", &["--since", "-24h", "-q", "--no-pager", "-g", "Failed password|authentication failure"])
        .unwrap_or_default().lines().count();
    if failed_logins > 20 {
        warnings.push("failed_logins");
    }
    let pending = updates();
    if pending["count"].as_u64().unwrap_or(0) > 0 {
        warnings.push("updates");
    }
    json!({"firewall": firewall, "ssh": {"present": ssh, "root_login": root, "passwords": passwords},
           "open_ports": ports, "failed_logins_24h": failed_logins, "updates": pending["count"],
           "warnings": warnings})
}

fn logs(kind: &str) -> Result<Value, String> {
    let args: Vec<&str> = match kind {
        "warnings" => vec!["-p", "warning", "-n", LOG_LINES, "--no-pager", "-o", "short-iso"],
        "audit" => vec!["_COMM=sudo", "-n", LOG_LINES, "--no-pager", "-o", "short-iso"],
        _ => return Err("unknown log".into()),
    };
    Ok(json!(output("journalctl", &args).unwrap_or_default()))
}

// --- jobs ------------------------------------------------------------------------------------

fn job_state() -> Value {
    let job = JOB.lock().unwrap().clone();
    let tail: String = read(JOB_LOG).lines().rev().take(40).collect::<Vec<_>>().into_iter().rev()
        .collect::<Vec<_>>().join("\n");
    match job {
        None => json!({"running": false}),
        Some(j) => json!({"name": j.name, "running": j.finished.is_none(), "started": j.started,
                          "finished": j.finished, "exit": j.exit, "log": tail}),
    }
}

/// Runs the steps one after another in the background. Refused while another job runs.
fn start(name: String, steps: Vec<(String, Vec<String>, Option<String>)>) -> Result<Value, String> {
    {
        let mut slot = JOB.lock().unwrap();
        if slot.as_ref().is_some_and(|j| j.finished.is_none()) {
            return Err("another job is running".into());
        }
        *slot = Some(Job { name: name.clone(), started: now(), finished: None, exit: None });
    }
    let _ = std::fs::create_dir_all("/var/lib/pi-guard-agent");
    let log = File::create(JOB_LOG).map_err(|e| e.to_string())?;
    std::thread::spawn(move || {
        let mut exit = 0;
        for (program, args, dir) in steps {
            let mut command = Command::new(&program);
            command.args(&args).stdin(Stdio::null())
                .env("DEBIAN_FRONTEND", "noninteractive");
            if let (Ok(out), Ok(err)) = (log.try_clone(), log.try_clone()) {
                command.stdout(out).stderr(err);
            }
            if let Some(dir) = dir {
                command.current_dir(dir);
            }
            exit = command.status().ok().and_then(|s| s.code()).unwrap_or(-1);
            if exit != 0 {
                break;
            }
        }
        if let Some(job) = JOB.lock().unwrap().as_mut() {
            job.finished = Some(now());
            job.exit = Some(exit);
        }
    });
    Ok(job_state())
}

fn step(program: &str, args: &[&str]) -> (String, Vec<String>, Option<String>) {
    (program.into(), args.iter().map(|a| a.to_string()).collect(), None)
}

fn upgrade() -> Result<Value, String> {
    let steps = if has("apt-get") {
        vec![step("apt-get", &["update"]), step("apt-get", &["-y", "-o", "Dpkg::Options::=--force-confold", "upgrade"])]
    } else if has("pacman") {
        vec![step("pacman", &["-Syu", "--noconfirm"])]
    } else if has("dnf") {
        vec![step("dnf", &["-y", "upgrade"])]
    } else {
        return Err("no known package manager".into());
    };
    start("upgrade".into(), steps)
}

/// Only a folder from the list in agent.conf; the Pi's word is compared, never used as a path.
fn listed(list: &[String], wanted: &str) -> Result<String, String> {
    list.iter().find(|d| d.as_str() == wanted).cloned().ok_or("not in this computer's list".into())
}

fn compose(config: &Config, request: &Value) -> Result<Value, String> {
    match request["action"].as_str().unwrap_or("") {
        "list" => Ok(json!(config.compose_dirs)),
        "update" => {
            let dir = listed(&config.compose_dirs, request["dir"].as_str().unwrap_or(""))?;
            let steps = vec![
                ("docker".into(), vec!["compose".into(), "pull".into()], Some(dir.clone())),
                ("docker".into(), vec!["compose".into(), "up".into(), "-d".into(), "--remove-orphans".into()], Some(dir.clone())),
            ];
            start(format!("compose {dir}"), steps)
        }
        _ => Err("unknown compose action".into()),
    }
}

fn git_args(repo: &str, rest: &[&str]) -> Vec<String> {
    let mut args = vec!["-c".to_string(), "core.hooksPath=/dev/null".into(),
                        "-c".into(), format!("safe.directory={repo}"), "-C".into(), repo.into()];
    args.extend(rest.iter().map(|a| a.to_string()));
    args
}

fn git(config: &Config, request: &Value) -> Result<Value, String> {
    match request["action"].as_str().unwrap_or("") {
        "list" => Ok(json!(config.git_repos.iter().map(|repo| {
            let args = git_args(repo, &["log", "-1", "--format=%h %s (%cr)"]);
            let refs: Vec<&str> = args.iter().map(String::as_str).collect();
            let last = output("git", &refs).unwrap_or_default();
            let args = git_args(repo, &["status", "-sb", "--porcelain"]);
            let refs: Vec<&str> = args.iter().map(String::as_str).collect();
            let state = output("git", &refs).unwrap_or_default();
            json!({"repo": repo, "last": last.trim(), "branch": state.lines().next().unwrap_or("").trim(),
                   "changed": state.lines().count().saturating_sub(1)})
        }).collect::<Vec<_>>())),
        "update" => {
            let repo = listed(&config.git_repos, request["repo"].as_str().unwrap_or(""))?;
            start(format!("git {repo}"), vec![("git".into(), git_args(&repo, &["pull", "--ff-only"]), None)])
        }
        _ => Err("unknown git action".into()),
    }
}

pub fn run(config: &Config, request: &Value) -> Option<Result<Value, String>> {
    let action = request["action"].as_str().unwrap_or("");
    Some(match request["cmd"].as_str()? {
        "info" => Ok(info()),
        "security" => Ok(security()),
        "updates" => Ok(updates()),
        "job" => Ok(job_state()),
        "logs" if config.allow_logs => logs(request["kind"].as_str().unwrap_or("")),
        "upgrade" if config.allow_upgrade => upgrade(),
        "compose" if config.allow_docker && !config.compose_dirs.is_empty() => compose(config, request),
        "git" if !config.git_repos.is_empty() => git(config, request),
        "logs" | "upgrade" | "compose" | "git" => Err(format!("not allowed on this computer{}",
            if action.is_empty() { "" } else { " for this" })),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> Config {
        Config::parse("URL=https://x\nTOKEN=t\nGIT_REPOS=/srv/app\nDOCKER_COMPOSE_DIRS=/srv/stack").unwrap()
    }

    #[test]
    fn only_listed_folders() {
        assert!(listed(&["/srv/app".into()], "/srv/app").is_ok());
        assert!(listed(&["/srv/app".into()], "/srv/app/../../etc").is_err());
        assert!(listed(&["/srv/app".into()], "/etc").is_err());
    }

    #[test]
    fn changes_need_their_switch() {
        let c = config();
        for cmd in ["logs", "upgrade"] {
            let result = run(&c, &json!({"cmd": cmd, "kind": "warnings"})).unwrap();
            assert!(result.is_err(), "{cmd} ran without its switch");
        }
        let result = run(&c, &json!({"cmd": "git", "action": "update", "repo": "/etc"})).unwrap();
        assert!(result.is_err());
        assert!(run(&c, &json!({"cmd": "exec"})).is_none());
    }

    #[test]
    fn git_never_runs_hooks() {
        let args = git_args("/srv/app", &["pull", "--ff-only"]);
        assert_eq!(&args[..2], &["-c".to_string(), "core.hooksPath=/dev/null".to_string()]);
        assert!(args.contains(&"safe.directory=/srv/app".to_string()));
    }

    #[test]
    fn reads_sshd_settings() {
        let text = "# PermitRootLogin yes\nPermitRootLogin no\npasswordauthentication NO\n";
        assert_eq!(sshd_setting(text, "PermitRootLogin").as_deref(), Some("no"));
        assert_eq!(sshd_setting(text, "PasswordAuthentication").as_deref(), Some("no"));
    }

    #[test]
    fn reading_works_everywhere() {
        assert!(info()["cores"].as_u64().unwrap() > 0);
        assert!(security()["warnings"].is_array());
    }
}

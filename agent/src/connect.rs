//! Computer-Connect: media control, receiving files, ringing and short texts, like KDE Connect.
//!
//! Everything runs as the person set in CONNECT_USER, in their own session, through
//! `systemd-run --user --machine=USER@`: the agent (root) never writes into a home folder
//! itself and never talks to a session bus. Each part is off until named in CONNECT. The Pi
//! sends fixed actions only; a file name is cleaned here and a file never replaces another.

use std::io::Write as _;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use ring::digest;
use serde_json::{json, Value};

use crate::config::Config;

/// Larger files are refused before the first byte; the Pi checks the same limit.
pub const MAX_FILE: u64 = 2 * 1024 * 1024 * 1024;
const MAX_TEXT: usize = 4000;
const STALE: Duration = Duration::from_secs(120);
const VOLUME_STEP: &str = "5%";

struct Transfer {
    id: String,
    part: String,
    target: String,
    size: u64,
    written: u64,
    hash: digest::Context,
    child: Child,
    stdin: Option<ChildStdin>,
    touched: Instant,
}

static TRANSFER: Mutex<Option<Transfer>> = Mutex::new(None);

pub fn wants(config: &Config, part: &str) -> bool {
    config.connect_user.is_some() && config.connect.iter().any(|p| p == part)
}

/// Runs a program as the Connect user in their session and waits for it.
fn as_user(config: &Config, args: &[&str]) -> Result<String, String> {
    let user = config.connect_user.as_deref().ok_or("CONNECT_USER is not set")?;
    let machine = format!("--machine={user}@");
    let out = Command::new("systemd-run")
        .args(["--user", &machine, "--quiet", "--pipe", "--wait", "--collect", "--"])
        .args(args)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| e.to_string())?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).into_owned())
    } else {
        let error = String::from_utf8_lossy(&out.stderr);
        Err(if error.contains("Failed to connect to bus") || error.contains("No such file") {
            format!("{user} is not logged in (or loginctl enable-linger {user})")
        } else {
            error.chars().take(200).collect()
        })
    }
}

// --- media ---------------------------------------------------------------------------------

// Players speak MPRIS on the session bus; busctl comes with systemd, so nothing is installed.
const MPRIS: &str = "org.mpris.MediaPlayer2.";
const MPRIS_PATH: &str = "/org/mpris/MediaPlayer2";
const PLAYER: &str = "org.mpris.MediaPlayer2.Player";

fn players(config: &Config) -> Vec<String> {
    let text = as_user(config, &["busctl", "--user", "list", "--json=short"]).unwrap_or_default();
    let list: Vec<Value> = serde_json::from_str(&text).unwrap_or_default();
    list.iter().filter_map(|x| x["name"].as_str()).filter(|n| n.starts_with(MPRIS)).map(String::from).collect()
}

fn property(config: &Config, player: &str, name: &str) -> Value {
    let text = as_user(config, &["busctl", "--user", "--json=short", "get-property", player, MPRIS_PATH, PLAYER, name])
        .unwrap_or_default();
    serde_json::from_str::<Value>(&text).map(|v| v["data"].clone()).unwrap_or(Value::Null)
}

/// The player that plays, else the one asked for, else the first one.
fn pick(config: &Config, wanted: Option<&str>) -> Option<(String, Vec<String>)> {
    let all = players(config);
    let chosen = wanted.and_then(|w| all.iter().find(|p| p.as_str() == w).cloned())
        .or_else(|| all.iter().find(|p| property(config, p, "PlaybackStatus") == "Playing").cloned())
        .or_else(|| all.first().cloned())?;
    Some((chosen, all))
}

fn media(config: &Config, request: &Value) -> Result<Value, String> {
    let action = request["action"].as_str().unwrap_or("");
    let method = match action {
        "status" => return media_status(config, request["player"].as_str()),
        "play-pause" => "PlayPause",
        "next" => "Next",
        "previous" => "Previous",
        "stop" => "Stop",
        "volume-up" | "volume-down" | "mute" => {
            volume(config, action)?;
            return media_status(config, request["player"].as_str());
        }
        _ => return Err("unknown media action".into()),
    };
    let (player, _) = pick(config, request["player"].as_str()).ok_or("no player is open")?;
    as_user(config, &["busctl", "--user", "call", &player, MPRIS_PATH, PLAYER, method])?;
    std::thread::sleep(Duration::from_millis(300)); // the player needs a moment to tell its new state
    media_status(config, Some(&player))
}

fn text_of(value: &Value) -> String {
    match &value["data"] {
        Value::String(s) => s.clone(),
        Value::Array(items) => items.iter().filter_map(Value::as_str).filter(|s| !s.is_empty()).collect::<Vec<_>>().join(", "),
        _ => String::new(),
    }
}

/// Title, artist and album only: no addresses or covers of what is open.
fn media_status(config: &Config, wanted: Option<&str>) -> Result<Value, String> {
    let volume = as_user(config, &["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]).unwrap_or_default();
    let level = volume.split_whitespace().nth(1).and_then(|v| v.parse::<f64>().ok()).map(|v| (v * 100.0).round());
    let muted = volume.contains("MUTED");
    let Some((player, all)) = pick(config, wanted) else {
        return Ok(json!({"status": "none", "players": [], "volume": level, "muted": muted}));
    };
    let meta = property(config, &player, "Metadata");
    let short = |p: &str| p.trim_start_matches(MPRIS).split('.').next().unwrap_or("").to_string();
    Ok(json!({
        "status": property(config, &player, "PlaybackStatus").as_str().unwrap_or("Stopped").to_lowercase(),
        "player": player, "name": short(&player),
        "players": all.iter().map(|p| json!({"id": p, "name": short(p)})).collect::<Vec<_>>(),
        "title": text_of(&meta["xesam:title"]), "artist": text_of(&meta["xesam:artist"]),
        "album": text_of(&meta["xesam:album"]), "volume": level, "muted": muted,
    }))
}

/// PipeWire's wpctl first, PulseAudio's pactl when it is not there.
fn volume(config: &Config, action: &str) -> Result<(), String> {
    let (wp, pa): (Vec<String>, Vec<String>) = match action {
        "mute" => (vec!["set-mute".into(), "@DEFAULT_AUDIO_SINK@".into(), "toggle".into()],
                   vec!["set-sink-mute".into(), "@DEFAULT_SINK@".into(), "toggle".into()]),
        _ => {
            let sign = if action == "volume-up" { "+" } else { "-" };
            (vec!["set-volume".into(), "-l".into(), "1.0".into(), "@DEFAULT_AUDIO_SINK@".into(), format!("{VOLUME_STEP}{sign}")],
             vec!["set-sink-volume".into(), "@DEFAULT_SINK@".into(), format!("{sign}{VOLUME_STEP}")])
        }
    };
    let wp: Vec<&str> = std::iter::once("wpctl").chain(wp.iter().map(String::as_str)).collect();
    if as_user(config, &wp).is_err() {
        let pa: Vec<&str> = std::iter::once("pactl").chain(pa.iter().map(String::as_str)).collect();
        as_user(config, &pa)?;
    }
    Ok(())
}

// --- ringing and texts ---------------------------------------------------------------------

const RING_SOUND: &str = "/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga";

/// The Pi sends the words for notifications in the owner's language; English otherwise.
fn note<'a>(request: &'a Value, fallback: &'a str) -> String {
    request["note"].as_str().unwrap_or(fallback).chars().filter(|c| !c.is_control()).take(200).collect()
}

fn ring(config: &Config, request: &Value) -> Result<Value, String> {
    let _ = as_user(config, &["notify-send", "--urgency=critical", "--app-name=pi-guard", "pi-guard", &note(request, "Someone is looking for this computer")]);
    let config = config.clone();
    std::thread::spawn(move || {
        for _ in 0..3 {
            if as_user(&config, &["paplay", RING_SOUND]).is_err() {
                let _ = as_user(&config, &["pw-play", RING_SOUND]);
            }
        }
    });
    Ok(json!(true))
}

/// A short text as a notification, and on the clipboard when a clipboard tool is there.
fn text(config: &Config, request: &Value) -> Result<Value, String> {
    let text: String = request["text"].as_str().unwrap_or("").chars().filter(|c| !c.is_control() || *c == '\n').take(MAX_TEXT).collect();
    if text.trim().is_empty() {
        return Err("empty text".into());
    }
    as_user(config, &["notify-send", "--app-name=pi-guard", "pi-guard", &text])?;
    // Wayland's wl-copy takes the text as an argument; nothing goes through a shell.
    let copied = request["copy"].as_bool() == Some(true) && as_user(config, &["wl-copy", "--", &text]).is_ok();
    Ok(json!({"copied": copied}))
}

// --- files ---------------------------------------------------------------------------------

/// A name without folders, hidden dots or odd characters; at most 120 characters.
pub fn clean_name(name: &str) -> Option<String> {
    let base = name.rsplit(['/', '\\']).next().unwrap_or("");
    let cleaned: String = base.chars()
        .map(|c| if c.is_alphanumeric() || " ._-()+,".contains(c) { c } else { '_' })
        .collect::<String>().trim().trim_start_matches('.').chars().take(120).collect();
    (!cleaned.is_empty() && cleaned != "." && cleaned != "..").then_some(cleaned)
}

fn receive_dir(config: &Config) -> Result<String, String> {
    if let Some(dir) = &config.receive_dir {
        return Ok(dir.clone());
    }
    let user = config.connect_user.as_deref().ok_or("CONNECT_USER is not set")?;
    let home = std::fs::read_to_string("/etc/passwd").ok().and_then(|text| text.lines()
        .map(|l| l.split(':').collect::<Vec<_>>())
        .find(|f| f.len() > 5 && f[0] == user).map(|f| f[5].to_string()))
        .ok_or("no home folder for CONNECT_USER")?;
    Ok(format!("{home}/Downloads/pi-guard"))
}

fn abort(transfer: &mut Transfer, config: &Config) {
    drop(transfer.stdin.take());
    let _ = transfer.child.kill();
    let _ = transfer.child.wait();
    let _ = as_user(config, &["rm", "-f", "--", &transfer.part]);
}

fn decode(text: &str) -> Option<Vec<u8>> {
    let value = |c: u8| -> Option<u32> {
        Some(match c {
            b'A'..=b'Z' => c - b'A',
            b'a'..=b'z' => c - b'a' + 26,
            b'0'..=b'9' => c - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => return None,
        } as u32)
    };
    let bytes: Vec<u8> = text.bytes().filter(|b| !b.is_ascii_whitespace()).collect();
    if !bytes.len().is_multiple_of(4) {
        return None;
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    for chunk in bytes.chunks(4) {
        let pad = chunk.iter().rev().take_while(|b| **b == b'=').count();
        if pad > 2 {
            return None;
        }
        let mut n = 0u32;
        for (i, b) in chunk.iter().enumerate() {
            n = (n << 6) | if i >= 4 - pad { 0 } else { value(*b)? };
        }
        out.extend_from_slice(&n.to_be_bytes()[1..4 - pad]);
    }
    Some(out)
}

fn file(config: &Config, request: &Value) -> Result<Value, String> {
    let mut slot = TRANSFER.lock().map_err(|_| "busy")?;
    if let Some(stale) = slot.as_mut().filter(|t| t.touched.elapsed() > STALE) {
        abort(stale, config);
        *slot = None;
    }
    let id = request["transfer"].as_str().unwrap_or("").to_string();
    match request["action"].as_str().unwrap_or("") {
        "begin" => {
            if slot.is_some() {
                return Err("another file is on its way".into());
            }
            let name = clean_name(request["name"].as_str().unwrap_or("")).ok_or("bad file name")?;
            let size = request["size"].as_u64().ok_or("no size")?;
            if size > MAX_FILE {
                return Err("file too large".into());
            }
            let dir = receive_dir(config)?;
            as_user(config, &["mkdir", "-p", "--", &dir])?;
            // A free name: "x.pdf", "x (2).pdf", …; checked as the user, in the user's folder.
            let (stem, ext) = match name.rsplit_once('.') { Some((s, e)) if !s.is_empty() => (s.to_string(), format!(".{e}")), _ => (name.clone(), String::new()) };
            let target = (1..100).map(|n| if n == 1 { format!("{dir}/{name}") } else { format!("{dir}/{stem} ({n}){ext}") })
                .find(|path| as_user(config, &["test", "!", "-e", path]).is_ok()).ok_or("no free file name")?;
            let part = format!("{target}.part");
            let user = config.connect_user.as_deref().unwrap_or("");
            let mut child = Command::new("systemd-run")
                .args(["--user", &format!("--machine={user}@"), "--quiet", "--pipe", "--wait", "--collect", "--",
                       "dd", &format!("of={part}"), "bs=64K", "conv=excl", "oflag=nofollow", "status=none"])
                .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null())
                .spawn().map_err(|e| e.to_string())?;
            let stdin = child.stdin.take();
            *slot = Some(Transfer { id, part, target, size, written: 0, hash: digest::Context::new(&digest::SHA256),
                                    child, stdin, touched: Instant::now() });
            Ok(json!(true))
        }
        "chunk" => {
            let transfer = slot.as_mut().filter(|t| t.id == id).ok_or("no such transfer")?;
            let data = decode(request["data"].as_str().unwrap_or("")).ok_or("bad data")?;
            if transfer.written + data.len() as u64 > transfer.size {
                let mut t = slot.take().unwrap();
                abort(&mut t, config);
                return Err("more data than announced".into());
            }
            let stdin = transfer.stdin.as_mut().ok_or("closed")?;
            if let Err(e) = stdin.write_all(&data) {
                let mut t = slot.take().unwrap();
                abort(&mut t, config);
                return Err(e.to_string());
            }
            transfer.hash.update(&data);
            transfer.written += data.len() as u64;
            transfer.touched = Instant::now();
            Ok(json!(transfer.written))
        }
        "end" => {
            let mut t = slot.take().filter(|t| t.id == id).ok_or("no such transfer")?;
            drop(t.stdin.take());
            let done = t.child.wait().map(|s| s.success()).unwrap_or(false);
            let sum: String = t.hash.clone().finish().as_ref().iter().map(|b| format!("{b:02x}")).collect();
            if !done || t.written != t.size || request["sha256"].as_str() != Some(sum.as_str()) {
                abort(&mut t, config);
                return Err("the file arrived incomplete and was removed".into());
            }
            as_user(config, &["mv", "-n", "--", &t.part, &t.target])?;
            let shown = t.target.rsplit('/').next().unwrap_or("").to_string();
            let _ = as_user(config, &["notify-send", "--app-name=pi-guard", "pi-guard", &note(request, "Received {name}").replace("{name}", &shown)]);
            Ok(json!({"saved": t.target}))
        }
        "abort" => {
            if let Some(mut t) = slot.take() {
                abort(&mut t, config);
            }
            Ok(json!(true))
        }
        _ => Err("unknown file action".into()),
    }
}

pub fn run(config: &Config, request: &Value) -> Option<Result<Value, String>> {
    let cmd = request["cmd"].as_str()?;
    let part = match cmd { "media" => "media", "ring" => "ring", "text" => "text", "file" => "files", _ => return None };
    if !wants(config, part) {
        return Some(Err(format!("{part} is not allowed on this computer (CONNECT in agent.conf)")));
    }
    Some(match cmd {
        "media" => media(config, request),
        "ring" => ring(config, request),
        "text" => text(config, request),
        _ => file(config, request),
    })
}

pub fn status(config: &Config) -> Value {
    json!({
        "media": wants(config, "media"), "files": wants(config, "files"),
        "ring": wants(config, "ring"), "text": wants(config, "text"),
        "max_file": MAX_FILE,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_stay_in_their_folder() {
        assert_eq!(clean_name("../../etc/passwd").as_deref(), Some("passwd"));
        assert_eq!(clean_name("C:\\x\\Bild 1.jpg").as_deref(), Some("Bild 1.jpg"));
        assert_eq!(clean_name(".bashrc").as_deref(), Some("bashrc"));
        assert_eq!(clean_name("a;$(rm -rf).txt").as_deref(), Some("a__(rm -rf).txt")); // no shell sees it
        assert!(clean_name("..").is_none() && clean_name("/").is_none() && clean_name("").is_none());
    }

    #[test]
    fn base64_round_trip() {
        assert_eq!(decode("aGFsbG8=").unwrap(), b"hallo");
        assert_eq!(decode("aGFsbG8h").unwrap(), b"hallo!");
        assert_eq!(decode("aGE=").unwrap(), b"ha");
        assert!(decode("a$==").is_none() && decode("abc").is_none());
    }

    #[test]
    fn everything_is_off_until_named() {
        let config = Config::parse("URL=https://p\nTOKEN=x").unwrap();
        for cmd in ["media", "ring", "text", "file"] {
            assert!(run(&config, &json!({"cmd": cmd})).unwrap().is_err());
        }
        assert!(run(&config, &json!({"cmd": "status"})).is_none());
        let config = Config::parse("URL=https://p\nTOKEN=x\nCONNECT=media\nCONNECT_USER=anna").unwrap();
        assert!(wants(&config, "media") && !wants(&config, "files"));
    }
}

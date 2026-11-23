//! `KEY=value` file; the token makes it secret, so the installer sets mode 0600.

#[derive(Debug, Clone)]
pub struct Config {
    pub url: String,
    pub token: String,
    pub ca_file: Option<String>,
    pub allow_power: bool,
    pub allow_docker: bool,
    pub auto_update: bool,
    /// Off unless set here: reading logs, and updating the system.
    pub allow_logs: bool,
    pub allow_upgrade: bool,
    /// The only folders pi-guard may update: Docker Compose projects and Git repositories.
    pub compose_dirs: Vec<String>,
    pub git_repos: Vec<String>,
    /// Computer-Connect: whose session it acts in, which parts are on, where files go.
    pub connect_user: Option<String>,
    pub connect: Vec<String>,
    pub receive_dir: Option<String>,
}

const CONNECT_PARTS: [&str; 4] = ["media", "files", "ring", "text"];

fn user_name(value: &str) -> Result<String, String> {
    let ok = value.len() <= 32 && value.chars().next().is_some_and(|c| c.is_ascii_lowercase() || c == '_')
        && value.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-');
    if ok && value != "root" { Ok(value.to_string()) } else { Err(format!("not a user name: {value}")) }
}

/// Absolute folders without "..", separated by commas.
fn folders(value: &str) -> Result<Vec<String>, String> {
    value.split(',').map(str::trim).filter(|v| !v.is_empty()).map(|v| {
        let path = v.trim_end_matches('/');
        if !path.starts_with('/') || path.split('/').any(|part| part == ".." || part == ".") {
            return Err(format!("not an absolute folder: {v}"));
        }
        Ok(path.to_string())
    }).collect()
}

fn is_local(url: &str) -> bool {
    let rest = url.split_once("://").map_or(url, |x| x.1);
    rest.starts_with("127.") || rest.starts_with("localhost")
}

impl Config {
    pub fn parse(text: &str) -> Result<Config, String> {
        let (mut url, mut token, mut ca_file) = (String::new(), String::new(), None);
        let (mut allow_power, mut allow_docker, mut auto_update) = (true, true, true);
        let (mut allow_logs, mut allow_upgrade) = (false, false);
        let (mut compose_dirs, mut git_repos) = (Vec::new(), Vec::new());
        let (mut connect_user, mut connect, mut receive_dir) = (None, Vec::new(), None);
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let (key, value) = line.split_once('=').ok_or(format!("bad line: {line}"))?;
            let value = value.trim().to_string();
            match key.trim() {
                "URL" => url = value.trim_end_matches('/').to_string(),
                "TOKEN" => token = value,
                "CA_FILE" => ca_file = Some(value),
                "ALLOW_POWER" => allow_power = value == "true",
                "ALLOW_DOCKER" => allow_docker = value == "true",
                "AUTO_UPDATE" => auto_update = value == "true",
                "ALLOW_LOGS" => allow_logs = value == "true",
                "ALLOW_UPGRADE" => allow_upgrade = value == "true",
                "DOCKER_COMPOSE_DIRS" => compose_dirs = folders(&value)?,
                "GIT_REPOS" => git_repos = folders(&value)?,
                "CONNECT_USER" => connect_user = Some(user_name(&value)?),
                "CONNECT" => {
                    connect = value.split(',').map(|v| v.trim().to_string()).filter(|v| !v.is_empty()).collect();
                    if let Some(bad) = connect.iter().find(|p| !CONNECT_PARTS.contains(&p.as_str())) {
                        return Err(format!("CONNECT knows media, files, ring and text, not {bad}"));
                    }
                }
                "RECEIVE_DIR" => receive_dir = folders(&value)?.into_iter().next(),
                other => return Err(format!("unknown setting {other}")),
            }
        }
        if url.is_empty() || token.is_empty() {
            return Err("URL and TOKEN are required".into());
        }
        if url.starts_with("http://") && !is_local(&url) {
            return Err("plain http is only allowed on this computer; use https".into());
        }
        Ok(Config { url, token, ca_file, allow_power, allow_docker, auto_update, allow_logs,
                    allow_upgrade, compose_dirs, git_repos, connect_user, connect, receive_dir })
    }

    pub fn load(path: &str) -> Result<Config, String> {
        let text = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
        Config::parse(&text)
    }

    pub fn websocket_url(&self) -> Result<String, String> {
        if let Some(rest) = self.url.strip_prefix("https://") {
            return Ok(format!("wss://{rest}"));
        }
        if let Some(rest) = self.url.strip_prefix("http://") {
            return Ok(format!("ws://{rest}"));
        }
        Err("URL must start with https://".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_http_only_locally() {
        assert!(Config::parse("URL=http://192.168.178.109:8443\nTOKEN=x").is_err());
        assert!(Config::parse("URL=http://127.0.0.1:8443\nTOKEN=x").is_ok());
        assert!(Config::parse("URL=https://pi:8443\nTOKEN=x").is_ok());
    }

    #[test]
    fn needs_url_and_token() {
        assert!(Config::parse("URL=https://pi:8443").is_err());
        assert!(Config::parse("TOKEN=x").is_err());
        assert!(Config::parse("URL=https://pi\nTOKEN=x\nWHAT=1").is_err());
    }

    #[test]
    fn folders_are_absolute() {
        let c = Config::parse("URL=https://p\nTOKEN=x\nGIT_REPOS=/srv/a, /srv/b/").unwrap();
        assert_eq!(c.git_repos, vec!["/srv/a", "/srv/b"]);
        assert!(!c.allow_logs && !c.allow_upgrade);
        assert!(Config::parse("URL=https://p\nTOKEN=x\nGIT_REPOS=srv/a").is_err());
        assert!(Config::parse("URL=https://p\nTOKEN=x\nGIT_REPOS=/srv/../etc").is_err());
    }

    #[test]
    fn connect_settings() {
        let c = Config::parse("URL=https://p\nTOKEN=x\nCONNECT=media, files\nCONNECT_USER=anna\nRECEIVE_DIR=/home/anna/In/").unwrap();
        assert_eq!(c.connect, vec!["media", "files"]);
        assert_eq!(c.receive_dir.as_deref(), Some("/home/anna/In"));
        assert!(Config::parse("URL=https://p\nTOKEN=x\nCONNECT=shell").is_err());
        assert!(Config::parse("URL=https://p\nTOKEN=x\nCONNECT_USER=root").is_err());
        assert!(Config::parse("URL=https://p\nTOKEN=x\nCONNECT_USER=a b").is_err());
    }

    #[test]
    fn switches() {
        let c = Config::parse("URL=https://p\nTOKEN=x\nALLOW_POWER=false").unwrap();
        assert!(!c.allow_power && c.allow_docker);
    }
}

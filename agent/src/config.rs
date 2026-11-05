//! `KEY=value` file; the token makes it secret, so the installer sets mode 0600.

#[derive(Debug, Clone)]
pub struct Config {
    pub url: String,
    pub token: String,
    pub ca_file: Option<String>,
    pub allow_power: bool,
    pub allow_docker: bool,
    pub auto_update: bool,
}

fn is_local(url: &str) -> bool {
    let rest = url.split_once("://").map_or(url, |x| x.1);
    rest.starts_with("127.") || rest.starts_with("localhost")
}

impl Config {
    pub fn parse(text: &str) -> Result<Config, String> {
        let (mut url, mut token, mut ca_file) = (String::new(), String::new(), None);
        let (mut allow_power, mut allow_docker, mut auto_update) = (true, true, true);
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
                other => return Err(format!("unknown setting {other}")),
            }
        }
        if url.is_empty() || token.is_empty() {
            return Err("URL and TOKEN are required".into());
        }
        if url.starts_with("http://") && !is_local(&url) {
            return Err("plain http is only allowed on this computer; use https".into());
        }
        Ok(Config { url, token, ca_file, allow_power, allow_docker, auto_update })
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
    fn switches() {
        let c = Config::parse("URL=https://p\nTOKEN=x\nALLOW_POWER=false").unwrap();
        assert!(!c.allow_power && c.allow_docker);
    }
}

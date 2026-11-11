//! What travels over the TLS connection: one JSON line to say who is there, then either
//! control lines or, on a data connection, the raw bytes of one forwarded connection.

use ring::digest::{digest, SHA256};
use ring::rand::{SecureRandom, SystemRandom};
use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt};

pub const VERSION: u32 = 1;
pub const MAX_LINE: usize = 4096;

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Hello {
    Control {
        v: u32,
        name: String,
        token: String,
        ports: Vec<u16>,
    },
    Data {
        v: u32,
        name: String,
        token: String,
        id: u64,
    },
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Welcome {
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub ports: Vec<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "lowercase", deny_unknown_fields)]
pub enum Control {
    Open { id: u64, port: u16 },
    Ping { n: u64 },
    Pong { n: u64 },
}

/// One line, at most MAX_LINE bytes; None when the other side closed.
pub async fn read_line<R: AsyncBufRead + Unpin>(reader: &mut R) -> std::io::Result<Option<String>> {
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().await?;
        if available.is_empty() {
            return Ok(if line.is_empty() {
                None
            } else {
                Some(String::from_utf8_lossy(&line).into())
            });
        }
        if let Some(end) = available.iter().position(|b| *b == b'\n') {
            line.extend_from_slice(&available[..end]);
            reader.consume(end + 1);
            break;
        }
        let taken = available.len();
        line.extend_from_slice(available);
        reader.consume(taken);
        if line.len() > MAX_LINE {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "line too long",
            ));
        }
    }
    if line.len() > MAX_LINE {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "line too long",
        ));
    }
    String::from_utf8(line)
        .map(Some)
        .map_err(|_| std::io::Error::new(std::io::ErrorKind::InvalidData, "not UTF-8"))
}

pub async fn write_json<W: AsyncWrite + Unpin, T: Serialize>(
    writer: &mut W,
    value: &T,
) -> std::io::Result<()> {
    let mut line = serde_json::to_vec(value).map_err(std::io::Error::other)?;
    line.push(b'\n');
    writer.write_all(&line).await?;
    writer.flush().await
}

pub fn token_hash(token: &str) -> String {
    digest(&SHA256, token.as_bytes())
        .as_ref()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Same time for every wrong token, so its letters cannot be guessed one by one.
pub fn token_matches(token: &str, expected_hash: &str) -> bool {
    let seen = token_hash(token);
    same(
        seen.as_bytes(),
        expected_hash.to_ascii_lowercase().as_bytes(),
    )
}

/// Compares in a time that depends only on the length.
pub fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

pub fn random_u64() -> u64 {
    let mut bytes = [0u8; 8];
    SystemRandom::new()
        .fill(&mut bytes)
        .expect("system randomness");
    u64::from_le_bytes(bytes)
}

pub fn new_token() -> String {
    let mut bytes = [0u8; 32];
    SystemRandom::new()
        .fill(&mut bytes)
        .expect("system randomness");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_match_only_their_hash() {
        let token = new_token();
        assert_eq!(token.len(), 64);
        assert!(token_matches(&token, &token_hash(&token)));
        assert!(token_matches(&token, &token_hash(&token).to_uppercase()));
        assert!(!token_matches("wrong", &token_hash(&token)));
    }

    #[test]
    fn messages_keep_their_shape() {
        let hello = Hello::Data {
            v: 1,
            name: "pi".into(),
            token: "t".into(),
            id: 7,
        };
        let text = serde_json::to_string(&hello).unwrap();
        assert_eq!(
            text,
            r#"{"kind":"data","v":1,"name":"pi","token":"t","id":7}"#
        );
        assert_eq!(serde_json::from_str::<Hello>(&text).unwrap(), hello);
        let open = serde_json::to_string(&Control::Open { id: 1, port: 8080 }).unwrap();
        assert_eq!(open, r#"{"open":{"id":1,"port":8080}}"#);
        assert!(serde_json::from_str::<Hello>(
            r#"{"kind":"control","v":1,"name":"a","token":"t","ports":[],"x":1}"#
        )
        .is_err());
    }

    #[tokio::test]
    async fn long_lines_are_refused() {
        let data = vec![b'a'; MAX_LINE + 10];
        let mut reader = tokio::io::BufReader::new(data.as_slice());
        assert!(read_line(&mut reader).await.is_err());
        let mut fine = tokio::io::BufReader::new(&b"one\ntwo"[..]);
        assert_eq!(read_line(&mut fine).await.unwrap().as_deref(), Some("one"));
        assert_eq!(read_line(&mut fine).await.unwrap().as_deref(), Some("two"));
        assert_eq!(read_line(&mut fine).await.unwrap(), None);
    }
}

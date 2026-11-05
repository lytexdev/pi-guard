//! Base64, for keys. Small enough not to need another dependency in a program that runs as root.

const TABLE: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

pub fn encode(data: &[u8]) -> String {
    let mut text = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let n = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        for i in 0..4 {
            if i <= chunk.len() {
                text.push(TABLE[(n >> (18 - 6 * i) & 63) as usize] as char);
            } else {
                text.push('=');
            }
        }
    }
    text
}

pub fn decode(text: &str) -> Option<Vec<u8>> {
    let bytes = text.as_bytes();
    if bytes.len() % 4 != 0 {
        return None;
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    for chunk in bytes.chunks(4) {
        let mut n = 0u32;
        let mut pad = 0;
        for &b in chunk {
            n <<= 6;
            if b == b'=' {
                pad += 1;
            } else if pad > 0 {
                return None;
            } else {
                n |= TABLE.iter().position(|&t| t == b)? as u32;
            }
        }
        if pad > 2 {
            return None;
        }
        out.push((n >> 16) as u8);
        if pad < 2 {
            out.push((n >> 8) as u8);
        }
        if pad < 1 {
            out.push(n as u8);
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn goes_there_and_back() {
        for data in [&b""[..], b"a", b"ab", b"abc", b"\xff\x00\x10 pi-guard"] {
            assert_eq!(decode(&encode(data)).unwrap(), data);
        }
        assert_eq!(encode(b"abc"), "YWJj");
        assert_eq!(decode("Y!Jj"), None);
        assert_eq!(decode("YWJ"), None);
    }
}

//! This device's side of the post-quantum key exchange, on Linux and Windows alike. It runs beside
//! the tunnel, so a slow answer never holds up anything else.

use crate::b64;
use pi_guard_kem::{device, KEY_SIZE, PORT, SERVER};
use std::net::{IpAddr, SocketAddr, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// About as often as WireGuard renews its own session. It also has to stay below the time after
/// which a device stops trusting its last key, see `LAST_KEY_VALID`.
const EVERY: u64 = 120;
const RETRY: u64 = 30;
const WAIT: Duration = Duration::from_secs(8);
/// The Pi returns to the starting key a little later.
pub const LAST_KEY_VALID: u64 = 190;

// A device must always hold a key it still trusts.
const _: () = assert!(EVERY < LAST_KEY_VALID);

pub fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

#[derive(Default)]
pub struct State {
    pub last: u64,
    pub key: Option<String>,
    tried: u64,
    busy: bool,
}

impl State {
    fn due(&self, time: u64) -> bool {
        !self.busy && time.saturating_sub(self.last) >= EVERY && time.saturating_sub(self.tried) >= RETRY
    }
}

pub type Shared = Arc<Mutex<State>>;

fn exchange(start: &[u8; KEY_SIZE]) -> Result<String, String> {
    let address = SocketAddr::new(SERVER.parse::<IpAddr>().map_err(|e| e.to_string())?, PORT);
    let mut stream = TcpStream::connect_timeout(&address, WAIT).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(WAIT)).map_err(|e| e.to_string())?;
    stream.set_write_timeout(Some(WAIT)).map_err(|e| e.to_string())?;
    let key = device(&mut stream, start).map_err(|e| e.to_string())?;
    Ok(b64::encode(key.as_slice()))
}

/// A new key when one is due. `install` hands it to WireGuard.
pub fn renew(shared: &Shared, start: &str, install: impl FnOnce(&str) -> Result<(), String> + Send + 'static) {
    let Some(start) = b64::decode(start).and_then(|raw| <[u8; KEY_SIZE]>::try_from(raw).ok()) else { return };
    {
        let mut state = shared.lock().unwrap();
        if !state.due(now()) {
            return;
        }
        state.busy = true;
        state.tried = now();
    }
    let shared = shared.clone();
    thread::spawn(move || {
        let result = exchange(&start).and_then(|key| install(&key).map(|()| key));
        let mut state = shared.lock().unwrap();
        state.busy = false;
        match result {
            Ok(key) => {
                state.last = now();
                state.key = Some(key);
            }
            Err(error) => eprintln!("key exchange: {error}"),
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_key_is_renewed_on_time_and_not_more_often() {
        let state = State { last: 1000, tried: 1000, ..State::default() };
        assert!(!state.due(1000 + EVERY - 1));
        assert!(state.due(1000 + EVERY));
    }

    #[test]
    fn a_failed_exchange_waits_a_moment() {
        let state = State { last: 0, tried: 1000, ..State::default() };
        assert!(!state.due(1000 + RETRY - 1));
        assert!(state.due(1000 + RETRY));
    }

    #[test]
    fn only_one_exchange_at_a_time() {
        assert!(!State { busy: true, ..State::default() }.due(1_000_000));
    }
}

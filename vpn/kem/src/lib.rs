//! The post-quantum key exchange of pi-guard: one implementation for the Pi, computers and phones.
//!
//! It runs inside the WireGuard tunnel. The device makes fresh ML-KEM-1024 and HQC-256 keys, the
//! Pi answers with a secret for each, and both derive a new preshared key for WireGuard:
//!
//!   device -> Pi   MAGIC, ML-KEM key, HQC key
//!   Pi -> device   ML-KEM ciphertext, HQC ciphertext, proof
//!   device -> Pi   proof
//!
//! Two methods from different mathematics, so a break of one leaves the other. Both are mixed with
//! the device's starting key, which the proofs show each side to hold: without it, nobody can sit
//! in the middle, not even with a quantum computer. Every message has a fixed size, so there is
//! nothing to parse, and a failed exchange changes nothing.

use ml_kem::{Decapsulate, Encapsulate, Kem, KeyExport, MlKem1024, TryKeyInit};
use pqcrypto_hqc::hqc256;
use pqcrypto_traits::kem::{Ciphertext as _, PublicKey as _, SecretKey as _, SharedSecret as _};
use sha2::{Digest, Sha256};
use std::io::{self, Read, Write};
use zeroize::{Zeroize, Zeroizing};

#[cfg(target_os = "android")]
mod android;

/// TCP, inside the tunnel only.
pub const PORT: u16 = 9999;
pub const KEY_SIZE: usize = 32;
/// The Pi's address in the tunnel.
pub const SERVER: &str = "10.8.0.1";

const MAGIC: &[u8; 4] = b"PGQ1";
const ML_KEM_KEY: usize = 1568;
const ML_KEM_CIPHERTEXT: usize = 1568;
const HQC_KEY: usize = 7245;
const HQC_CIPHERTEXT: usize = 14421;
const HQC_SECRET: usize = 64;
const PROOF: usize = 32;
const HELLO: usize = MAGIC.len() + ML_KEM_KEY + HQC_KEY;
const ANSWER: usize = ML_KEM_CIPHERTEXT + HQC_CIPHERTEXT + PROOF;

pub type Key = Zeroizing<[u8; KEY_SIZE]>;

#[derive(Debug, PartialEq, Eq)]
pub enum Failure {
    /// The other side did not answer properly, or the connection broke.
    Connection,
    /// Not what this protocol sends.
    Malformed,
    /// The other side does not hold the starting key.
    WrongProof,
}

impl From<io::Error> for Failure {
    fn from(_: io::Error) -> Self {
        Failure::Connection
    }
}

impl std::fmt::Display for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Failure::Connection => "no answer from the key exchange",
            Failure::Malformed => "the key exchange sent something else",
            Failure::WrongProof => "the other side of the key exchange could not prove who it is",
        })
    }
}

/// The library's own `decapsulate` panics on a ciphertext it rejects, and the other side chooses
/// the ciphertext: a forged answer must be a refused answer, not a crash.
fn hqc_open(ciphertext: &[u8], secret: &hqc256::SecretKey) -> Result<Zeroizing<[u8; HQC_SECRET]>, Failure> {
    if ciphertext.len() != HQC_CIPHERTEXT {
        return Err(Failure::Malformed);
    }
    let mut shared = Zeroizing::new([0u8; HQC_SECRET]);
    // Sizes: `shared` and `ciphertext` are checked above, `secret` comes from the library itself.
    let status = unsafe {
        pqcrypto_hqc::ffi::PQCLEAN_HQC256_CLEAN_crypto_kem_dec(
            shared.as_mut_ptr(),
            ciphertext.as_ptr(),
            secret.as_bytes().as_ptr(),
        )
    };
    if status == 0 { Ok(shared) } else { Err(Failure::Malformed) }
}

fn derive(start: &[u8; KEY_SIZE], ml_kem: &[u8], hqc: &[u8], hello: &[u8], answer: &[u8]) -> Key {
    let transcript = Sha256::new().chain_update(hello).chain_update(answer).finalize();
    let mut hash = Sha256::new();
    hash.update(b"pi-guard pq key v1");
    hash.update(start);
    hash.update(ml_kem);
    hash.update(hqc);
    hash.update(transcript);
    let mut key = Zeroizing::new([0u8; KEY_SIZE]);
    key.copy_from_slice(&hash.finalize());
    key
}

fn proof(label: &[u8], key: &[u8; KEY_SIZE]) -> [u8; PROOF] {
    Sha256::new().chain_update(label).chain_update(key).finalize().into()
}

/// Takes the same time wherever the first difference is.
fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |all, (x, y)| all | (x ^ y)) == 0
}

const FROM_PI: &[u8] = b"pi-guard pq proof from the pi";
const FROM_DEVICE: &[u8] = b"pi-guard pq proof from the device";

/// The device's side. The key is only returned once the Pi has proven itself.
pub fn device<S: Read + Write>(stream: &mut S, start: &[u8; KEY_SIZE]) -> Result<Key, Failure> {
    let (ml_kem_secret, ml_kem_public) = MlKem1024::generate_keypair();
    let (hqc_public, hqc_secret) = hqc256::keypair();
    let mut hello = Vec::with_capacity(HELLO);
    hello.extend_from_slice(MAGIC);
    hello.extend_from_slice(&ml_kem_public.to_bytes());
    hello.extend_from_slice(hqc_public.as_bytes());
    stream.write_all(&hello)?;
    stream.flush()?;

    let mut answer = vec![0u8; ANSWER];
    stream.read_exact(&mut answer)?;
    let (signed, their_proof) = answer.split_at(ANSWER - PROOF);
    let (ml_kem_ciphertext, hqc_ciphertext) = signed.split_at(ML_KEM_CIPHERTEXT);
    let mut ml_kem = ml_kem_secret.decapsulate_slice(ml_kem_ciphertext).map_err(|_| Failure::Malformed)?;
    let hqc = hqc_open(hqc_ciphertext, &hqc_secret)?;
    let key = derive(start, &ml_kem, hqc.as_slice(), &hello, signed);
    ml_kem.zeroize();
    if !same(their_proof, &proof(FROM_PI, &key)) {
        return Err(Failure::WrongProof);
    }
    stream.write_all(&proof(FROM_DEVICE, &key))?;
    stream.flush()?;
    Ok(key)
}

/// The Pi's side, for a device already known by its address in the tunnel.
pub fn pi<S: Read + Write>(stream: &mut S, start: &[u8; KEY_SIZE]) -> Result<Key, Failure> {
    let mut hello = vec![0u8; HELLO];
    stream.read_exact(&mut hello)?;
    let (magic, keys) = hello.split_at(MAGIC.len());
    if magic != MAGIC {
        return Err(Failure::Malformed);
    }
    let (ml_kem_public, hqc_public) = keys.split_at(ML_KEM_KEY);
    let ml_kem_public = <MlKem1024 as Kem>::EncapsulationKey::new_from_slice(ml_kem_public)
        .map_err(|_| Failure::Malformed)?;
    let hqc_public = hqc256::PublicKey::from_bytes(hqc_public).map_err(|_| Failure::Malformed)?;
    let (ml_kem_ciphertext, mut ml_kem) = ml_kem_public.encapsulate();
    let (hqc, hqc_ciphertext) = hqc256::encapsulate(&hqc_public);

    let mut answer = Vec::with_capacity(ANSWER);
    answer.extend_from_slice(&ml_kem_ciphertext);
    answer.extend_from_slice(hqc_ciphertext.as_bytes());
    let key = derive(start, &ml_kem, hqc.as_bytes(), &hello, &answer);
    ml_kem.zeroize();
    answer.extend_from_slice(&proof(FROM_PI, &key));
    stream.write_all(&answer)?;
    stream.flush()?;

    let mut their_proof = [0u8; PROOF];
    stream.read_exact(&mut their_proof)?;
    if !same(&their_proof, &proof(FROM_DEVICE, &key)) {
        return Err(Failure::WrongProof);
    }
    Ok(key)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::{TcpListener, TcpStream};
    use std::thread;

    type Both = (Result<Key, Failure>, Result<Key, Failure>);

    fn exchange(device_start: [u8; KEY_SIZE], pi_start: [u8; KEY_SIZE]) -> Both {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let other = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            pi(&mut stream, &pi_start)
        });
        let mut stream = TcpStream::connect(address).unwrap();
        let mine = device(&mut stream, &device_start);
        drop(stream);
        (mine, other.join().unwrap())
    }

    /// Sends what it is given, then keeps what comes back.
    struct Scripted {
        incoming: io::Cursor<Vec<u8>>,
        sent: Vec<u8>,
    }

    impl Scripted {
        fn new(incoming: Vec<u8>) -> Self {
            Scripted { incoming: io::Cursor::new(incoming), sent: vec![] }
        }
    }

    impl Read for Scripted {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            self.incoming.read(buffer)
        }
    }

    impl Write for Scripted {
        fn write(&mut self, data: &[u8]) -> io::Result<usize> {
            self.sent.extend_from_slice(data);
            Ok(data.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn both_sides_end_up_with_the_same_new_key() {
        let (mine, theirs) = exchange([7; KEY_SIZE], [7; KEY_SIZE]);
        let (mine, theirs) = (mine.unwrap(), theirs.unwrap());
        assert_eq!(*mine, *theirs);
        assert_ne!(*mine, [7; KEY_SIZE]);
    }

    #[test]
    fn every_exchange_makes_a_different_key() {
        let first = exchange([7; KEY_SIZE], [7; KEY_SIZE]).0.unwrap();
        let second = exchange([7; KEY_SIZE], [7; KEY_SIZE]).0.unwrap();
        assert_ne!(*first, *second);
    }

    #[test]
    fn nobody_without_the_starting_key_gets_through() {
        let (mine, theirs) = exchange([7; KEY_SIZE], [8; KEY_SIZE]);
        assert_eq!(mine.unwrap_err(), Failure::WrongProof);
        assert_eq!(theirs.unwrap_err(), Failure::Connection, "the Pi must not accept any key");
    }

    #[test]
    fn a_wrong_greeting_is_refused() {
        let mut stream = Scripted::new(vec![b'x'; HELLO]);
        assert_eq!(pi(&mut stream, &[7; KEY_SIZE]).unwrap_err(), Failure::Malformed);
        assert!(stream.sent.is_empty());
    }

    #[test]
    fn garbage_instead_of_keys_is_refused_without_a_crash() {
        let mut hello = MAGIC.to_vec();
        hello.resize(HELLO, 0xff);
        let mut stream = Scripted::new(hello);
        assert!(pi(&mut stream, &[7; KEY_SIZE]).is_err());
    }

    #[test]
    fn a_cut_off_message_is_refused() {
        let mut stream = Scripted::new(MAGIC.to_vec());
        assert_eq!(pi(&mut stream, &[7; KEY_SIZE]).unwrap_err(), Failure::Connection);
        let mut stream = Scripted::new(vec![0; ANSWER - 1]);
        assert_eq!(device(&mut stream, &[7; KEY_SIZE]).unwrap_err(), Failure::Connection);
    }

    #[test]
    fn a_forged_answer_is_refused() {
        for byte in [0x00, 0xff, 0x5a] {
            let mut stream = Scripted::new(vec![byte; ANSWER]);
            assert!(device(&mut stream, &[7; KEY_SIZE]).is_err());
            assert_eq!(stream.sent.len(), HELLO, "no proof goes out for a forged answer");
        }
    }

    #[test]
    fn the_key_depends_on_everything() {
        let base = derive(&[1; 32], &[2; 32], &[3; 64], b"a", b"b");
        assert_ne!(*base, *derive(&[9; 32], &[2; 32], &[3; 64], b"a", b"b"));
        assert_ne!(*base, *derive(&[1; 32], &[9; 32], &[3; 64], b"a", b"b"));
        assert_ne!(*base, *derive(&[1; 32], &[2; 32], &[9; 64], b"a", b"b"));
        assert_ne!(*base, *derive(&[1; 32], &[2; 32], &[3; 64], b"x", b"b"));
        assert_ne!(*base, *derive(&[1; 32], &[2; 32], &[3; 64], b"a", b"x"));
    }

    #[test]
    fn the_derivation_stays_as_it_is() {
        // Devices and the Pi update at different times: this must never change silently.
        let key = derive(&[1; 32], &[2; 32], &[3; 64], b"a", b"b");
        let text: String = key.iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(text, "8b5383a1aba9244231a017c42a1a5febdab2990ce9600519cdd7ea83af9a7d9e");
    }

    #[test]
    fn sizes_match_the_methods() {
        assert_eq!(hqc256::public_key_bytes(), HQC_KEY);
        assert_eq!(hqc256::ciphertext_bytes(), HQC_CIPHERTEXT);
        assert_eq!(hqc256::shared_secret_bytes(), HQC_SECRET);
        let (_, public) = MlKem1024::generate_keypair();
        assert_eq!(public.to_bytes().len(), ML_KEM_KEY);
    }
}

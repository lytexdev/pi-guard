//! TLS without a certificate authority: the server makes its own certificate once, and each
//! client trusts exactly that certificate, by its SHA-256 fingerprint.

use ring::digest::{digest, SHA256};
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{verify_tls12_signature, verify_tls13_signature, CryptoProvider};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, ServerName, UnixTime};
use rustls::{ClientConfig, DigitallySignedStruct, ServerConfig, SignatureScheme};
use std::path::Path;
use std::sync::Arc;

pub fn provider() -> Arc<CryptoProvider> {
    Arc::new(rustls::crypto::ring::default_provider())
}

pub fn fingerprint(der: &[u8]) -> String {
    digest(&SHA256, der)
        .as_ref()
        .iter()
        .map(|b| format!("{b:02X}"))
        .collect::<Vec<_>>()
        .join(":")
}

pub fn parse_fingerprint(text: &str) -> Option<[u8; 32]> {
    let hex: String = text.chars().filter(|c| *c != ':').collect();
    if hex.len() != 64 {
        return None;
    }
    let mut out = [0u8; 32];
    for (i, byte) in out.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).ok()?;
    }
    Some(out)
}

/// A new key and certificate, written with mode 0600 for the key. Returns the fingerprint.
pub fn generate(cert: &Path, key: &Path, names: Vec<String>) -> Result<String, String> {
    let made = rcgen::generate_simple_self_signed(names).map_err(|e| e.to_string())?;
    write_private(key, made.key_pair.serialize_pem().as_bytes())?;
    std::fs::write(cert, made.cert.pem()).map_err(|e| format!("{}: {e}", cert.display()))?;
    Ok(fingerprint(made.cert.der()))
}

fn write_private(path: &Path, data: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    file.write_all(data)
        .map_err(|e| format!("{}: {e}", path.display()))
}

pub fn read_certificate(path: &Path) -> Result<CertificateDer<'static>, String> {
    let data = std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut reader = data.as_slice();
    let first = rustls_pemfile::certs(&mut reader).next();
    first
        .ok_or_else(|| format!("{}: no certificate", path.display()))?
        .map_err(|e| format!("{}: {e}", path.display()))
}

pub fn server_config(cert: &Path, key: &Path) -> Result<Arc<ServerConfig>, String> {
    let chain = vec![read_certificate(cert)?];
    let data = std::fs::read(key).map_err(|e| format!("{}: {e}", key.display()))?;
    let key: PrivateKeyDer<'static> = rustls_pemfile::private_key(&mut data.as_slice())
        .map_err(|e| e.to_string())?
        .ok_or("no private key")?;
    let config = ServerConfig::builder_with_provider(provider())
        .with_protocol_versions(&[&rustls::version::TLS13])
        .map_err(|e| e.to_string())?
        .with_no_client_auth()
        .with_single_cert(chain, key)
        .map_err(|e| e.to_string())?;
    Ok(Arc::new(config))
}

#[derive(Debug)]
struct Pinned {
    expected: [u8; 32],
    provider: Arc<CryptoProvider>,
}

impl ServerCertVerifier for Pinned {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _name: &ServerName<'_>,
        _ocsp: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        // The fingerprint is the trust: names and dates of a self-made certificate say nothing.
        let seen = digest(&SHA256, end_entity.as_ref());
        if crate::proto::same(seen.as_ref(), &self.expected) {
            Ok(ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General(
                "server certificate does not match the fingerprint".into(),
            ))
        }
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls12_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(
            message,
            cert,
            dss,
            &self.provider.signature_verification_algorithms,
        )
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider
            .signature_verification_algorithms
            .supported_schemes()
    }
}

pub fn client_config(fingerprint: &str) -> Result<Arc<ClientConfig>, String> {
    let expected = parse_fingerprint(fingerprint).ok_or("bad fingerprint")?;
    let provider = provider();
    let config = ClientConfig::builder_with_provider(provider.clone())
        .with_protocol_versions(&[&rustls::version::TLS13])
        .map_err(|e| e.to_string())?
        .dangerous()
        .with_custom_certificate_verifier(Arc::new(Pinned { expected, provider }))
        .with_no_client_auth();
    Ok(Arc::new(config))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fingerprints_read_back_with_and_without_colons() {
        let text = fingerprint(b"certificate");
        let bytes = parse_fingerprint(&text).unwrap();
        assert_eq!(parse_fingerprint(&text.replace(':', "")), Some(bytes));
        assert_eq!(parse_fingerprint("AB:CD"), None);
        assert_eq!(parse_fingerprint(&"zz".repeat(32)), None);
    }
}

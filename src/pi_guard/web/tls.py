"""HTTPS for the dashboard with a small local certificate authority.

Browsers only allow microphone access, installable web apps and push
notifications on secure origins. A home device has no public domain, so
pi-guard creates its own CA once; installing `ca.crt` on a phone or laptop
makes the dashboard trusted there. The server certificate is reissued when the
device's addresses change or it is about to expire.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import ssl
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pi_guard.security.files import secure_dir

log = logging.getLogger(__name__)

CA_DAYS = 3650
SERVER_DAYS = 397  # Apple rejects TLS certificates valid for longer
RENEW_BEFORE = timedelta(days=30)


def local_names() -> list[str]:
    host = socket.gethostname()
    names = {"localhost", host, f"{host}.local"}
    return sorted(names)


def local_addresses() -> list[str]:
    addresses = {"127.0.0.1", "::1"}
    try:
        out = subprocess.run(["ip", "-j", "addr"], capture_output=True, text=True, timeout=5,
                             check=True).stdout  # fmt: skip
        for link in json.loads(out):
            for info in link.get("addr_info", []):
                if info.get("scope") == "global":
                    addresses.add(info["local"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        pass
    return sorted(addresses)


def _openssl(*args: str) -> str:
    return subprocess.run(["openssl", *args], capture_output=True, text=True, timeout=60,
                          check=True).stdout  # fmt: skip


def _normalize(entry: str) -> str:
    entry = entry.strip()
    try:
        return str(ipaddress.ip_address(entry))  # "0:0:0:0:0:0:0:1" and "::1" compare equal
    except ValueError:
        return entry.lower()


def _san(cert: Path) -> set[str]:
    text = _openssl("x509", "-in", str(cert), "-noout", "-ext", "subjectAltName")
    entries = text.split("\n", 1)[-1].split(",")
    return {_normalize(e.split(":", 1)[1]) for e in entries if ":" in e}


def _expires(cert: Path) -> datetime:
    text = _openssl("x509", "-in", str(cert), "-noout", "-enddate").strip()
    return datetime.strptime(text.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)


def ensure_certificates(directory: Path, extra: list[str] | None = None) -> tuple[Path, Path, Path]:
    """Create or renew CA and server certificate; returns (ca_cert, cert, key).

    `extra` adds names or addresses the device is reached by but cannot see
    itself, e.g. the host's LAN address when running in a container.
    """
    secure_dir(directory)
    ca_key, ca_cert = directory / "ca.key", directory / "ca.crt"
    key, cert = directory / "server.key", directory / "server.crt"
    if not ca_cert.exists():
        log.info("creating local certificate authority in %s", directory)
        _openssl("req", "-x509", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                 "-nodes", "-keyout", str(ca_key), "-out", str(ca_cert), "-days", str(CA_DAYS),
                 "-subj", f"/CN=pi-guard local CA ({socket.gethostname()})",
                 "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                 "-addext", "keyUsage=critical,keyCertSign,cRLSign")  # fmt: skip
        ca_key.chmod(0o600)
    names, addresses = local_names(), local_addresses()
    for entry in extra or []:
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            names.append(entry)
        else:
            addresses.append(entry)
    wanted = {_normalize(entry) for entry in (*names, *addresses)}
    if cert.exists():
        try:
            fresh = _expires(cert) - datetime.now(UTC) > RENEW_BEFORE
            if fresh and wanted <= _san(cert):
                return ca_cert, cert, key
        except (subprocess.SubprocessError, ValueError):
            pass
    log.info("issuing dashboard certificate for %s", ", ".join(sorted(wanted)))
    san = ",".join([*(f"DNS:{n}" for n in names), *(f"IP:{a}" for a in addresses)])
    csr = directory / "server.csr"
    extensions = directory / "server.ext"
    extensions.write_text(
        f"subjectAltName={san}\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n"
    )
    _openssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
             "-keyout", str(key), "-out", str(csr),
             "-subj", f"/CN={socket.gethostname()}")  # fmt: skip
    _openssl("x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
             "-CAcreateserial", "-out", str(cert), "-days", str(SERVER_DAYS),
             "-extfile", str(extensions))  # fmt: skip
    key.chmod(0o600)
    csr.unlink(missing_ok=True)
    extensions.unlink(missing_ok=True)
    return ca_cert, cert, key


def server_context(cert: Path, key: Path) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context

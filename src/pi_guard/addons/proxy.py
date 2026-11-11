"""Reverse proxy: services behind NAT, reached through a server you choose (pgproxy, in Rust).

This Pi is either the server, which other machines connect out to, or a client of a server
elsewhere. The server decides which ports each client may open; tokens are shown once and the
server keeps only their SHA-256. Clients trust exactly one server certificate, by fingerprint.
Nothing here opens a port in a router or a firewall: that stays the owner's decision.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hashlib
import json
import logging
import re
import secrets
import shutil
import ssl
import time
from pathlib import Path
from typing import Any

from pi_guard.security.files import atomic_write
from pi_guard.storage.db import Database

log = logging.getLogger(__name__)

STATE_KEY = "proxy.state"
ROLES = ("off", "server", "client")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
TARGET_RE = re.compile(r"^[A-Za-z0-9.\-]{1,200}:\d{1,5}$|^\[[0-9A-Fa-f:]{2,45}\]:\d{1,5}$")
SERVER_RE = TARGET_RE
MIN_PORT = 1024
MAX_CLIENTS = 64
MAX_FORWARDS = 32
STATUS_FRESH = 30
RESTART_MAX = 60
# A public port is the point of a reverse proxy; 127.0.0.1 keeps one for this server only.
ANY_ADDRESS = "0.0.0.0"  # noqa: S104
BINDS = (ANY_ADDRESS, "127.0.0.1", "::")


class ProxyError(Exception):
    """A key for the dashboard."""


def _port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ProxyError("web.px_bad_port") from None
    if not MIN_PORT <= port <= 65535:
        raise ProxyError("web.px_bad_port")
    return port


def fingerprint_of(pem: str) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem)
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())


def valid_fingerprint(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Fa-f]{64}", text.replace(":", "")))


class Proxy:
    def __init__(self, data_dir: Path, db: Database) -> None:
        self.dir = Path(data_dir) / "proxy"
        self._db = db
        self._changed = asyncio.Event()
        self.running = False
        self.last_error: str | None = None

    # --- state -----------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        saved = self._db.get(STATE_KEY) or {}
        return {"role": "off", "listen_port": 7000, "clients": [], "client": None, **saved}

    def _save(self, state: dict[str, Any]) -> None:
        self._db.set(STATE_KEY, state)
        self._changed.set()

    @staticmethod
    def binary() -> str | None:
        found = shutil.which("pgproxy")
        if found:
            return found
        default = Path("/usr/local/bin/pgproxy")
        return str(default) if default.is_file() else None

    def public(self) -> dict[str, Any]:
        """For the dashboard: everything except tokens."""
        state = self.state()
        client = state.get("client")
        return {
            "role": state["role"], "listen_port": state["listen_port"],
            "installed": self.binary() is not None, "running": self.running,
            "error": self.last_error, "fingerprint": self.fingerprint(),
            "clients": [{k: v for k, v in c.items() if k != "token_sha256"}
                        for c in state["clients"]],
            "client": {k: v for k, v in client.items() if k != "token"} if client else None,
            "status": self.status(),
        }  # fmt: skip

    def status(self) -> dict[str, Any] | None:
        path = self.dir / "status.json"
        try:
            if time.time() - path.stat().st_mtime > STATUS_FRESH:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    # --- server role -----------------------------------------------------------------------

    def _ensure_certificate(self) -> None:
        cert, key = self.dir / "cert.pem", self.dir / "key.pem"
        if cert.is_file() and key.is_file():
            return
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        private = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pgproxy")])
        now = datetime.datetime.now(datetime.UTC)
        certificate = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(private.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("pgproxy")]), critical=False)
            .sign(private, hashes.SHA256())
        )  # fmt: skip
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())  # fmt: skip
        atomic_write(key, pem, mode=0o600)
        atomic_write(cert, certificate.public_bytes(serialization.Encoding.PEM), mode=0o644)

    def fingerprint(self) -> str | None:
        cert = self.dir / "cert.pem"
        return fingerprint_of(cert.read_text()) if cert.is_file() else None

    def set_role(self, role: str, listen_port: Any = None) -> None:
        if role not in ROLES:
            raise ProxyError("web.px_bad_role")
        state = self.state()
        if listen_port not in (None, ""):
            port = _port(listen_port)
            if any(port == p["port"] for c in state["clients"] for p in c["ports"]):
                raise ProxyError("web.px_port_taken")
            state["listen_port"] = port
        if role == "server":
            self._ensure_certificate()
        state["role"] = role
        self._save(state)

    def add_client(self, name: str, ports: list[Any], bind: str = ANY_ADDRESS) -> str:
        """A new machine for this server. Returns its token, which is not kept here."""
        if not NAME_RE.match(name):
            raise ProxyError("web.px_bad_name")
        if bind not in BINDS:
            raise ProxyError("web.px_bad_bind")
        state = self.state()
        if any(c["name"] == name for c in state["clients"]):
            raise ProxyError("web.px_name_taken")
        if len(state["clients"]) >= MAX_CLIENTS:
            raise ProxyError("web.px_too_many")
        wanted = sorted({_port(p) for p in ports})
        if len(wanted) > MAX_FORWARDS:
            raise ProxyError("web.px_too_many")
        used = {p["port"] for c in state["clients"] for p in c["ports"]} | {state["listen_port"]}
        if used & set(wanted):
            raise ProxyError("web.px_port_taken")
        token = secrets.token_hex(32)
        state["clients"].append({
            "name": name, "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "ports": [{"port": p, "bind": bind} for p in wanted],
            "created": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        })  # fmt: skip
        self._save(state)
        return token

    def remove_client(self, name: str) -> bool:
        state = self.state()
        kept = [c for c in state["clients"] if c["name"] != name]
        if len(kept) == len(state["clients"]):
            return False
        state["clients"] = kept
        self._save(state)
        return True

    def client_config(self, name: str, token: str, host: str) -> dict[str, Any]:
        """What the new machine needs, to save as client.json there."""
        state = self.state()
        entry = next(c for c in state["clients"] if c["name"] == name)
        return {
            "server": f"{host}:{state['listen_port']}", "fingerprint": self.fingerprint(),
            "name": name, "token": token,
            "forwards": [{"remote_port": p["port"], "local": "127.0.0.1:PORT"}
                         for p in entry["ports"]],
        }  # fmt: skip

    # --- client role -----------------------------------------------------------------------

    def set_client(self, data: dict[str, Any]) -> None:
        state = self.state()
        old = state.get("client") or {}
        server = str(data.get("server", "")).strip()
        if not SERVER_RE.match(server):
            raise ProxyError("web.px_bad_server")
        fingerprint = str(data.get("fingerprint", "")).strip().upper()
        if not valid_fingerprint(fingerprint):
            raise ProxyError("web.px_bad_fingerprint")
        name = str(data.get("name", "")).strip()
        if not NAME_RE.match(name):
            raise ProxyError("web.px_bad_name")
        token = str(data.get("token") or "").strip() or old.get("token", "")
        if not 32 <= len(token) <= 128 or not token.isascii() or not token.isprintable():
            raise ProxyError("web.px_bad_token")
        forwards = []
        seen = set()
        for item in list(data.get("forwards") or [])[:MAX_FORWARDS]:
            port = _port(item.get("remote_port"))
            local = str(item.get("local", "")).strip()
            if not TARGET_RE.match(local) or port in seen:
                raise ProxyError("web.px_bad_forward")
            seen.add(port)
            forwards.append({"remote_port": port, "local": local})
        state["client"] = {"server": server, "fingerprint": fingerprint, "name": name,
                           "token": token, "forwards": forwards}  # fmt: skip
        self._save(state)

    # --- running pgproxy -------------------------------------------------------------------

    def _config(self, state: dict[str, Any]) -> dict[str, Any] | None:
        status = str(self.dir / "status.json")
        if state["role"] == "server":
            self._ensure_certificate()
            return {
                "listen": f"{ANY_ADDRESS}:{state['listen_port']}",
                "cert": str(self.dir / "cert.pem"), "key": str(self.dir / "key.pem"),
                "status": status,
                "clients": [{k: c[k] for k in ("name", "token_sha256", "ports")}
                            for c in state["clients"]],
            }  # fmt: skip
        if state["role"] == "client" and state.get("client"):
            return {**state["client"], "status": status}
        return None

    async def run(self) -> None:
        delay = 1
        while True:
            self._changed.clear()
            state = self.state()
            config = self._config(state)
            binary = self.binary()
            if config is None or binary is None:
                self.last_error = None if config is None else "web.px_not_installed"
                await self._changed.wait()
                continue
            self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.dir / "config.json"
            # Holds the client's token: only for this user.
            atomic_write(path, json.dumps(config, indent=2).encode(), mode=0o600)
            process = await asyncio.create_subprocess_exec(
                binary, state["role"], str(path), stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )  # fmt: skip
            self.running = True
            self.last_error = None
            started = time.monotonic()
            reader = asyncio.create_task(self._log(process))
            changed = asyncio.create_task(self._changed.wait())
            exited = asyncio.create_task(process.wait())
            try:
                await asyncio.wait({changed, exited}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                self.running = False
                changed.cancel()
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), 5)
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
                reader.cancel()
            if exited.done() and not self._changed.is_set():
                self.last_error = "web.px_stopped"
                lasted = time.monotonic() - started > RESTART_MAX
                delay = 1 if lasted else min(delay * 2, RESTART_MAX)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), delay)

    @staticmethod
    async def _log(process: asyncio.subprocess.Process) -> None:
        assert process.stderr is not None
        while line := await process.stderr.readline():
            log.info("pgproxy: %s", line.decode(errors="replace").strip()[:300])

"""Keys, the pairing code, the handshake proofs and what a helper may reach."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

VERSION = 1
CODE_PREFIX = "PGF1."
NONCE_BYTES = 32
MAX_FRAME = 12 * 1024 * 1024  # a tunnelled answer, base64 included
MAX_BODY = 8 * 1024 * 1024
NAME_MAX = 40

# Rights the helped side can grant. The role is "admin" or "viewer"; the rest are extras.
ROLES = ("admin", "viewer")
EXTRAS = ("alerts", "cameras", "medical")

# Never through the tunnel, whatever was granted: logins and anything that outlives the visit,
# copies of all data, and the links themselves. Streams cannot pass a request/answer tunnel.
BLOCKED = ("/api/federation", "/api/account", "/api/login", "/api/keyfiles", "/api/tokens",
           "/api/dashboard-users", "/api/export", "/api/backup", "/api/email/export",
           "/api/setup", "/api/mobile/pair", "/api/updates/", "/api/v1/", "/api/agent/ws",
           "/api/mobile/ws", "/api/plugins/", "/api/connect")  # fmt: skip
STREAMS = re.compile(r"^/api/live/[^/]+/(video|overlay|audio|call)$")
CAMERAS = ("/api/live/", "/api/media/", "/api/image/", "/api/people/", "/api/faces/",
           "/api/evidence", "/api/mobile/emergency/media")  # fmt: skip
CAMERA_PATTERN = re.compile(r"^/api/events/\d+/faces")
MEDICAL = ("/api/medical",)
# What the tunnel carries at all: API answers only. The page and its scripts always come from
# the helper's own Pi, so a helped Pi can never run code in the helper's dashboard.
TUNNEL_PATH = re.compile(r"^/api/[A-Za-z0-9_./%:{}=@+-]*$")
# Shown inline on the helper's side; anything else only as a download, never as a page.
INLINE_TYPES = (
    "application/json", "text/plain", "image/jpeg", "image/png", "image/webp", "image/gif",
    "video/mp4", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav",
)  # fmt: skip


class ProtocolError(ValueError):
    pass


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ProtocolError("bad base64") from exc


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(public: bytes) -> str:
    """Short and readable, to compare on both screens: 4 groups of 4 hex digits."""
    digest = hashlib.sha256(public).hexdigest()[:16]
    return " ".join(digest[i : i + 4] for i in range(0, 16, 4))


def proof_message(side: str, mine: bytes, theirs: bytes, key: bytes) -> bytes:
    """What a side signs: its role, the other side's nonce first, and the key it talks to."""
    return b"|".join((b"pi-guard-fed", str(VERSION).encode(), side.encode(), theirs, mine, key))


def sign(key: Ed25519PrivateKey, message: bytes) -> str:
    return b64(key.sign(message))


def verify(public: bytes, signature: str, message: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(unb64(signature), message)
    except (InvalidSignature, ValueError, ProtocolError):
        return False
    return True


def new_nonce() -> bytes:
    return secrets.token_bytes(NONCE_BYTES)


def clean_name(value: object) -> str:
    return " ".join(str(value or "").split())[:NAME_MAX]


@dataclass(frozen=True)
class Code:
    """What the helped side needs to reach the helper once and to know it is the right one."""

    url: str  # wss://host:port/federation/ws
    pin: str  # sha256 of the helper's certificate, hex
    key: bytes  # the helper's public key
    token: str  # one time, a day
    name: str  # the helper household, as shown

    def encode(self) -> str:
        body = {"u": self.url, "p": self.pin, "k": b64(self.key), "t": self.token, "n": self.name}
        return CODE_PREFIX + b64(json.dumps(body, separators=(",", ":")).encode())


def parse_code(text: str) -> Code:
    text = "".join(text.split())
    if not text.startswith(CODE_PREFIX) or len(text) > 2000:
        raise ProtocolError("web.fed_bad_code")
    try:
        body: dict[str, Any] = json.loads(unb64(text[len(CODE_PREFIX) :]))
        url, pin, key = str(body["u"]), str(body["p"]).lower(), unb64(str(body["k"]))
        token, name = str(body["t"]), clean_name(body.get("n"))
    except (ProtocolError, ValueError, KeyError, TypeError) as exc:
        raise ProtocolError("web.fed_bad_code") from exc
    if not re.fullmatch(r"wss://[A-Za-z0-9.\-\[\]:]{1,260}/federation/ws", url):
        raise ProtocolError("web.fed_bad_code")
    if not re.fullmatch(r"[0-9a-f]{64}", pin) or len(key) != 32 or not 20 <= len(token) <= 64:
        raise ProtocolError("web.fed_bad_code")
    return Code(url, pin, key, token, name or "pi-guard")


def tunnel_allowed(path: str, role: str, extras: list[str]) -> bool:
    """Whether a helper's request may reach this path at all. The dashboard then checks the
    role like for anybody else."""
    if not TUNNEL_PATH.match(path) or ".." in path:
        return False
    if path.startswith(BLOCKED) or STREAMS.match(path):
        return False
    if (path.startswith(CAMERAS) or CAMERA_PATTERN.match(path)) and "cameras" not in extras:
        return False
    if path.startswith(MEDICAL) and "medical" not in extras:
        return False
    return role in ROLES

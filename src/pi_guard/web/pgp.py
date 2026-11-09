"""Logging in with an OpenPGP signature.

The owner adds a public key once. To log in, the page shows a short text with a random part;
the owner signs it with `gpg --clearsign` and pastes the result. The Pi checks it with GnuPG
in a fresh, temporary keyring that holds only the added keys, so no other key on the system
counts, and nothing is kept. Like a passkey, this replaces the password; the code word and
security questions are still asked.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pi_guard.core.timeutil import utcnow
from pi_guard.storage.db import Database

MAX_KEY = 64 * 1024
MAX_SIGNED = 16 * 1024
MAX_KEYS = 5
FINGERPRINT = re.compile(r"^[0-9A-F]{40}$|^[0-9A-F]{64}$")


class PgpError(ValueError):
    """`key` is a translation key."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def available() -> bool:
    return shutil.which("gpg") is not None


def _gpg(home: Path, args: list[str], data: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["gpg", "--batch", "--no-tty", "--homedir", str(home), "--no-options",
         "--no-auto-key-locate", "--trust-model", "always", *args],
        input=data, capture_output=True, timeout=20, check=False,
    )  # fmt: skip


def fingerprint_of(armored: str) -> tuple[str, str]:
    """(fingerprint, user id) of the one public key in `armored`."""
    if not armored.strip() or len(armored) > MAX_KEY or "PRIVATE KEY" in armored:
        raise PgpError("web.pgp_bad_key")
    with tempfile.TemporaryDirectory(prefix="pg-gpg-") as home:
        shown = _gpg(Path(home), ["--with-colons", "--import-options", "show-only", "--import"],
                     armored.encode())  # fmt: skip
    rows = [line.split(":") for line in shown.stdout.decode(errors="replace").splitlines()]
    fingerprints = [r[9] for r in rows if r[0] == "fpr"]
    primaries = [r for r in rows if r[0] == "pub"]
    if shown.returncode != 0 or len(primaries) != 1 or not fingerprints:
        raise PgpError("web.pgp_bad_key")
    names = [r[9] for r in rows if r[0] == "uid"]
    return fingerprints[0], (names[0] if names else "")[:120]


def verify(keys: list[str], signed: str, expected: str) -> str | None:
    """The fingerprint whose key signed exactly `expected`, or None."""
    if len(signed) > MAX_SIGNED or "BEGIN PGP SIGNED MESSAGE" not in signed:
        return None
    with tempfile.TemporaryDirectory(prefix="pg-gpg-") as tmp:
        home = Path(tmp)
        imported = _gpg(home, ["--import"], "\n".join(keys).encode())
        if imported.returncode != 0:
            return None
        result = _gpg(home, ["--status-fd", "2", "--output", "-", "--decrypt"], signed.encode())
    status = result.stderr.decode(errors="replace")
    valid = re.search(r"^\[GNUPG:\] VALIDSIG ([0-9A-F]+) .* ([0-9A-F]+)$", status, re.M)
    if result.returncode != 0 or valid is None or "[GNUPG:] GOODSIG" not in status:
        return None
    if result.stdout.decode(errors="replace").strip() != expected.strip():
        return None  # signed, but another text: perhaps an old login
    return valid.group(2)  # the primary key's fingerprint


class PgpKeys:
    def __init__(self, db: Database) -> None:
        self._db = db

    def entries(self) -> list[dict[str, Any]]:
        row = self._db.execute("SELECT value FROM web_factors WHERE name = 'pgp'").fetchone()
        return list(json.loads(row["value"])) if row else []

    def _store(self, keys: list[dict[str, Any]]) -> None:
        self._db.execute("DELETE FROM web_factors WHERE name = 'pgp'")
        if keys:
            self._db.execute("INSERT INTO web_factors (name, value) VALUES ('pgp', ?)",
                             (json.dumps(keys),))  # fmt: skip

    def add(self, armored: str) -> dict[str, Any]:
        fingerprint, name = fingerprint_of(armored)
        keys = [k for k in self.entries() if k["fingerprint"] != fingerprint]
        if len(keys) >= MAX_KEYS:
            raise PgpError("web.pgp_too_many")
        entry = {"fingerprint": fingerprint, "name": name, "key": armored.strip(),
                 "created": utcnow().isoformat()}  # fmt: skip
        self._store([*keys, entry])
        return {k: entry[k] for k in ("fingerprint", "name", "created")}

    def delete(self, fingerprint: str) -> bool:
        keys = self.entries()
        kept = [k for k in keys if k["fingerprint"] != fingerprint]
        self._store(kept)
        return len(kept) < len(keys)

    def armored(self) -> list[str]:
        return [str(k["key"]) for k in self.entries()]

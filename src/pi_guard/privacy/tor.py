"""Optional Tor integration. Does nothing when tor is not installed or disabled."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from pi_guard.privacy.compat import validate_tor
from pi_guard.privacy.errors import PrivacyError

TORRC_DROPIN = Path("/etc/tor/torrc.d/pi-guard.conf")
SOCKS_DEFAULT = 9050


def installed() -> bool:
    return shutil.which("tor") is not None


def normalize(tor: dict[str, Any]) -> dict[str, Any]:
    mode = str(tor.get("mode") or "off")
    enabled = bool(tor.get("enabled")) and mode != "off"
    try:
        port = int(tor.get("socks_port") or SOCKS_DEFAULT)
    except (TypeError, ValueError) as exc:
        raise PrivacyError("web.priv_tor_port") from exc
    if not 1024 <= port <= 65535:
        raise PrivacyError("web.priv_tor_port")
    bridges = str(tor.get("bridges") or "")
    if len(bridges) > 8192:
        raise PrivacyError("web.priv_tor_bridges_big")
    return {
        "enabled": enabled,
        "mode": mode if enabled else "off",
        "socks_port": port,
        "bridges": bridges,
        "use_bridges": bool(tor.get("use_bridges")) and bool(bridges.strip()),
    }


def status() -> dict[str, Any]:
    bootstrap = Path("/run/pi-guard-status/privacy.tor")
    text = bootstrap.read_text(encoding="utf-8") if bootstrap.is_file() else ""
    return {
        "installed": installed(),
        "running": "running=1" in text,
        "bootstrap": _bootstrap_pct(text),
        "circuit_ok": "circuit=1" in text,
    }


def _bootstrap_pct(text: str) -> int | None:
    for line in text.splitlines():
        if line.startswith("bootstrap="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                return None
    return None


def bridges_file_body(tor: dict[str, Any]) -> str:
    """Content the root job may install into torrc.d. No shell, only Tor directives."""
    lines = [
        "# Managed by pi-guard. Do not edit while privacy Tor is enabled.",
        f"SocksPort {int(tor['socks_port'])}",
    ]
    if tor.get("use_bridges") and tor.get("bridges"):
        lines.append("UseBridges 1")
        for raw in str(tor["bridges"]).splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if not line.lower().startswith("bridge "):
                line = f"Bridge {line}"
            # Reject anything that could be another torrc command.
            if any(c in line for c in ";`$\\") or "\n" in line:
                raise PrivacyError("web.priv_tor_bridges_bad")
            lines.append(line)
    return "\n".join(lines) + "\n"


def update(store: Any, caps: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    tor = normalize(body)
    validate_tor(tor, caps)
    if tor["enabled"] and store.data["chain"].get("active") and store.data["chain"].get("hops"):
        raise PrivacyError("web.priv_tor_with_hops")
    store.data["tor"] = tor
    store.write_tor_want(tor["enabled"])
    bridges_path = store._dir / "tor.bridges"
    if tor["enabled"] and tor.get("use_bridges"):
        from pi_guard.security.files import atomic_write

        atomic_write(bridges_path, bridges_file_body(tor).encode())
    else:
        bridges_path.unlink(missing_ok=True)
    if store.data.get("profile") != "custom":
        store.data["profile"] = "custom"
    store.save()
    return tor

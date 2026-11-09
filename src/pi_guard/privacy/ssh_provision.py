"""SSH remote WireGuard setup (dry-run only until explicitly applied by the owner).

Never disables host-key verification. Never runs unreviewed remote install scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pi_guard.privacy.errors import PrivacyError


@dataclass(frozen=True)
class RemotePlan:
    host: str
    user: str
    steps: tuple[str, ...]
    warning: str = "web.priv_ssh_preview"


def plan(host: str, user: str = "root", *, port: int = 22) -> RemotePlan:
    host = (host or "").strip()
    user = (user or "").strip() or "root"
    if not host or len(host) > 253 or any(c in host for c in ";`$\\\"'"):
        raise PrivacyError("web.priv_endpoint")
    if not 1 <= port <= 65535:
        raise PrivacyError("web.priv_endpoint")
    return RemotePlan(
        host=host,
        user=user,
        steps=(
            f"ssh -o StrictHostKeyChecking=yes -p {port} {user}@{host} 'uname -a'",
            "Detect OS and whether wireguard / wg-quick are installed",
            "Show proposed /etc/wireguard changes for confirmation",
            "Generate keys on the remote only after explicit apply",
        ),
    )


def apply_not_implemented(_plan: RemotePlan) -> None:
    """Automatic apply is intentionally not implemented in this release."""
    raise PrivacyError("web.priv_ssh_manual")


def as_dict(plan_: RemotePlan) -> dict[str, Any]:
    return {
        "host": plan_.host,
        "user": plan_.user,
        "steps": list(plan_.steps),
        "warning": plan_.warning,
        "auto_apply": False,
    }

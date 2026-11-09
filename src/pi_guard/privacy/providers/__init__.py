"""VPN exit provider adapters."""

from __future__ import annotations

from pi_guard.privacy.providers.base import ExitPeer, Provider
from pi_guard.privacy.providers.custom_wg import CustomWireGuard
from pi_guard.privacy.providers.import_wg import parse_wireguard_conf
from pi_guard.privacy.providers.mullvad import MullvadProvider

__all__ = [
    "CustomWireGuard",
    "ExitPeer",
    "MullvadProvider",
    "Provider",
    "parse_wireguard_conf",
]

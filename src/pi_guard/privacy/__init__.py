"""Optional privacy upstream on the Pi: one exit (Mullvad or an own server), DNS, kill switch.

Devices still connect to the home WireGuard server. When enabled, selected traffic
leaves through the exit (Mullvad, optionally multi-hop, or an own WireGuard server) instead of the
home WAN. Everything here stays off until the owner turns it on.
"""

from __future__ import annotations

from pi_guard.privacy.service import PrivacyService

__all__ = ["PrivacyService"]

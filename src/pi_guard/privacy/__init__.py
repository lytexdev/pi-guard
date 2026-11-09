"""Optional privacy upstream on the Pi: multi-hop exit, DNS, Tor, profiles.

Devices still connect to the home WireGuard server. When enabled, selected traffic
leaves through an upstream hop (custom WireGuard, Mullvad, or Tor) instead of the
home WAN. Everything here stays off until the owner turns it on.
"""

from __future__ import annotations

from pi_guard.privacy.service import PrivacyService

__all__ = ["PrivacyService"]

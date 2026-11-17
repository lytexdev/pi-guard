"""Messenger delivery modes and email test recipient binding."""

from __future__ import annotations

from types import SimpleNamespace

from pi_guard.messaging.hub import delivery_allows


def test_delivery_allows_modes() -> None:
    s = SimpleNamespace(telegram_delivery="alerts", signal_delivery="emergency", email_delivery="both")
    assert delivery_allows(s, "telegram", "alerts")
    assert not delivery_allows(s, "telegram", "emergency")
    assert not delivery_allows(s, "signal", "alerts")
    assert delivery_allows(s, "signal", "emergency")
    assert delivery_allows(s, "email", "alerts")
    assert delivery_allows(s, "email", "emergency")
    assert delivery_allows(SimpleNamespace(), "discord", "alerts")  # default both

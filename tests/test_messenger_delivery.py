"""Messenger delivery modes and email test recipient binding."""

from __future__ import annotations

from types import SimpleNamespace

from pi_guard.messaging.hub import delivery_allows


def test_delivery_allows_modes() -> None:
    s = SimpleNamespace(
        telegram_delivery="alerts", signal_delivery="emergency", email_delivery="both"
    )
    assert delivery_allows(s, "telegram", "alerts")
    assert not delivery_allows(s, "telegram", "emergency")
    assert not delivery_allows(s, "signal", "alerts")
    assert delivery_allows(s, "signal", "emergency")
    assert delivery_allows(s, "email", "alerts")
    assert delivery_allows(s, "email", "emergency")
    assert delivery_allows(SimpleNamespace(), "discord", "alerts")  # default both


def test_messengers_need_no_admin_list_when_the_dashboard_is_on() -> None:
    import pytest
    from pydantic import ValidationError

    from pi_guard.config.settings import Settings

    base = {"discord_enabled": True, "discord_bot_token": "x" * 40, "_env_file": None}
    Settings(**base, dashboard_enabled=True, dashboard_tls=False, telegram_enabled=False)  # by code
    with pytest.raises(ValidationError, match="DISCORD_ADMIN_USER_IDS"):
        Settings(**base, dashboard_enabled=False, telegram_enabled=False)
    assert delivery_allows(SimpleNamespace(mobile_delivery="emergency"), "mobile", "emergency")
    assert not delivery_allows(SimpleNamespace(push_delivery="commands"), "push", "alerts")

"""Exact log level filter for the dashboard API."""

from __future__ import annotations

from pi_guard.logs import RECENT, _DampenSslClosed


def test_dampen_ssl_closed_rate_limits() -> None:
    filt = _DampenSslClosed(every=60.0)

    class Rec:
        def getMessage(self) -> str:
            return "SSL connection is closed"

    assert filt.filter(Rec()) is True
    assert filt.filter(Rec()) is False


def test_recent_records_shape() -> None:
    assert hasattr(RECENT, "records")

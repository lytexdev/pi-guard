from __future__ import annotations

import re

# Telegram tokens can appear in URLs logged by HTTP libraries.
_TOKEN_RE = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{30,}")


def redact(text: str, secrets: list[str] | None = None) -> str:
    for secret in secrets or []:
        text = text.replace(secret, "[redacted]")
    text = _TOKEN_RE.sub("[redacted-token]", text)
    return text


def mask_phone(number: str) -> str:
    return number[:4] + "…" + number[-2:] if len(number) > 6 else "…"


def mask_sender(sender: str) -> str:
    """`signal:+4915112345678` -> `signal:+491…78`; other IDs are not personal data."""
    provider, _, user = sender.partition(":")
    return f"{provider}:{mask_phone(user)}" if user.startswith("+") else sender

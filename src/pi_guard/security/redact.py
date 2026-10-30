from __future__ import annotations

import logging
import re

# Telegram tokens can appear in URLs logged by HTTP libraries.
_TOKEN_RE = re.compile(r"\d{5,15}:[A-Za-z0-9_-]{30,}")
_PHONE_RE = re.compile(r"\+\d{6,15}")


def redact(text: str, secrets: list[str] | None = None) -> str:
    for secret in secrets or []:
        text = text.replace(secret, "[redacted]")
    text = _TOKEN_RE.sub("[redacted-token]", text)
    return text


def mask_phone(number: str) -> str:
    return number[:4] + "…" + number[-2:] if len(number) > 6 else "…"


class RedactingFilter(logging.Filter):
    """Scrubs configured secrets and anything token-shaped from every log record."""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message, self._secrets)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def mask_phones(text: str) -> str:
    return _PHONE_RE.sub(lambda m: mask_phone(m.group(0)), text)

"""Logging setup: human-readable or JSON lines on stderr (journald), optional rotating file."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path

from pi_guard.security.redact import redact

_NOISY = ("httpx", "httpcore", "telegram.ext", "apscheduler", "aiomqtt")


class RedactingFormatter(logging.Formatter):
    """Redacts the final text, so secrets inside tracebacks are caught as well."""

    def __init__(self, inner: logging.Formatter, secrets: list[str]) -> None:
        super().__init__()
        self._inner = inner
        self._secrets = [s for s in secrets if s]

    def format(self, record: logging.LogRecord) -> str:
        return redact(self._inner.format(record), self._secrets)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(
    level: str, fmt: str = "text", log_file: Path | None = None, secrets: list[str] | None = None
) -> None:
    formatter: logging.Formatter
    if fmt == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(levelname)-7s %(name)s: %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3)
        )
    for handler in handlers:
        handler.setFormatter(RedactingFormatter(formatter, secrets or []))
    logging.basicConfig(level=level, handlers=handlers, force=True)
    for name in _NOISY:
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLevelName(level)))

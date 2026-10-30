"""Logging setup: human-readable or JSON lines on stderr (journald), optional rotating file."""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path

from pi_guard.security.redact import RedactingFilter

_NOISY = ("httpx", "httpcore", "telegram.ext", "apscheduler", "aiomqtt")


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
    redactor = RedactingFilter(secrets or [])
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redactor)
    logging.basicConfig(level=level, handlers=handlers, force=True)
    for name in _NOISY:
        logging.getLogger(name).setLevel(max(logging.WARNING, logging.getLevelName(level)))

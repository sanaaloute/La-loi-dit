"""Structured (JSON-ish) logging to stdout, stdlib logging only.

One line per record, key=value pairs with JSON quoting for safety, so logs
stay grep-able locally and parseable by log shippers without extra deps.
"""

from __future__ import annotations

import json
import logging
import sys

from backend.core.config import Settings

_CONFIGURED = False


class _StructuredFormatter(logging.Formatter):
    """Formats records as a single line of JSON-ish key=value pairs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__ and key not in (
                "message",
                "asctime",
                "taskName",
            ):
                try:
                    json.dumps(value)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = str(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: Settings) -> None:
    """Configure root logging once; idempotent across repeated calls/tests."""
    global _CONFIGURED
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_StructuredFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Tame noisy third parties.
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    _CONFIGURED = True
    logging.getLogger(__name__).info("logging configured", extra={"log_level": settings.log_level})

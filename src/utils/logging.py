"""Structured logging configuration."""

import logging
import json
import sys
from typing import Any


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add context fields if present
        if hasattr(record, "extra_fields"):
            log_obj.update(record.extra_fields)

        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the application."""
    logger = logging.getLogger()
    logger.setLevel(level)

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Add structured handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)


def get_logger(name: str) -> logging.LoggerAdapter:
    """Get a logger with support for extra fields."""

    class ExtraAdapter(logging.LoggerAdapter):
        def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict]:
            extra = kwargs.pop("extra", {})
            self.extra = {"extra_fields": extra}
            return msg, kwargs

    return ExtraAdapter(logging.getLogger(name), {})

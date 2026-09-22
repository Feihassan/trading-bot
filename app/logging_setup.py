"""
Structured logging setup.

Design decisions:
- Console gets a human-readable format for interactive development.
- File gets JSON-lines output so trade decisions/errors can be queried,
  aggregated, or shipped to a log system later without reparsing text.
- A rotating file handler bounds disk usage; logs are never silently lost
  (rotation keeps N backups) but also never grow unbounded.
- `log_decision(...)` is the canonical way strategy/risk code should log a
  trade decision, so every decision is explainable per spec section 25.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(log_dir: str = "./logs", level: str = "INFO") -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s")
    )
    root.addHandler(console_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        Path(log_dir) / "trading_bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    error_handler = logging.handlers.RotatingFileHandler(
        Path(log_dir) / "errors.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(JsonFormatter())
    root.addHandler(error_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_decision(logger: logging.Logger, symbol: str, decision: str, **fields: Any) -> None:
    """
    Log a trade/signal decision with full context, so every decision the
    bot makes is reconstructable later from logs alone (spec section 25).
    """
    logger.info(
        f"{symbol} decision={decision}",
        extra={"extra_fields": {"symbol": symbol, "decision": decision, **fields}},
    )

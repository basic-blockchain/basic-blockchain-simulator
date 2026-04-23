from __future__ import annotations

import json
import logging
import time


def _current_request_id() -> str | None:
    try:
        from flask import g
        return getattr(g, "request_id", None)
    except RuntimeError:
        return None


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        rid = _current_request_id()
        if rid:
            payload["request_id"] = rid
        if hasattr(record, "data"):
            payload["data"] = record.data
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("blockchain")
    if logger.handlers:
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(_JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


logger = configure_logging()

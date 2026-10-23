"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)

_SECRET_KEYS = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential)", re.I)
_SECRET_VALUES = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # OpenAI / Anthropic style keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"),
]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEYS.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        out = value
        for rx in _SECRET_VALUES:
            out = rx.sub("***", out)
        return out
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        cid = correlation_id.get()
        if cid:
            payload["correlation_id"] = cid
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = redact(extra)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel("WARNING")

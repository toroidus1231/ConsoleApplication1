"""Structured JSON logging with trace/request IDs.

Emits one JSON line per record to stdout. Each record carries a
trace_id (set on entry to a request via contextvars) so log lines from
the same request can be correlated downstream.

Usage:

    from src.observability.structured_logging import configure, get_logger, set_trace_id
    configure(level="INFO")
    log = get_logger(__name__)
    set_trace_id(request_id)
    log.info("test_started", device_id=d, test_name=t, duration_s=12.4)

The output is plain JSON, one record per line, suitable for Loki /
Cloud Logging / Splunk ingestion. No external dependencies.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
from typing import Any

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp_ns": time.time_ns(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        tid = _trace_id_var.get()
        if tid:
            payload["trace_id"] = tid
        # Any structured fields attached via logger.info(msg, extra={...})
        # come through in record.__dict__; copy known-safe ones.
        for k, v in record.__dict__.items():
            if k in payload or k in _RESERVED:
                continue
            if k.startswith("_"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName",
}


def configure(*, level: str = "INFO", stream=None) -> None:
    """Set up the root logger to emit JSON to `stream` (default stdout).
    Idempotent — call once at startup."""
    target = stream if stream is not None else sys.stdout
    handler = logging.StreamHandler(target)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    # Replace any existing handlers so re-configure() is clean.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> "StructuredLogger":
    return StructuredLogger(logging.getLogger(name))


def set_trace_id(trace_id: str | None) -> contextvars.Token:
    return _trace_id_var.set(trace_id)


def clear_trace_id(token: contextvars.Token) -> None:
    _trace_id_var.reset(token)


class StructuredLogger:
    """Thin wrapper that lets you call log.info("event_name", **fields)."""

    def __init__(self, logger: logging.Logger):
        self._log = logger

    def _emit(self, level: int, message: str, **fields: Any) -> None:
        self._log.log(level, message, extra=fields)

    def debug(self, message: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, message, **fields)

    def info(self, message: str, **fields: Any) -> None:
        self._emit(logging.INFO, message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit(logging.WARNING, message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit(logging.ERROR, message, **fields)

    def exception(self, message: str, **fields: Any) -> None:
        self._log.exception(message, extra=fields)

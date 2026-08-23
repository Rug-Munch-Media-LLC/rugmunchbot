# VENDORED 2026-08-23 from rmi-backend/app/core/logging_config.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RMI Backend — Standard-library JSON logging configuration.

Provides a ``JsonFormatter`` for stdlib logging (used by worker.py and
as a reliable fallback when structlog is unavailable).  Also exposes
``RateLimitedLogger`` for high-frequency DEBUG paths that need sampling.

Usage:
    import logging
    from rugmunchbot._vendor.logging_config import JsonFormatter, setup_stdlib_logging

    logger = setup_stdlib_logging(__name__)
    logger.info("scan_complete", extra={"tokens": 42})

    # Or manually:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any

# ── Sampling ──────────────────────────────────────────────────────────

_SAMPLE_RATES: dict[str, float] = {}  # logger_name -> fraction (0.0 - 1.0)


def set_sample_rate(name: str, rate: float) -> None:
    """Configure sampling *rate* for *name* (exact or prefix match).

    ``rate=0.01`` means 1 % of log entries pass through.
    """
    _SAMPLE_RATES[name] = max(0.0, min(1.0, rate))


def _should_sample(name: str) -> bool:
    """Return ``True`` if this log entry should *pass* the sampler."""
    rate = _SAMPLE_RATES.get(name)
    if rate is None:
        return True  # not configured → pass through
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    # Deterministic sampling based on a hash of name + time window
    window = int(time.time()) // 60  # 1-minute window
    h = hash(f"{name}:{window}") & 0xFFFF
    return h < int(rate * 65536)


# ── JSON Formatter ────────────────────────────────────────────────────

_TRACE_ID_ATTR = "trace_id"
_REQUEST_ID_ATTR = "request_id"


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects.

    Adds the following keys to every entry:
      ``timestamp``, ``level``, ``logger``, ``message``, ``module``,
      ``function``, ``line``, ``pid``.

    If the record carries ``request_id`` or ``trace_id`` in its ``extra``
    dict they are included.  Exception info is rendered under ``exception``.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "pid": record.process,
        }

        # Structured extra fields (keys not in default LogRecord)
        if hasattr(record, _REQUEST_ID_ATTR):
            log_entry[_REQUEST_ID_ATTR] = getattr(record, _REQUEST_ID_ATTR)
        if hasattr(record, _TRACE_ID_ATTR):
            log_entry[_TRACE_ID_ATTR] = getattr(record, _TRACE_ID_ATTR)

        # User-supplied extra keys (skip stdlib reserved attrs).
        # Stdlib LogRecord stores extra kwargs as attributes directly,
        # so we diff the record dict against known-reserved names.
        _reserved = {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",  # Python ≥3.12
        }
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if not k.startswith("_")
            and k not in _reserved
            and k not in (_REQUEST_ID_ATTR, _TRACE_ID_ATTR)  # handled above
        }
        if extras:
            log_entry["extra"] = extras

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        if record.stack_info:
            log_entry["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(log_entry, default=str, ensure_ascii=False)


# ── Stdlib factory ────────────────────────────────────────────────────


def create_json_handler(level: int | str = logging.INFO) -> logging.Handler:
    """Return a StreamHandler with ``JsonFormatter`` set."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(JsonFormatter())
    return handler


def setup_stdlib_logging(
    name: str | None = None,
    level: str = "INFO",
    *,
    propagate: bool = True,
) -> logging.Logger:
    """Configure a stdlib logger that emits JSON.

    If *name* is ``None``, configures the root logger instead.
    """
    logger = logging.getLogger(name) if name else logging.getLogger()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove pre-existing handlers (avoids duplicate lines)
    for h in list(logger.handlers):
        logger.removeHandler(h)

    logger.addHandler(create_json_handler())
    logger.propagate = propagate
    return logger


# ── Rate-limited logger wrapper ───────────────────────────────────────


class RateLimitedLogger:
    """Wrapper that samples DEBUG calls to prevent disk fill.

    Usage::

        log = RateLimitedLogger("scanner.mempool", sample_rate=0.01)
        log.debug("transaction_seen", tx_hash=tx)  # only 1% recorded
        log.info("scan_complete")                    # always recorded
    """

    def __init__(self, name: str, sample_rate: float = 0.01) -> None:
        self._logger = logging.getLogger(name)
        self._name = name
        set_sample_rate(name, sample_rate)

    def debug(self, msg: str, **extra: Any) -> None:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        if _should_sample(self._name):
            self._logger.debug(msg, extra=extra)

    def info(self, msg: str, **extra: Any) -> None:
        self._logger.info(msg, extra=extra)

    def warning(self, msg: str, **extra: Any) -> None:
        self._logger.warning(msg, extra=extra)

    def error(self, msg: str, **extra: Any) -> None:
        self._logger.error(msg, extra=extra)

    def exception(self, msg: str, **extra: Any) -> None:
        self._logger.exception(msg, extra=extra)


# ── Request / trace ID helpers ────────────────────────────────────────


def generate_request_id() -> str:
    """Return a short unique request ID (8-char hex)."""
    return uuid.uuid4().hex[:8]


def generate_trace_id() -> str:
    """Return a 16-char hex trace ID for cross-service correlation."""
    return uuid.uuid4().hex[:16]


def inject_request_id(logger: logging.Logger, request_id: str) -> logging.LoggerAdapter[logging.Logger]:
    """Attach *request_id* to extra so ``JsonFormatter`` picks it up.

    This is called automatically by the request-id middleware when the
    ``structlog`` path is active; this function exists for manual use
    (e.g., background workers, Celery tasks).
    """
    return logging.LoggerAdapter(logger, {_REQUEST_ID_ATTR: request_id})


def inject_trace_id(logger: logging.Logger, trace_id: str) -> logging.LoggerAdapter[logging.Logger]:
    """Attach *trace_id* to extra for cross-service correlation."""
    return logging.LoggerAdapter(logger, {_TRACE_ID_ATTR: trace_id})

# VENDORED 2026-08-17 from rmi-backend/app/core/logging.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""RMI Backend — Structured logging with structlog + request ID tracking.

Architecture
────────────
All services use **structlog** with JSON rendering in production and
coloured console output in development.  The ``request_id_middleware``
injects ``X-Request-ID`` into ``structlog.contextvars`` so every log entry
automatically carries the correlation ID — zero boilerplate per module.

Log sampling protects high-frequency DEBUG paths (e.g. mempool polling)
from filling disk.  Configure per-module sample rates via
``LogSampler`` or ``set_sample_rate()``.

Usage
─────
    from rugmunchbot._vendor.logging_shim import get_logger

    logger = get_logger(__name__)
    logger.info("scan_complete", token_count=42, chain="solana")
    logger.error("scan_failed", token_address=addr, exc_info=True)
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, cast

import structlog

# Re-export for convenience
from rugmunchbot._vendor.logging_config import (
    JsonFormatter,
    RateLimitedLogger,
    generate_request_id,
    generate_trace_id,
)

__all__ = [
    "JsonFormatter",
    "LogSampler",
    "RateLimitedLogger",
    "bind_request_id",
    "bind_trace_context",
    "generate_request_id",
    "generate_trace_id",
    "get_logger",
    "quiet_noisy_loggers",
    "set_sample_rate",
    "setup_logging",
]

# ── Public helpers ────────────────────────────────────────────────────


def bind_request_id(request_id: str | None = None) -> str:
    """Inject ``request_id`` into the current structlog context var.

    Every log entry emitted after this call (in the same async context)
    will carry ``request_id``.  Returns the ID so callers can use it
    for response headers.
    """
    if not request_id:
        request_id = generate_request_id()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    return request_id


def bind_trace_context(
    *,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    service: str = "rmi-backend",
) -> str:
    """Inject cross-service trace context into structlog context vars.

    Call this at service entry points (HTTP handler, Celery task, worker
    loop) to enable end-to-end correlation.

    Returns the *trace_id*.
    """
    if not trace_id:
        trace_id = generate_trace_id()
    ctx: dict[str, Any] = {"trace_id": trace_id, "service": service}
    if parent_span_id:
        ctx["parent_span_id"] = parent_span_id
    structlog.contextvars.bind_contextvars(**ctx)
    return trace_id


def reset_context() -> None:
    """Clear all structlog context vars for the current async context.

    Call before processing a new job in a long-lived worker to prevent
    leaking request/trace IDs between jobs.
    """
    structlog.contextvars.clear_contextvars()


def quiet_noisy_loggers() -> None:
    """Suppress chatty third-party loggers so they don't drown signal."""
    for name in (
        "httpx",
        "httpcore",
        "urllib3",
        "urllib3.connectionpool",
        "aiokafka",
        "kafka",
        "redis",
        "asyncio",
        "PIL",
        "faker",
        "chardet",
        "requests",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    # Uvicorn access logs are duplicated by our own middleware
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ── Log sampler ───────────────────────────────────────────────────────

_SAMPLE_RATES: dict[str, float] = {}  # logger name prefix → rate


def set_sample_rate(name: str, rate: float) -> None:
    """Configure structlog sample *rate* (0.0 - 1.0) for *name*.

    Applied to structlog's isEnabledFor via ``LogSampler``, or used
    directly by ``RateLimitedLogger`` (stdlib path).
    """
    _SAMPLE_RATES[name] = max(0.0, min(1.0, rate))


def _sample_check(name: str) -> bool:
    """Return True if this record should pass (not be sampled out)."""
    rate = _SAMPLE_RATES.get(name)
    if rate is None:
        return True  # not configured = pass through
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    # Deterministic 1-minute window so bursts don't dominate
    window = int(time.time()) // 60
    h = hash(f"{name}:{window}") & 0xFFFF
    return h < int(rate * 65536)


class LogSampler:
    """Structlog processor that drops DEBUG entries for sampled loggers.

    Add this *before* ``filter_by_level`` in the processor chain::

        structlog.configure(
            processors=[
                LogSampler(),
                structlog.stdlib.filter_by_level,
                ...
            ],
        )

    Configure rates::

        from rugmunchbot._vendor.logging_shim import set_sample_rate
        set_sample_rate("scanner.mempool", 0.01)   # 1 % of DEBUG kept
        set_sample_rate("watcher.poll",     0.05)   # 5 % of DEBUG kept
    """

    def __call__(
        self,
        logger: logging.Logger,
        method_name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        if method_name == "debug":
            log_name = logger.name
            for prefix, _ in _SAMPLE_RATES.items():
                if log_name.startswith(prefix):
                    if not _sample_check(log_name):
                        raise structlog.DropEvent("sampled_out")
                    break
        return event_dict


# ── Setup ─────────────────────────────────────────────────────────────


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog with JSON output for production, console for dev.

    Call once at process startup (called by ``app.lifespan``).
    """
    is_prod = level.upper() == "INFO"  # JSON in prod, coloured console in debug

    processors: list[Any] = [
        # 1. Sampling — drop sampled DEBUG before anything else
        LogSampler(),
        structlog.stdlib.filter_by_level,
        # 2. Enrichment
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        # 3. Context vars (request_id, trace_id, service, …)
        structlog.contextvars.merge_contextvars,
        # 4. Exception / stack info
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # 5. Rendering
    if is_prod:
        # JSON for production — includes all context vars
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Pretty-print for local dev
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Set root logger level and quieten chatty third-party loggers
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )
    quiet_noisy_loggers()


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger for a module.

    The returned logger automatically includes ``request_id`` and
    ``trace_id`` if they have been bound via ``bind_request_id()`` /
    ``bind_trace_context()``.
    """
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))

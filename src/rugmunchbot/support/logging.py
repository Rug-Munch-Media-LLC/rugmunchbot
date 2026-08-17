"""RugMunchBot standalone logging shim.

First migration step toward rugmunchbot's own repository: provides a
``get_logger(name)`` with the same interface as ``app.core.logging``
(structlog-style bound logger: ``logger.info("msg", key=value)``, JSON
output in production, coloured console in dev) WITHOUT importing
``app.core``.

The structlog processor chain mirrors ``app.core.logging.setup_logging``
so log output is byte-identical.  Configuration is applied lazily on the
first ``get_logger()`` call *only if* structlog is not already configured
(e.g. when the backend's lifespan already ran ``setup_logging``) — the
global structlog config is never clobbered.

Usage::

    from app.domains.telegram.rugmunchbot.support.logging import get_logger

    logger = get_logger(__name__)
    logger.info("scan_complete", token_count=42, chain="solana")
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, cast

import structlog

__all__ = ["get_logger"]

# ── Log sampler (mirror of app.core.logging.LogSampler) ──────────────

_SAMPLE_RATES: dict[str, float] = {}  # logger name prefix → rate


def set_sample_rate(name: str, rate: float) -> None:
    """Configure structlog sample *rate* (0.0 - 1.0) for *name*."""
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
    """Structlog processor that drops DEBUG entries for sampled loggers."""

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


# ── Setup (mirror of app.core.logging.setup_logging) ─────────────────

_configured = False


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog with JSON output for production, console for dev.

    Byte-identical to ``app.core.logging.setup_logging``.  No-op if
    structlog was already configured (e.g. by the backend lifespan).
    """
    global _configured
    _configured = True
    if structlog.is_configured():
        return  # backend already configured with the same chain

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

    # Set root logger level
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )


def _ensure_configured() -> None:
    if _configured:
        return
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structlog logger for a module (same interface as app.core)."""
    _ensure_configured()
    return cast("structlog.stdlib.BoundLogger", structlog.get_logger(name))

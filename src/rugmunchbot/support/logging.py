"""
RugMunchBot standalone logging shim.

Self-contained structlog adapter — the SAME module already used by the live
bot (rmi-backend/app/domains/telegram/rugmunchbot/support/logging.py). It was
built as the first extraction step: same interface as the private
``app.core.logging`` (``logger.info("msg", key=value)``, JSON in production,
coloured console in dev) WITHOUT importing ``app.core``.

This file is the OSS verbatim copy. Port status: PORTABLE (Stage 1).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

import structlog

__all__ = ["get_logger"]

# ── Log sampler (mirror of the private app.core.logging.LogSampler) ────────
_SAMPLE_RATES: dict[str, float] = {}


def set_sample_rate(name: str, rate: float) -> None:
    """Configure structlog sample rate (0.0-1.0) for *name*."""
    _SAMPLE_RATES[name] = max(0.0, min(1.0, rate))


def _sample_check(name: str) -> bool:
    rate = _SAMPLE_RATES.get(name)
    if rate is None:
        return True
    return _hash_sample(name) < rate


def _hash_sample(name: str) -> float:
    return (hash(name) & 0xFFFF) / 0xFFFF


class LogSampler:
    """Drops events for sampled-out logger names (mirror of app.core)."""

    def __call__(
        self,
        _logger: Any,
        _method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> Mapping[str, Any] | str | bytes | bytearray | tuple[Any, ...]:
        name = str(event_dict.get("logger", ""))
        if not _sample_check(name):
            raise structlog.DropEvent("sampled_out")
        return event_dict


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog: JSON for production, console for dev. Idempotent."""
    if structlog.is_configured():
        return
    level_name = os.getenv("LOG_LEVEL", level).upper()
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO))
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.contextvars.merge_contextvars,
            structlog.processors.StackInfoRenderer(),
            LogSampler(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Return a structlog bound logger (mirror of app.core.logging.get_logger).

    Example::

        logger = get_logger("rmi_bot")
        logger.info("scan_complete", token_count=42, chain="solana")
    """
    if name is None:
        name = "rugmunchbot"
    setup_logging()
    logger: Any = structlog.get_logger(name)
    # time-based convenience bound fields are applied lazily by processors
    return logger


# Silence unused-import lints for the cast/time imports kept for parity.
_ = (cast, time, sys)

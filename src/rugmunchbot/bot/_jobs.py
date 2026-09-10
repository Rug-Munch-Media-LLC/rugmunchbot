"""Core APScheduler jobs — metrics heartbeat + channel job wiring."""

from __future__ import annotations

import ctypes
import gc
from contextlib import suppress
from datetime import time as dtime  # noqa: F401 — used by daily_scam_tip if re-enabled
from functools import wraps
from typing import TYPE_CHECKING, Any

from rugmunchbot.alert_worker import deliver_pending_alerts
from rugmunchbot.broadcast_worker import deliver_pending_broadcasts
from rugmunchbot.config import (
    ALERT_CHECK_INTERVAL,
)
from rugmunchbot.handlers.alerts import (
    MONITOR_CHECK_INTERVAL,
    check_alerts,
    check_monitors,
)
from rugmunchbot.observability import (
    record_error,
    record_job,
)
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


if TYPE_CHECKING:
    from telegram.ext import (
        Application,
    )


def _metrics_job(name: str, fn: Any) -> Any:
    """Wrap a scheduled job with run/failure counters + error logging.

    Additive + cheap: counts every invocation, and on an unexpected
    exception increments the error counter, records the last error and
    logs before re-raising so PTB's error handler still sees it.
    """

    @wraps(fn)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        record_job(name)
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            record_error(kind=f"job:{name}", message=str(e))
            logger.error("scheduled_job_failed name=%s err=%s", name, e, exc_info=True)
            raise

    return wrapped


async def heap_trim(context: Any = None) -> None:
    """Run gc.collect + malloc_trim(0) to return fragmented heap to the OS.

    Python's memory allocator (pymalloc + glibc malloc) grows arenas but
    never shrinks them — once the heap expands (scanner caches, news
    ingestion bursts, media processing) the RSS stays high even after
    objects are freed.  This periodic trim reclaims that memory.
    """
    collected = gc.collect()
    with suppress(OSError, AttributeError):
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    if collected:
        logger.debug("heap_trim collected=%d", collected)


def register_core_jobs(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the CORE repeating scheduler jobs on ``app.job_queue``.

    Contract (unit-tested in tests/test_bot_scheduler.py): EXACTLY 5 core
    repeating jobs — check_alerts, check_monitors,
    deliver_pending_broadcasts, deliver_pending_alerts, heap_trim. The daily scam tip
    stays DISABLED (2026-08-12); env-driven channel content jobs are a
    separate concern owned by :func:`register_channel_jobs`. Extracted from
    ``main()`` so the scheduling contract is testable without a Telegram
    connection (``main`` ends in the blocking ``run_polling``).
    """
    job_queue = app.job_queue
    if not job_queue:
        return
    job_queue.run_repeating(
        _metrics_job("check_alerts", check_alerts), interval=ALERT_CHECK_INTERVAL, first=10
    )
    # Stars-paid token monitors: re-scan for tax/ownership/mint/LP deltas
    # and push meaningful changes as DMs (live LP/whale/price events flow
    # through the webhook bridge independently).
    job_queue.run_repeating(
        _metrics_job("check_monitors", check_monitors),
        interval=MONITOR_CHECK_INTERVAL,
        first=120,
    )
    # Admin-panel broadcasts (queued in Redis by the backend API)
    job_queue.run_repeating(
        _metrics_job("deliver_pending_broadcasts", deliver_pending_broadcasts),
        interval=30,
        first=15,
    )
    # Intelligent webhook alerts (queued in Redis by the backend webhook
    # engine → telegram_bridge; whale/price/liquidity/launch push alerts)
    job_queue.run_repeating(
        _metrics_job("deliver_pending_alerts", deliver_pending_alerts), interval=30, first=15
    )
    # Memory-pressure relief: gc.collect + malloc_trim returns fragmented
    # heap pages to the OS. Without this the bot's RSS grows monotonically
    # (Python never shrinks its arenas) and eventually hits MemoryMax.
    job_queue.run_repeating(_metrics_job("heap_trim", heap_trim), interval=1800, first=300)

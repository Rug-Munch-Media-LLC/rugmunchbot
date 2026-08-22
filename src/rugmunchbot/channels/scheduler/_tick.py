from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels import scheduler_state

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


from typing import TYPE_CHECKING

from rugmunchbot.channels.scheduler._core import (  # noqa: F401
    _FIRST_RUN_SECONDS,
    _elapsed_ms,
    _posted_keys,
    logger,
)


def _tick_wrapper(
    job_name: str,
    callback: Callable[[Any], Coroutine[Any, Any, Any]],
    *,
    envelope: bool = False,
) -> Callable[[Any], Coroutine[Any, Any, Any]]:
    """Wrap a JobQueue callback with durability + observability.

    Always records the last-run timestamp and persists scheduler state at
    the end of the tick (fail-open). When ``envelope=True`` (jobs whose
    source modules live outside this file) it also emits the ``channels:tick``
    JSON line with the fields the return value can provide; the in-file jobs
    (news/alerts/alpha) emit their own full-count tick lines instead.
    """

    async def wrapped(ctx: Any) -> Any:
        """Run one scheduled job tick: record start, invoke callback, persist state."""
        started = time.perf_counter()
        scheduler_state.mark_last_run(job_name)
        result: Any = None
        try:
            result = await callback(ctx)
        except Exception as exc:  # a poster must never crash the JobQueue
            logger.error(
                "channels:tick",
                job=job_name,
                items_swept=0,
                items_scored=0,
                items_posted=0,
                items_failed=1,
                latency_ms=_elapsed_ms(started),
                error=str(exc)[:200],
            )
        else:
            if envelope:
                logger.info(
                    "channels:tick",
                    job=job_name,
                    items_swept=0,
                    items_scored=0,
                    items_posted=int(result or 0),
                    items_failed=0,
                    latency_ms=_elapsed_ms(started),
                )
        finally:
            try:
                await scheduler_state.save_state(_posted_keys, job=job_name)
            except Exception:  # durability must never crash the job
                logger.debug("channels:tick_save_failed job=%s", job_name, exc_info=True)
        return result

    return wrapped


async def _load_channel_state(ctx: Any = None) -> None:
    """One-time startup job: hydrate dedupe + last-run from Redis (fail-open)."""
    await scheduler_state.load_state(_posted_keys)

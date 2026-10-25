from __future__ import annotations

import functools
import time
from typing import TYPE_CHECKING, Any

from rugmunchbot import observability
from rugmunchbot.channels import scheduler_state
from rugmunchbot.errors import backoff_seconds, classify, is_retryable

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

from rugmunchbot.channels.scheduler._core import (  # noqa: F401
    _FIRST_RUN_SECONDS,
    _elapsed_ms,
    _posted_keys,
    logger,
)

#: How many times a TRANSIENT job failure is retried before it is only recorded.
MAX_JOB_RETRIES = 2


def _retry_callback(
    job_name: str,
    callback: Callable[[Any], Coroutine[Any, Any, Any]],
    *,
    attempt: int,
    envelope: bool,
) -> Callable[[Any], Coroutine[Any, Any, Any]]:
    """Build a one-shot callback that re-runs *callback* with retry *attempt*."""

    async def _retry(ctx: Any) -> Any:
        return await _tick_wrapper(job_name, callback, envelope=envelope, attempt=attempt)(ctx)

    return _retry


def _schedule_retry(
    ctx: Any,
    job_name: str,
    callback: Callable[[Any], Coroutine[Any, Any, Any]],
    *,
    attempt: int,
    envelope: bool,
) -> bool:
    """Queue a bounded backoff retry for a transient failure. Returns scheduled?"""
    if attempt >= MAX_JOB_RETRIES:
        return False
    job_queue = getattr(ctx, "job_queue", None)
    if job_queue is None:
        return False
    delay = backoff_seconds(attempt)
    try:
        job_queue.run_once(
            _retry_callback(job_name, callback, attempt=attempt + 1, envelope=envelope),
            when=delay,
            name=f"{job_name}:retry{attempt + 1}",
        )
    except Exception:  # scheduling must never crash the tick
        logger.warning("channels:retry_schedule_failed job=%s", job_name, exc_info=True)
        return False
    observability.record_job_retry(job_name)
    return True


def _tick_wrapper(
    job_name: str,
    callback: Callable[[Any], Coroutine[Any, Any, Any]],
    *,
    envelope: bool = False,
    attempt: int = 0,
) -> Callable[[Any], Coroutine[Any, Any, Any]]:
    """Wrap a JobQueue callback with durability + observability + retry policy.

    Always records the run counter, last-run timestamp and persists scheduler
    state at the end of the tick (fail-open). On failure it records the job in
    observability (``rmi_bot_jobs_failed_total`` + ``/healthz.failed_jobs``) —
    a job that has been failing for a day must not read "ok" — then retries
    only if the taxonomy says the error is transient. When ``envelope=True``
    (jobs whose source modules live outside this file) it also emits the
    ``channels:tick`` JSON line; the in-file jobs emit their own full-count
    lines instead.
    """

    @functools.wraps(callback)
    async def wrapped(ctx: Any) -> Any:
        """Run one scheduled job tick: record start, invoke callback, persist state."""
        started = time.perf_counter()
        scheduler_state.mark_last_run(job_name)
        observability.record_job(job_name)
        result: Any = None
        try:
            result = await callback(ctx)
        except Exception as exc:  # a poster must never crash the JobQueue
            kind = classify(exc)
            retried = is_retryable(exc) and _schedule_retry(
                ctx, job_name, callback, attempt=attempt, envelope=envelope
            )
            observability.record_job_failure(job_name, f"{type(exc).__name__}: {exc}")
            logger.error(
                "channels:tick",
                job=job_name,
                items_swept=0,
                items_scored=0,
                items_posted=0,
                items_failed=1,
                latency_ms=_elapsed_ms(started),
                error=f"{type(exc).__name__}: {str(exc)[:180]}",
                error_kind=str(kind),
                retry_scheduled=retried,
                attempt=attempt,
            )
        else:
            if envelope:
                # 2026-09-01: some channel jobs return a dict payload
                # (e.g. {"posted": n, ...}) instead of a bare count —
                # int(dict) raised TypeError and crashed the JobQueue run.
                # Accept both shapes; dicts may carry an explicit count key.
                if isinstance(result, dict):
                    count = result.get("posted", result.get("items_posted", 0))
                    try:
                        count = int(count or 0)
                    except (TypeError, ValueError):
                        count = 0
                else:
                    try:
                        count = int(result or 0)
                    except (TypeError, ValueError):
                        count = 0
                logger.info(
                    "channels:tick",
                    job=job_name,
                    items_swept=0,
                    items_scored=0,
                    items_posted=count,
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

"""scheduler_state — the process-wide singleton and its accessors."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from rugmunchbot.support.logging import get_logger

from ._state import SchedulerState
from ._sync import reset_sync

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = get_logger("rmi_bot.channels.state")

_state = SchedulerState()


def get_state() -> SchedulerState:
    """Return the module-level durable-state singleton."""
    return _state


def reset_state() -> None:
    """Reset the durable-state singleton (tests / env reload)."""
    _state.reset()
    reset_sync()


def is_available() -> bool:
    """True when the durable layer is usable right now (fail-open)."""
    return _state.is_available()


def mark_last_run(job: str) -> None:
    """Record a job tick start on the singleton (see SchedulerState.mark_last_run)."""
    _state.mark_last_run(job)


def last_run_iso(job: str) -> str:
    """Return the last tick start for ``job`` as an ISO string on the singleton."""
    return _state.last_run_iso(job)


def spawn_persist(channel: str, key: str, extra: tuple[str, ...] = ()) -> None:
    """Schedule a best-effort durable ``sadd`` without blocking the caller.

    Called synchronously from ``scheduler._remember`` on the hot path; the
    Redis round-trip runs in a background task and never raises.
    """
    _spawn(_state.remember(channel, key, extra))


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    """Best-effort background task; swallows every error. Never blocks."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()  # no loop: close explicitly, silence never-awaited warn
        return
    task = loop.create_task(coro)
    task.add_done_callback(_consume_task_result)


def _consume_task_result(task: asyncio.Task[None]) -> None:
    try:
        exc = task.exception()
        if exc is not None:
            logger.debug("channels:state_task_failed err=%s", str(exc)[:120])
    except (asyncio.CancelledError, Exception) as exc:  # task already finished/cancelled
        logger.debug("channels:state_task_consume_failed err=%s", str(exc)[:120])

"""Durable scheduler state for the JobQueue channel jobs (Redis-backed).

The channel scheduler (``channels/scheduler.py``) runs on
python-telegram-bot's in-memory JobQueue. A bot restart drops the queue's
dedupe state, which has already caused duplicate posts once (the JWT
restart). This module adds a Redis durability layer that survives restarts:

* **Posted-key dedupe** — the in-memory ``_posted_keys`` dict (the
  ``_remember`` sets, incl. the ``tgnews:`` prefix and the
  ``personal``/``alpha:*`` prefixes) is mirrored to per-channel Redis SETs
  (``bot:channels:posted:<channel>``, TTL 72h). On startup
  (:func:`load_state`) the sets hydrate the in-memory dedupe so a restart
  resumes cleanly instead of re-posting. On every tick
  (:func:`save_state`) the in-memory snapshot + last-run timestamps are
  flushed back. Additionally :func:`spawn_persist` records each *new* key
  at the moment ``_remember`` first sees it (fire-and-forget), so the
  crash window between "posted to Telegram" and "next tick's save" is
  milliseconds, not an interval.

* **Last-run timestamps** — ``bot:channels:last_run`` (Redis HASH,
  TTL 30d) maps job name → ISO-8601 UTC timestamp so operators can see
  cadence and detect a job that silently stopped after a restart.

* **scam_watch seen-set** — already durable: ``channels/scam_watch.py``
  persists every seen address to the Redis SET ``bot:scam_watch:seen``
  (the in-memory ``_SEEN_CACHE`` is only a bounded fast path). No change
  needed there.

Fail-open by contract: every Redis call is best-effort and never raises.
When Redis is unreachable the state layer degrades to the pre-existing
in-memory behavior (the scheduler keeps running with in-memory dedupe
only) and re-probes periodically so it self-heals when Redis returns.

The scheduler passes its own ``_posted_keys`` dict in as the *seed /
snapshot* — this module does not own a second copy, so there is exactly
one source of truth for the running process.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.redis import get_redis_async
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Coroutine

logger = get_logger("rmi_bot.channels.state")

#: Redis key prefixes (shared namespace with scam_watch's bot:scam_watch:*).
_POSTED_SET_PREFIX = "bot:channels:posted:"
_LAST_RUN_HASH_KEY = "bot:channels:last_run"

#: Posted-key sets are bounded the same way the in-memory dedupe is
#: (``scheduler._MAX_POSTED``); only the most recent window is persisted.
_POSTED_MAX = 500


#: How long posted-key entries survive in Redis (72h ≫ the longest interval
#: of 4h, so nothing re-posts across a restart within that window).
def _env_ttl(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(300, int(raw.strip()))
    except (TypeError, ValueError):
        return default


_DEFAULT_TTL = _env_ttl("RMI_CHANNELS_STATE_TTL_SECONDS", 72 * 3600)
_LAST_RUN_TTL = 30 * 24 * 3600

#: Re-probe Redis after a failure no sooner than this (fail-open + self-heal).
_RETRY_AFTER_SECONDS = 60.0


class SchedulerState:
    """Redis-backed mirror of the scheduler's dedupe + last-run state.

    The class is intentionally decoupled from ``scheduler.py`` — callers
    pass the in-memory posted-key dict in as seed/snapshot. All methods are
    fail-open (never raise); ``get_redis_async()`` returning ``None`` or a
    connection error simply disables the durable layer for this process.
    """

    def __init__(self) -> None:
        self._redis: Any | None = None
        self._redis_down = False
        self._next_probe: float = 0.0
        self._last_run: dict[str, str] = {}

    # ── Redis access (fail-open, self-healing) ───────────────────────────

    def _acquire(self) -> Any | None:
        """Return the async Redis client, or ``None`` (down / suppressed)."""
        if self._redis_down:
            if time.monotonic() < self._next_probe:
                return None
            self._redis_down = False  # allow a re-probe
        if self._redis is None:
            try:
                self._redis = get_redis_async()
            except Exception as exc:  # fail-open by contract
                logger.debug("channels:state_acquire_failed err=%s", str(exc)[:120])
                self._redis = None
            if self._redis is None:
                self._mark_down()
        return self._redis

    def _mark_down(self) -> None:
        self._redis = None
        self._redis_down = True
        self._next_probe = time.monotonic() + _RETRY_AFTER_SECONDS

    def is_available(self) -> bool:
        """True when the durable layer can be used right now."""
        return self._acquire() is not None

    # ── Last-run timestamps ──────────────────────────────────────────────

    def mark_last_run(self, job: str, when: datetime | None = None) -> None:
        """Record a job tick start (in-memory; persisted by :func:`save_state`)."""
        self._last_run[job] = (when or datetime.now(UTC)).isoformat()

    def last_run(self, job: str) -> datetime | None:
        """Return the last tick start for ``job`` as a datetime (None if never)."""
        raw = self._last_run.get(job)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def last_run_iso(self, job: str) -> str:
        """Return the last tick start for ``job`` as an ISO string (empty if never)."""
        return self._last_run.get(job, "")

    # ── Load / save ──────────────────────────────────────────────────────

    async def load(self, seed: dict[str, set[str]]) -> bool:
        """Hydrate ``seed`` (the scheduler's ``_posted_keys``) from Redis.

        Idempotent; call once at startup. Replaces per-channel sets with the
        durable window (bounded to ``_POSTED_MAX``) and restores the
        last-run hash. Fail-open → ``False`` (in-memory state unchanged).
        """
        r = self._acquire()
        if r is None:
            logger.info("channels:state_load_skipped_no_redis")
            return False
        try:
            loaded = 0
            async for raw_key in r.scan_iter(match=f"{_POSTED_SET_PREFIX}*", count=100):
                channel = _to_str(raw_key)[len(_POSTED_SET_PREFIX) :]
                if not channel:
                    continue
                members = await r.smembers(raw_key)
                if not members:
                    continue
                decoded = {_to_str(m) for m in members}
                seed[channel] = set(list(decoded)[:_POSTED_MAX])
                loaded += len(seed[channel])
            raw_last = await r.hgetall(_LAST_RUN_HASH_KEY)
            self._last_run = {_to_str(k): _to_str(v) for k, v in raw_last.items()}
            logger.info(
                "channels:state_loaded posted_keys=%s last_run=%s",
                loaded,
                len(self._last_run),
            )
            return True
        except Exception as exc:  # fail-open by contract
            logger.warning("channels:state_load_failed err=%s", str(exc)[:200])
            self._mark_down()
            return False

    async def save(self, snapshot: dict[str, set[str]]) -> bool:
        """Flush the posted-key snapshot + last-run hash to Redis.

        Called at the end of every tick (idempotent — sadd is a no-op for
        existing members; TTLs are refreshed). Fail-open → ``False``.
        """
        r = self._acquire()
        if r is None:
            return False
        try:
            pipe = r.pipeline(transaction=False)
            for channel, keys in snapshot.items():
                if not keys:
                    continue
                members = list(keys)[:_POSTED_MAX]
                if not members:
                    continue
                skey = f"{_POSTED_SET_PREFIX}{channel}"
                pipe.sadd(skey, *members)
                pipe.expire(skey, _DEFAULT_TTL)
            if self._last_run:
                pipe.hset(_LAST_RUN_HASH_KEY, mapping=dict(self._last_run))
                pipe.expire(_LAST_RUN_HASH_KEY, _LAST_RUN_TTL)
            await pipe.execute()
            return True
        except Exception as exc:  # fail-open by contract
            logger.debug("channels:state_save_failed err=%s", str(exc)[:120])
            self._mark_down()
            return False

    async def remember(self, channel: str, key: str) -> None:
        """Record one newly-seen key durably (fire-and-forget from ``_remember``).

        Best-effort; failures only disable the durable layer until the next
        re-probe. The in-memory dedupe already recorded the key, so a failed
        persist does NOT cause a duplicate within this process — it only
        widens the restart window.
        """
        r = self._acquire()
        if r is None:
            return
        try:
            skey = f"{_POSTED_SET_PREFIX}{channel}"
            await r.sadd(skey, key)
            await r.expire(skey, _DEFAULT_TTL)
        except Exception as exc:  # fail-open by contract
            self._mark_down()
            logger.debug("channels:state_remember_failed channel=%s err=%s", channel, str(exc)[:120])

    def reset(self) -> None:
        """Clear cached client + state (tests / config reload)."""
        self._redis = None
        self._redis_down = False
        self._next_probe = 0.0
        self._last_run.clear()


def _to_str(value: Any) -> str:
    return value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value)


# ── Module-level singleton + helpers used by scheduler.py ───────────────────

_state = SchedulerState()


def get_state() -> SchedulerState:
    """Return the module-level durable-state singleton."""
    return _state


def reset_state() -> None:
    """Reset the durable-state singleton (tests / env reload)."""
    _state.reset()


def is_available() -> bool:
    """True when the durable layer is usable right now (fail-open)."""
    return _state.is_available()


def mark_last_run(job: str) -> None:
    """Record a job tick start on the singleton (see :meth:`SchedulerState.mark_last_run`)."""
    _state.mark_last_run(job)


def last_run_iso(job: str) -> str:
    """Return the last tick start for ``job`` as an ISO string on the singleton."""
    return _state.last_run_iso(job)


def spawn_persist(channel: str, key: str) -> None:
    """Schedule a best-effort durable ``sadd`` without blocking the caller.

    Called synchronously from ``scheduler._remember`` on the hot path; the
    Redis round-trip runs in a background task and never raises.
    """
    _spawn(_state.remember(channel, key))


async def load_state(seed: dict[str, set[str]]) -> bool:
    """Hydrate the scheduler's dedupe sets + last-run from Redis (startup)."""
    return await _state.load(seed)


async def save_state(snapshot: dict[str, set[str]], job: str | None = None) -> bool:
    """Persist the posted-key snapshot + last-run hash (end of each tick)."""
    if job is not None:
        _state.mark_last_run(job)
    return await _state.save(snapshot)


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    """Best-effort background task; swallows every error. Never blocks."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
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


__all__ = [
    "get_state",
    "is_available",
    "last_run_iso",
    "load_state",
    "mark_last_run",
    "reset_state",
    "save_state",
    "spawn_persist",
]

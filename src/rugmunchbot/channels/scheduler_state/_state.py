"""scheduler_state — the Redis-backed dedupe/last-run state object."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot.support.logging import get_logger

from . import _config

logger = get_logger("rmi_bot.channels.state")


class SchedulerState:
    """Redis-backed mirror of the scheduler's dedupe + last-run state.

    The class is intentionally decoupled from ``scheduler.py`` — callers
    pass the in-memory posted-key dict in as seed/snapshot. All methods are
    fail-open (never raise); ``get_redis_async()`` returning ``None`` or a
    connection error simply disables the durable layer for this process.
    """

    def __init__(self, redis: Any | None = None) -> None:
        self._redis = redis
        self._redis_down = False
        self._next_probe: float = 0.0
        self._last_run: dict[str, str] = {}

    # ── Redis access (fail-open, self-healing) ───────────────────────────

    def _acquire(self) -> Any | None:
        """Return the async Redis client, or ``None`` (down / suppressed)."""
        if self._redis is not None:
            return self._redis
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
        self._next_probe = time.monotonic() + _config.RETRY_AFTER_SECONDS

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

    async def load(self, seed: dict[str, set[str] | dict[str, None]]) -> bool:
        """Hydrate ``seed`` (the scheduler's ``_posted_keys``) from Redis.

        Idempotent; call once at startup. Replaces per-channel sets with the
        durable window (bounded to ``_config.POSTED_MAX``) and restores the
        last-run hash. Fail-open → ``False`` (in-memory state unchanged).
        """
        r = self._acquire()
        if r is None:
            logger.info("channels:state_load_skipped_no_redis")
            return False
        try:
            loaded = 0
            async for raw_key in r.scan_iter(match=f"{_config.POSTED_SET_PREFIX}*", count=100):
                channel = _config.to_str(raw_key)[len(_config.POSTED_SET_PREFIX) :]
                if not channel:
                    continue
                members = await r.smembers(raw_key)
                if not members:
                    continue
                decoded = {_config.to_str(m) for m in members}
                if channel == "__global__":
                    # Ordered dict (insertion order) — matches the
                    # scheduler's ordered-set so eviction keeps the newest.
                    seed[channel] = dict.fromkeys(list(decoded)[:_config.POSTED_MAX])
                else:
                    # Legacy per-channel sets: merge their BARE keys into the
                    # global seed — ``_remember`` only reads ``__global__``,
                    # so per-channel hydration alone was dead weight and a
                    # restart lost those dedupe marks (2026-09-02 fix).
                    g = seed.setdefault("__global__", {})
                    for k in list(decoded)[:_config.POSTED_MAX]:
                        g[k.split(":", 1)[-1] if ":" in k else k] = None
                loaded += len(decoded)
            raw_last = await r.hgetall(_config.LAST_RUN_HASH_KEY)
            self._last_run = {_config.to_str(k): _config.to_str(v) for k, v in raw_last.items()}
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

    async def save(self, snapshot: dict[str, set[str] | dict[str, None]]) -> bool:
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
                members = list(keys)[:_config.POSTED_MAX]
                if not members:
                    continue
                skey = f"{_config.POSTED_SET_PREFIX}{channel}"
                pipe.sadd(skey, *members)
                pipe.expire(skey, _config.DEFAULT_TTL)
            if self._last_run:
                pipe.hset(_config.LAST_RUN_HASH_KEY, mapping=dict(self._last_run))
                pipe.expire(_config.LAST_RUN_HASH_KEY, _config.LAST_RUN_TTL)
            await pipe.execute()
            return True
        except Exception as exc:  # fail-open by contract
            logger.debug("channels:state_save_failed err=%s", str(exc)[:120])
            self._mark_down()
            return False

    async def remember(self, channel: str, key: str, extra: tuple[str, ...] = ()) -> None:
        """Record one newly-seen key durably (fire-and-forget from ``_remember``).

        Best-effort; failures only disable the durable layer until the next
        re-probe. The in-memory dedupe already recorded the key, so a failed
        persist does NOT cause a duplicate within this process — it only
        widens the restart window.

        2026-09-02 dedup fix: the full ``key`` still lands in the
        per-channel set, AND the bare key (lane prefix stripped) plus any
        ``extra`` keys (title hashes) land in the GLOBAL durable set
        (``bot:channels:posted:__global__``) — the set :func:`has_posted`
        checks on every ``_remember`` call.
        """
        r = self._acquire()
        if r is None:
            return
        try:
            bare = key.split(":", 1)[-1] if ":" in key else key
            global_members = {bare, *extra}
            pipe = r.pipeline(transaction=False)
            pipe.sadd(f"{_config.POSTED_SET_PREFIX}{channel}", key)
            pipe.expire(f"{_config.POSTED_SET_PREFIX}{channel}", _config.DEFAULT_TTL)
            pipe.sadd(_config.GLOBAL_POSTED_SET, *global_members)
            pipe.expire(_config.GLOBAL_POSTED_SET, _config.DEFAULT_TTL)
            await pipe.execute()
        except Exception as exc:  # fail-open by contract
            self._mark_down()
            logger.debug(
                "channels:state_remember_failed channel=%s err=%s", channel, str(exc)[:120]
            )

    def reset(self) -> None:
        """Clear cached client + state (tests / config reload)."""
        self._redis = None
        self._redis_down = False
        self._next_probe = 0.0
        self._last_run.clear()

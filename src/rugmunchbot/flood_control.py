#!/usr/bin/env python3
"""
RugMunchBot — Per-user flood control (per-second + per-minute).

Protects the AI chat surface from message floods:
  - per-second: max 3 messages per user
  - per-minute: max 30 messages per user (v6: raised from 20)

Storage:
  1. Redis (preferred) — one sliding-window ZSET per user, auto-expiring.
     Config: REDIS_URL (preferred) or REDIS_HOST / REDIS_PORT /
     REDIS_PASSWORD / BOT_FLOOD_REDIS_DB.
  2. SQLite (fallback) — timestamp rows in the bot DB (bot_users.db) with
     periodic cleanup of events older than the 60s window.

How the counters work (testable contract):
  check_flood(user_id) -> (allowed, retry_after_seconds, receipt)
    - Checks AND counts one message atomically (sliding window).
    - Returns receipt=None when allowed but nothing needs refunding.
  refund_flood(user_id, receipt)
    - Undoes the count. Used by the message handler for tier-3 redirects
      (catch-all help text) which are NOT counted against the flood limit.
    - /start and /help are CommandHandler commands and never reach the
      free-text message handler, so they are never counted at all.

Example:
  allowed, retry_after, receipt = await check_flood(telegram_user_id)
  if not allowed:
      await update.message.reply_text(f"Slow down! Wait {retry_after}s.")
      return
  ... run AI chat tiers ...
  if tier_3_redirect:
      await refund_flood(telegram_user_id, receipt)
"""

from __future__ import annotations

import math
import os
import time
import uuid
from typing import Any

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.states import is_owner
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")

# ══════════════════════════════════════════════
# LIMITS
# ══════════════════════════════════════════════
PER_SECOND_LIMIT = 3  # max 3 messages per second per user
PER_MINUTE_LIMIT = 30  # max 30 messages per minute per user (v6: raised from 20)
WINDOW_SECONDS = 60  # retention window for both counters (sliding)
REDIS_KEY_PREFIX = "rmi:bot:flood:"
REDIS_TTL = 120  # expire a user's ZSET shortly after the window passes

# After a Redis failure, back off retries for this long (mirrors backend
# ChatRateLimiter behaviour — fail open to SQLite, never crash the bot).
_REDIS_RETRY_BACKOFF = 30.0


def _redis_url() -> str:
    """Build a Redis URL: REDIS_URL wins, else host/port/password/db parts."""
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        return url
    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", "6379"))
    password = os.getenv("REDIS_PASSWORD", "") or None
    db_num = int(os.getenv("BOT_FLOOD_REDIS_DB", "7"))
    if password:
        return f"redis://:{password}@{host}:{port}/{db_num}"
    return f"redis://{host}:{port}/{db_num}"


class FloodController:
    """Per-user flood control: max 3 msgs/sec and 20 msgs/min (sliding window).

    Redis-backed with a SQLite fallback so the bot never crashes when Redis
    is unreachable. Each counted message returns a ``receipt`` that can be
    used to undo the count (tier-3 redirects are not counted).
    """

    def __init__(
        self,
        per_second: int = PER_SECOND_LIMIT,
        per_minute: int = PER_MINUTE_LIMIT,
        redis_enabled: bool = True,
    ) -> None:
        self.per_second = per_second
        self.per_minute = per_minute
        # redis_enabled=False forces the SQLite fallback (used by tests and
        # deployments where the bot process has no Redis access).
        self._redis_enabled = redis_enabled
        self._redis: Any = None
        self._redis_failed_at: float = 0.0

    # ── Redis bootstrap (lazy, degrade to SQLite on failure) ─────────────

    async def _ensure_redis(self) -> Any | None:
        if not self._redis_enabled:
            return None
        if self._redis is not None:
            return self._redis
        if self._redis_failed_at and time.monotonic() - self._redis_failed_at < _REDIS_RETRY_BACKOFF:
            return None
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(_redis_url(), socket_connect_timeout=2, decode_responses=True)
            await client.ping()
            self._redis = client
            return client
        except Exception:
            self._redis = None
            self._redis_failed_at = time.monotonic()
            return None

    @staticmethod
    def _key(user_id: int) -> str:
        return f"{REDIS_KEY_PREFIX}{user_id}"

    # ── SQLite schema (created lazily so import order doesn't matter) ─────

    @staticmethod
    def _ensure_table(conn: Any) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS flood_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_flood_user_ts ON flood_events(user_id, ts)")
        conn.commit()

    # ── Public API ───────────────────────────────────────────────────────

    async def check_and_count(self, user_id: int) -> tuple[bool, int, str | None]:
        """Check + count one message.

        Returns ``(allowed, retry_after_seconds, receipt)``. ``receipt`` is
        ``None`` for owners and on rejection; pass it to ``refund()`` when
        the message turns out to be a tier-3 redirect.
        """
        if is_owner(user_id):
            return True, 0, None
        redis = await self._ensure_redis()
        if redis is not None:
            try:
                return await self._check_count_redis(redis, user_id)
            except Exception:
                logger.debug("flood redis failure, falling back to sqlite", exc_info=True)
                self._redis = None
                self._redis_failed_at = time.monotonic()
        return await self._check_count_sqlite(user_id)

    async def refund(self, user_id: int, receipt: str | None) -> None:
        """Undo a count (tier-3 redirects are not counted against flood limits)."""
        if not receipt or is_owner(user_id):
            return
        redis = await self._ensure_redis()
        if redis is not None:
            try:
                await redis.zrem(self._key(user_id), receipt)
                return
            except Exception:
                logger.debug("flood refund redis failure, falling back to sqlite", exc_info=True)
                self._redis = None
                self._redis_failed_at = time.monotonic()
        await self._refund_sqlite(user_id, receipt)

    # ── Redis sliding-window ZSET ────────────────────────────────────────

    async def _check_count_redis(self, redis: Any, user_id: int) -> tuple[bool, int, str | None]:
        now = time.time()
        key = self._key(user_id)
        # Drop events outside the retention window, then read both counters.
        await redis.zremrangebyscore(key, "-inf", now - WINDOW_SECONDS)
        minute_count = await redis.zcard(key)
        second_count = await redis.zcount(key, now - 1, now)

        if second_count >= self.per_second or minute_count >= self.per_minute:
            oldest = await redis.zrange(key, 0, 0, withscores=True)
            oldest_ts = oldest[0][1] if oldest else now
            return False, self._retry_after(oldest_ts, now), None

        receipt = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
        await redis.zadd(key, {receipt: now})
        await redis.expire(key, REDIS_TTL)
        return True, 0, receipt

    # ── SQLite fallback (timestamp rows + cleanup) ───────────────────────

    async def _check_count_sqlite(self, user_id: int) -> tuple[bool, int, str | None]:
        now = time.time()
        conn = db.get_db()
        try:
            self._ensure_table(conn)
            # Timestamp-based cleanup: drop anything older than the window.
            conn.execute("DELETE FROM flood_events WHERE ts < ?", (now - WINDOW_SECONDS,))
            second_count = conn.execute(
                "SELECT COUNT(*) FROM flood_events WHERE user_id = ? AND ts >= ?",
                (user_id, now - 1),
            ).fetchone()[0]
            minute_count = conn.execute(
                "SELECT COUNT(*) FROM flood_events WHERE user_id = ? AND ts >= ?",
                (user_id, now - WINDOW_SECONDS),
            ).fetchone()[0]

            if second_count >= self.per_second or minute_count >= self.per_minute:
                row = conn.execute(
                    "SELECT MIN(ts) FROM flood_events WHERE user_id = ? AND ts >= ?",
                    (user_id, now - WINDOW_SECONDS),
                ).fetchone()
                oldest_ts = row[0] if row and row[0] is not None else now
                return False, self._retry_after(oldest_ts, now), None

            cur = conn.execute("INSERT INTO flood_events (user_id, ts) VALUES (?, ?)", (user_id, now))
            conn.commit()
            return True, 0, f"sqlite:{cur.lastrowid}"
        finally:
            conn.close()

    async def _refund_sqlite(self, user_id: int, receipt: str) -> None:
        if not receipt.startswith("sqlite:"):
            return
        try:
            row_id = int(receipt.split(":", 1)[1])
        except (IndexError, ValueError):
            return
        conn = db.get_db()
        try:
            conn.execute("DELETE FROM flood_events WHERE id = ? AND user_id = ?", (row_id, user_id))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _retry_after(oldest_ts: float, now: float) -> int:
        """Seconds until the oldest counted event leaves the window."""
        return max(1, math.ceil(WINDOW_SECONDS - (now - oldest_ts)))


# ── Module-level singleton + helpers ─────────────────────────────────────

_flood_controller: FloodController | None = None


def get_flood_controller() -> FloodController:
    global _flood_controller
    if _flood_controller is None:
        _flood_controller = FloodController()
    return _flood_controller


async def check_flood(user_id: int) -> tuple[bool, int, str | None]:
    """Check + count one AI-surface message.

    Returns ``(allowed, retry_after_seconds, receipt)``. On rejection,
    ``retry_after_seconds`` tells the caller how long to ask the user to wait.
    """
    return await get_flood_controller().check_and_count(user_id)


async def refund_flood(user_id: int, receipt: str | None) -> None:
    """Undo a flood count for tier-3 redirects (they are not counted)."""
    await get_flood_controller().refund(user_id, receipt)

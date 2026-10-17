"""The Daily Rug — once-per-day date guard (idempotent daily job).

The operator requirement is EXACTLY ONE post per day. The scheduler's
``run_daily`` already fires once, but a restart, a manual re-run, a
duplicate tick, or a redeploy mid-window can all re-invoke the job. This
guard makes the job idempotent: the first successful run of a calendar
day claims the day in Redis; every later run the same day no-ops.

Fail-open by contract: a Redis outage degrades to "allowed" so a broken
cache never silently kills the flagship brief. The content-hash ledger
(``telegram_news._ledger``) is the second line of defense against a
byte-identical re-post.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger("rmi_bot.channels.daily_rug.guard")

#: Redis key holding the date the brief last posted (one per calendar day).
_DAILY_RUG_DAY_KEY = "daily_rug:last_post_date"
#: TTL — 3 days; the key self-cleans well after the day it guards.
_DAILY_RUG_DAY_TTL = 86400 * 3


def _today() -> str:
    """UTC calendar date (YYYY-MM-DD) — the guard's day identity."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _client() -> object | None:
    """Lazy sync Redis client (fail-open via None)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        return get_redis()
    except Exception as exc:  # fail-open by contract
        logger.debug("daily_rug:redis_client_failed err=%s", str(exc)[:100])
        return None


def already_posted_today() -> bool:
    """True when the brief has already posted today (fail-open → False)."""
    client = _client()
    if client is None:
        return False
    try:
        raw = client.get(_DAILY_RUG_DAY_KEY)
        if raw is None:
            return False
        value = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        return value == _today()
    except Exception as exc:  # fail-open — never block the brief
        logger.debug("daily_rug:guard_read_failed err=%s", str(exc)[:100])
        return False


def claim_today() -> bool:
    """Atomically claim today's slot. True when this run owns the day.

    Uses ``SET NX`` so two concurrent ticks cannot both win. Fail-open:
    a Redis outage returns True (the run proceeds) — the content-hash
    ledger still blocks a byte-identical duplicate.
    """
    client = _client()
    if client is None:
        return True
    try:
        claimed = client.set(_DAILY_RUG_DAY_KEY, _today(), nx=True, ex=_DAILY_RUG_DAY_TTL)
        return bool(claimed)
    except Exception as exc:  # fail-open — never block the brief
        logger.debug("daily_rug:guard_claim_failed err=%s", str(exc)[:100])
        return True


def release_today() -> None:
    """Release today's claim after a failed run so a retry can post.

    Called when the pipeline aborts BEFORE a successful send — a failed
    LLM call or a blocked quality gate must not burn the day's slot.
    """
    client = _client()
    if client is None:
        return
    try:
        raw = client.get(_DAILY_RUG_DAY_KEY)
        value = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw or "")
        if value == _today():
            client.delete(_DAILY_RUG_DAY_KEY)
    except Exception as exc:  # fail-open
        logger.debug("daily_rug:guard_release_failed err=%s", str(exc)[:100])

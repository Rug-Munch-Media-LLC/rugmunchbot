"""GetXAPI budget tracker — Redis-backed daily call + total spend accounting.

Why this exists
---------------
GetXAPI is a metered API (~$0.001/call). The bot's X/Twitter lanes (search,
user-tweets, trends, mentions) would otherwise burn the $5 envelope with no
visibility. This module is the hard gate: every GetXAPI call is recorded in
Redis (survives restarts), and callers check ``can_afford_call()`` BEFORE
making a request so we never overspend.

Budget doctrine (hard, enforced here)
-------------------------------------
* **Daily call cap** — ``GETXAPI_DAILY_CALL_LIMIT`` (150/day). ``can_afford_call``
  returns False once today's count reaches 150. At 150/day the $5 envelope
  lasts ~30 days.
* **Total spend cap** — ``GETXAPI_TOTAL_BUDGET`` ($5.00). ``can_afford_call``
  returns False once cumulative spend reaches $5.00. This is the hard lifetime
  ceiling — the tracker never lets a call through past it.
* **Early warnings** — WARNING when daily calls > 100 (approaching the daily
  limit), CRITICAL when daily calls > 140 (nearly exhausted), CRITICAL when
  total spend > $4.50 (90% of budget). These fire once per crossing (not every
  call) so logs don't spam.
* **Fail-open** — if Redis is down, ``record_call`` / ``can_afford_call``
  degrade to allowing the call but log a WARNING. A dead Redis must never
  block the bot's X lanes; the cost is a brief window of un-metered calls.

Persistence
-----------
* ``getxapi:calls:{YYYYMMDD}`` — INCR daily counter (TTL 48h so stale keys
  expire).
* ``getxapi:spend`` — INCRBYFLOAT cumulative spend in dollars (no TTL — it is
  the lifetime total).

Thread-safety: the bot runs a single asyncio event loop; Redis INCR /
INCRBYFLOAT are atomic, so concurrent ticks cannot race the counters.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.getxapi_config import (
    GETXAPI_COST_PER_CALL,
    GETXAPI_DAILY_CALL_LIMIT,
    GETXAPI_TOTAL_BUDGET,
)

logger = get_logger("rmi_bot.getxapi_budget")

#: Redis key prefix for the daily call counter.
_CALLS_KEY_PREFIX = "getxapi:calls"
#: Redis key for the cumulative spend (dollars).
_SPEND_KEY = "getxapi:spend"
#: TTL for daily call keys (seconds) — 48h so a stale key never lingers.
_DAILY_KEY_TTL = 172800

#: Warning thresholds (fired once per crossing, not per call).
_WARN_CALLS = 100  # WARNING when daily calls > 100
_CRIT_CALLS = 140  # CRITICAL when daily calls > 140
_CRIT_SPEND = 4.50  # CRITICAL when total spend > $4.50

#: In-memory flags so each threshold logs exactly once per process.
_warned_calls = False
_crit_calls = False
_crit_spend = False


def _today() -> str:
    """Today's date as ``YYYYMMDD`` (UTC) — the daily counter key suffix."""
    return time.strftime("%Y%m%d", time.gmtime())


def _daily_key() -> str:
    return f"{_CALLS_KEY_PREFIX}:{_today()}"


def _redis() -> Any | None:
    """Sync Redis client, or None when Redis is down (fail-open)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        return get_redis()
    except Exception as exc:  # pragma: no cover — import guard, fail-open
        logger.warning("getxapi_budget:redis_unavailable err=%s", str(exc)[:120])
        return None


def record_call(endpoint: str, cost: float | None = None) -> None:
    """Record one GetXAPI call: increment today's counter + total spend.

    ``endpoint`` is a human-readable label (e.g. ``search``, ``user-tweets``)
    used only for logging. ``cost`` defaults to ``GETXAPI_COST_PER_CALL``
    ($0.001). Fail-open: Redis down → log a WARNING and return (the call is
    still allowed; we just can't meter it).
    """
    cost = GETXAPI_COST_PER_CALL if cost is None else float(cost)
    r = _redis()
    if r is None:
        logger.warning("getxapi_budget:record_failed_redis_down endpoint=%s", endpoint)
        return
    try:
        pipe = r.pipeline(transaction=False)
        pipe.incr(_daily_key())
        pipe.expire(_daily_key(), _DAILY_KEY_TTL)
        pipe.incrbyfloat(_SPEND_KEY, cost)
        pipe.execute()
    except Exception as exc:  # fail-open by contract
        logger.warning("getxapi_budget:record_failed endpoint=%s err=%s", endpoint, str(exc)[:120])
        return
    _check_thresholds()
    logger.debug("getxapi_budget:recorded endpoint=%s cost=%.4f", endpoint, cost)


def _read_counters() -> tuple[int, float]:
    """Return ``(daily_calls, total_spend)``. Redis down → ``(0, 0.0)``."""
    r = _redis()
    if r is None:
        return 0, 0.0
    try:
        calls = int(r.get(_daily_key()) or 0)
        spend = float(r.get(_SPEND_KEY) or 0.0)
        return calls, spend
    except Exception as exc:  # fail-open by contract
        logger.warning("getxapi_budget:read_failed err=%s", str(exc)[:120])
        return 0, 0.0


def get_daily_usage() -> dict[str, Any]:
    """Current usage snapshot: calls, spend, budget remaining, calls remaining.

    Returns ``{"calls", "spend", "budget_remaining", "calls_remaining"}``.
    ``calls_remaining`` is the smaller of (daily cap - calls) and
    (budget_remaining / cost_per_call) - the true number of calls we can still
    afford today.
    """
    calls, spend = _read_counters()
    budget_remaining = max(0.0, GETXAPI_TOTAL_BUDGET - spend)
    calls_remaining = max(0, GETXAPI_DAILY_CALL_LIMIT - calls)
    # Dollar-budget calls remaining (floor) — the binding constraint when the
    # spend cap is closer than the daily cap.
    budget_calls = (
        int(budget_remaining // GETXAPI_COST_PER_CALL)
        if GETXAPI_COST_PER_CALL > 0
        else calls_remaining
    )
    calls_remaining = min(calls_remaining, budget_calls)
    return {
        "calls": calls,
        "spend": round(spend, 4),
        "budget_remaining": round(budget_remaining, 4),
        "calls_remaining": calls_remaining,
    }


def can_afford_call() -> bool:
    """True when a GetXAPI call is within budget.

    Returns False when daily calls >= ``GETXAPI_DAILY_CALL_LIMIT`` OR total
    spend >= ``GETXAPI_TOTAL_BUDGET``. Fail-open: Redis down → True (allow the
    call, log a warning).
    """
    calls, spend = _read_counters()
    if calls >= GETXAPI_DAILY_CALL_LIMIT:
        logger.warning(
            "getxapi_budget:blocked_daily_limit calls=%s limit=%s", calls, GETXAPI_DAILY_CALL_LIMIT
        )
        return False
    if spend >= GETXAPI_TOTAL_BUDGET:
        logger.warning(
            "getxapi_budget:blocked_budget spend=%.4f budget=%.2f", spend, GETXAPI_TOTAL_BUDGET
        )
        return False
    return True


def _check_thresholds() -> None:
    """Fire WARNING / CRITICAL logs once per crossing (not per call)."""
    global _warned_calls, _crit_calls, _crit_spend
    calls, spend = _read_counters()
    if calls > _WARN_CALLS and not _warned_calls:
        _warned_calls = True
        logger.warning(
            "getxapi_budget:approaching_daily_limit calls=%s warn_at=%s",
            calls,
            _WARN_CALLS,
        )
    if calls > _CRIT_CALLS and not _crit_calls:
        _crit_calls = True
        logger.critical(
            "getxapi_budget:daily_limit_nearly_exhausted calls=%s crit_at=%s",
            calls,
            _CRIT_CALLS,
        )
    if spend > _CRIT_SPEND and not _crit_spend:
        _crit_spend = True
        logger.critical(
            "getxapi_budget:budget_nearly_exhausted spend=%.4f crit_at=%.2f",
            spend,
            _CRIT_SPEND,
        )


def get_budget_report() -> str:
    """Human-readable budget report for logging / admin visibility."""
    usage = get_daily_usage()
    return (
        f"GetXAPI budget: {usage['calls']}/{GETXAPI_DAILY_CALL_LIMIT} calls today, "
        f"spent ${usage['spend']:.4f} of ${GETXAPI_TOTAL_BUDGET:.2f} "
        f"(remaining ${usage['budget_remaining']:.4f}), "
        f"{usage['calls_remaining']} calls remaining"
    )


def reset_counters() -> None:
    """Clear all counters + threshold flags (test helper)."""
    global _warned_calls, _crit_calls, _crit_spend
    _warned_calls = False
    _crit_calls = False
    _crit_spend = False
    r = _redis()
    if r is None:
        return
    try:
        r.delete(_daily_key(), _SPEND_KEY)
    except Exception:  # pragma: no cover - fail-open
        logger.debug("getxapi_budget:reset_failed")


__all__ = [
    "can_afford_call",
    "get_budget_report",
    "get_daily_usage",
    "record_call",
    "reset_counters",
]

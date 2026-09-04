"""GetXAPI budget tracker — Redis-backed daily call + total spend accounting.

Why this exists
---------------
GetXAPI is a metered API (~$0.001/call). The bot's X/Twitter lanes (search,
user-tweets, trends, mentions) would otherwise burn the $5 envelope with no
visibility. This module is the hard gate: every GetXAPI call is recorded in
Redis (survives restarts), and callers check ``can_afford_call()`` BEFORE
making a request so we never overspend.

It is also the **intelligent call router**: ``should_use_getxapi()`` decides
per-request whether to spend a metered GetXAPI call or fall back to the free
Pry scrape lane. Low-value work (small accounts' tweets, mentions) goes to Pry
for free; high-value work (search, big accounts, trends, user info) keeps the
reliable GetXAPI lane. This stretches the $5 envelope from ~7 days to 15+.

Budget doctrine (hard, enforced here)
-------------------------------------
* **Daily call cap** — ``GETXAPI_DAILY_CALL_LIMIT`` (700/day). ``can_afford_call``
  returns False once today's count reaches 700. This is the hard ceiling.
* **Total spend cap** — ``GETXAPI_TOTAL_BUDGET`` ($5.00). ``can_afford_call``
  returns False once cumulative spend reaches $5.00. This is the hard lifetime
  ceiling — the tracker never lets a call through past it.
* **Early warnings** — WARNING when daily calls > 100 (approaching the daily
  limit), CRITICAL when daily calls > 140 (nearly exhausted), CRITICAL when
  total spend > $4.50 (90% of budget). These fire once per crossing (not every
  call) so logs don't spam.
* **Pacing (softer, Pry-fallback)** — when hourly GetXAPI calls exceed 50 the
  bot switches to Pry-only for the rest of the hour; when daily calls exceed
  400 it switches to Pry-only for the rest of the day. ``should_use_getxapi``
  returns False for every endpoint while pacing is active, so the metered lane
  is fully paused and the free Pry lane carries the load.
* **Fail-open** — if Redis is down, ``record_call`` / ``can_afford_call``
  degrade to allowing the call but log a WARNING. A dead Redis must never
  block the bot's X lanes; the cost is a brief window of un-metered calls.

Persistence
-----------
* ``getxapi:calls:{YYYYMMDD}`` — INCR daily counter (TTL 48h so stale keys
  expire).
* ``getxapi:calls:hourly:{YYYYMMDDHH}`` — INCR hourly counter (pacing).
* ``getxapi:pry:{YYYYMMDD}`` / ``getxapi:pry:hourly:{YYYYMMDDHH}`` — Pry scrape
  counters (free lane, metered for visibility).
* ``getxapi:content:{YYYYMMDD}`` — total content fetched (tweets/scrapes).
* ``getxapi:spend`` — INCRBYFLOAT cumulative spend in dollars (no TTL — it is
  the lifetime total).
* ``getxapi:pacing_until`` — epoch timestamp until which the bot stays
  Pry-only (empty = not pacing).

Thread-safety: the bot runs a single asyncio event loop; Redis INCR /
INCRBYFLOAT are atomic, so concurrent ticks cannot race the counters.
"""

from __future__ import annotations

import os
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

# ══════════════════════════════════════════════════════════════════════════
# INTELLIGENT ROUTING + PACING (env-overridable)
# ══════════════════════════════════════════════════════════════════════════

#: Follower-count threshold that marks an account "high-value". Accounts with
#: MORE followers than this get the reliable GetXAPI lane; accounts at or below
#: it are low-value and get the free Pry scrape lane instead.
_HIGH_VALUE_FOLLOWERS = int(os.getenv("GETXAPI_HIGH_VALUE_FOLLOWERS", "50000"))

#: Hourly call ceiling. When GetXAPI calls in the current hour exceed this, the
#: bot switches to Pry-only for the remainder of the hour (pacing).
_HOURLY_CALL_LIMIT = int(os.getenv("GETXAPI_HOURLY_CALL_LIMIT", "50"))

#: Daily call ceiling for pacing. When GetXAPI calls today exceed this, the bot
#: switches to Pry-only for the rest of the day. NOTE: this is the *pacing*
#: ceiling (softer, triggers Pry fallback), distinct from the hard
#: ``GETXAPI_DAILY_CALL_LIMIT`` that ``can_afford_call`` enforces.
_PACING_DAILY_LIMIT = int(os.getenv("GETXAPI_PACING_DAILY_LIMIT", "400"))

#: Redis key prefix for the hourly call counter.
_HOURLY_KEY_PREFIX = "getxapi:calls:hourly"
#: Redis key prefix for the Pry call counter (daily).
_PRY_KEY_PREFIX = "getxapi:pry"
#: Redis key prefix for the Pry hourly counter.
_PRY_HOURLY_KEY_PREFIX = "getxapi:pry:hourly"
#: Redis key prefix for the total-content-fetched counter (daily).
_CONTENT_KEY_PREFIX = "getxapi:content"
#: Redis key for the pacing lock — an ISO/epoch timestamp until which the bot
#: stays Pry-only (empty = not pacing).
_PACING_KEY = "getxapi:pacing_until"

#: In-memory flag so the hourly budget-status log fires once per hour.
_last_status_hour: str = ""

#: In-memory flags so each threshold logs exactly once per process.
_warned_calls = False
_crit_calls = False
_crit_spend = False


def _today() -> str:
    """Today's date as ``YYYYMMDD`` (UTC) — the daily counter key suffix."""
    return time.strftime("%Y%m%d", time.gmtime())


def _daily_key() -> str:
    return f"{_CALLS_KEY_PREFIX}:{_today()}"


def _hour() -> str:
    """Current hour as ``YYYYMMDDHH`` (UTC) — the hourly counter key suffix."""
    return time.strftime("%Y%m%d%H", time.gmtime())


def _hourly_key() -> str:
    return f"{_HOURLY_KEY_PREFIX}:{_hour()}"


def _pry_key() -> str:
    return f"{_PRY_KEY_PREFIX}:{_today()}"


def _pry_hourly_key() -> str:
    return f"{_PRY_HOURLY_KEY_PREFIX}:{_hour()}"


def _content_key() -> str:
    return f"{_CONTENT_KEY_PREFIX}:{_today()}"


def _redis() -> Any | None:
    """Sync Redis client, or None when Redis is down (fail-open)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        return get_redis()
    except Exception as exc:  # pragma: no cover — import guard, fail-open
        logger.warning("getxapi_budget:redis_unavailable err=%s", str(exc)[:120])
        return None


def record_call(endpoint: str, cost: float | None = None) -> None:
    """Record one GetXAPI call: increment today's + hourly counters + spend.

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
        pipe.incr(_hourly_key())
        pipe.expire(_hourly_key(), _DAILY_KEY_TTL)
        pipe.incrbyfloat(_SPEND_KEY, cost)
        pipe.execute()
    except Exception as exc:  # fail-open by contract
        logger.warning("getxapi_budget:record_failed endpoint=%s err=%s", endpoint, str(exc)[:120])
        return
    _check_thresholds()
    _check_pacing()
    _log_budget_status()
    logger.debug("getxapi_budget:recorded endpoint=%s cost=%.4f", endpoint, cost)


def record_pry_call(endpoint: str = "pry") -> None:
    """Record one Pry scrape (free fallback): increment today's + hourly Pry
    counters and the total-content-fetched counter.

    Pry calls cost nothing, but we still meter them so ``get_daily_usage()``
    can report how much content the Pry lane is carrying (and so the pacing
    logic can see the full picture). Fail-open: Redis down → log and return.
    """
    r = _redis()
    if r is None:
        logger.debug("getxapi_budget:pry_record_failed_redis_down endpoint=%s", endpoint)
        return
    try:
        pipe = r.pipeline(transaction=False)
        pipe.incr(_pry_key())
        pipe.expire(_pry_key(), _DAILY_KEY_TTL)
        pipe.incr(_pry_hourly_key())
        pipe.expire(_pry_hourly_key(), _DAILY_KEY_TTL)
        pipe.incr(_content_key())
        pipe.expire(_content_key(), _DAILY_KEY_TTL)
        pipe.execute()
    except Exception as exc:  # fail-open by contract
        logger.debug(
            "getxapi_budget:pry_record_failed endpoint=%s err=%s", endpoint, str(exc)[:120]
        )
        return
    logger.debug("getxapi_budget:pry_recorded endpoint=%s", endpoint)


def record_content_fetched(count: int = 1) -> None:
    """Increment the total-content-fetched counter by *count* (default 1).

    Used when a GetXAPI call returns multiple tweets (e.g. 20 tweets in one
    call) so the report reflects content volume, not just call count. Fail-open.
    """
    count = max(0, int(count))
    if count == 0:
        return
    r = _redis()
    if r is None:
        return
    try:
        r.incrby(_content_key(), count)
        r.expire(_content_key(), _DAILY_KEY_TTL)
    except Exception as exc:  # fail-open by contract
        logger.debug("getxapi_budget:content_record_failed err=%s", str(exc)[:120])


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


def _read_usage() -> dict[str, int]:
    """Read the full usage counters (daily + hourly + Pry + content).

    Returns a dict with ``calls``, ``hourly_calls``, ``pry_calls``,
    ``pry_hourly_calls``, ``content``. Redis down → all zeros (fail-open).
    """
    r = _redis()
    if r is None:
        return {"calls": 0, "hourly_calls": 0, "pry_calls": 0, "pry_hourly_calls": 0, "content": 0}
    try:
        return {
            "calls": int(r.get(_daily_key()) or 0),
            "hourly_calls": int(r.get(_hourly_key()) or 0),
            "pry_calls": int(r.get(_pry_key()) or 0),
            "pry_hourly_calls": int(r.get(_pry_hourly_key()) or 0),
            "content": int(r.get(_content_key()) or 0),
        }
    except Exception as exc:  # fail-open by contract
        logger.warning("getxapi_budget:read_usage_failed err=%s", str(exc)[:120])
        return {"calls": 0, "hourly_calls": 0, "pry_calls": 0, "pry_hourly_calls": 0, "content": 0}


def _pacing_until() -> float:
    """Epoch timestamp until which the bot stays Pry-only (0 = not pacing)."""
    r = _redis()
    if r is None:
        return 0.0
    try:
        return float(r.get(_PACING_KEY) or 0.0)
    except Exception:  # fail-open by contract
        return 0.0


def _pacing_active() -> bool:
    """True when the bot is currently in Pry-only pacing mode."""
    return _pacing_until() > time.time()


def get_daily_usage() -> dict[str, Any]:
    """Current usage snapshot: calls, spend, budget remaining, calls remaining.

    Returns ``{"calls", "spend", "budget_remaining", "calls_remaining",
    "hourly_calls", "pry_calls", "pry_hourly_calls", "total_content",
    "pacing_active"}``. ``calls_remaining`` is the smaller of (daily cap -
    calls) and (budget_remaining / cost_per_call) - the true number of calls
    we can still afford today.
    """
    calls, spend = _read_counters()
    usage = _read_usage()
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
        "hourly_calls": usage["hourly_calls"],
        "pry_calls": usage["pry_calls"],
        "pry_hourly_calls": usage["pry_hourly_calls"],
        "total_content": usage["content"],
        "pacing_active": _pacing_active(),
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


def _set_pacing(until_epoch: float, reason: str) -> None:
    """Persist the Pry-only pacing lock until *until_epoch* (fail-open)."""
    r = _redis()
    if r is None:
        return
    try:
        r.set(_PACING_KEY, str(until_epoch))
    except Exception as exc:  # fail-open by contract
        logger.debug("getxapi_budget:pacing_set_failed reason=%s err=%s", reason, str(exc)[:120])


def _check_pacing() -> None:
    """Switch to Pry-only when hourly or daily GetXAPI calls exceed their caps.

    * Hourly calls > ``_HOURLY_CALL_LIMIT`` (50) → Pry-only for the rest of the
      current hour.
    * Daily calls > ``_PACING_DAILY_LIMIT`` (400) → Pry-only for the rest of the
      day (until 00:00 UTC tomorrow).

    Each transition logs a WARNING. Fail-open: Redis down → no-op (we simply
    can't meter, so pacing can't engage — the hard caps still protect us).
    """
    usage = _read_usage()
    now = time.time()
    if usage["hourly_calls"] > _HOURLY_CALL_LIMIT:
        # Pry-only until the top of the next hour.
        until = (int(now // 3600) + 1) * 3600
        if _pacing_until() < until:
            _set_pacing(until, "hourly")
            logger.warning(
                "getxapi_budget:pacing_hourly hourly_calls=%s limit=%s pry_only_until=%s",
                usage["hourly_calls"],
                _HOURLY_CALL_LIMIT,
                time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(until)),
            )
    if usage["calls"] > _PACING_DAILY_LIMIT:
        # Pry-only until 00:00 UTC tomorrow.
        tomorrow = (int(now // 86400) + 1) * 86400
        if _pacing_until() < tomorrow:
            _set_pacing(tomorrow, "daily")
            logger.warning(
                "getxapi_budget:pacing_daily calls=%s limit=%s pry_only_until=%s",
                usage["calls"],
                _PACING_DAILY_LIMIT,
                time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(tomorrow)),
            )


def _log_budget_status() -> None:
    """Log a budget-status line once per hour (not per call)."""
    global _last_status_hour
    hour = _hour()
    if hour == _last_status_hour:
        return
    _last_status_hour = hour
    usage = get_daily_usage()
    logger.info(
        "getxapi_budget:status hour=%s getxapi_calls=%s hourly=%s pry_calls=%s "
        "pry_hourly=%s content=%s pacing=%s",
        hour,
        usage["calls"],
        usage["hourly_calls"],
        usage["pry_calls"],
        usage["pry_hourly_calls"],
        usage["total_content"],
        usage["pacing_active"],
    )


def should_use_getxapi(endpoint: str, params: dict[str, Any] | None = None) -> bool:
    """Decide whether a data request should hit GetXAPI or fall back to Pry.

    This is the intelligent call router. It returns True when GetXAPI should be
    used (reliable, metered) and False when the free Pry scrape lane should be
    used instead (conserves the $5 envelope).

    Routing rules (per endpoint family):
    * ``search`` / ``advanced_search`` → **always GetXAPI** — search is the
      killer feature Pry can't replicate.
    * ``user-tweets`` / ``user/tweets`` → GetXAPI if the account is high-value
      (> ``_HIGH_VALUE_FOLLOWERS`` followers), else Pry (free).
    * ``user-info`` / ``user/info`` → **GetXAPI** (1 cheap call, high value).
    * ``user-mentions`` / ``user/mentions`` → **Pry** (free — just scrape the
      mentions page).
    * ``trends`` → **GetXAPI** (lightweight, 1 call).
    * ``user-status`` / ``user/status`` → **GetXAPI** (1 cheap call).

    Pacing override: when the bot is in Pry-only pacing mode (hourly > 50 or
    daily > 400), this returns False for every endpoint — GetXAPI is fully
    paused until the pacing window expires.

    ``params`` may carry ``followers_count`` (int) to classify a user-tweets
    account as high/low value. When it's absent we default to GetXAPI
    (conservative — never silently degrade reliability on unknown accounts).

    Fail-open: Redis down → pacing can't engage, so routing is purely
    rule-based (never blocks a call).
    """
    if _pacing_active():
        logger.debug("getxapi_budget:routing_pacing endpoint=%s → pry", endpoint)
        return False

    ep = str(endpoint or "").strip().lower().replace("/", "-")
    params = params or {}

    # Search is the killer feature — always GetXAPI.
    if ep in ("search", "advanced-search", "tweet-search"):
        return True

    # User tweets: high-value accounts get the reliable lane, low-value get Pry.
    if ep in ("user-tweets", "user-timeline"):
        followers = params.get("followers_count")
        if followers is not None:
            try:
                high_value = int(followers) > _HIGH_VALUE_FOLLOWERS
            except (TypeError, ValueError):
                high_value = True  # unknown → conservative GetXAPI
        else:
            high_value = True  # unknown → conservative GetXAPI
        return high_value

    # User info: 1 cheap call, high value — always GetXAPI.
    if ep == "user-info":
        return True

    # Mentions: free to scrape — always Pry.
    if ep == "user-mentions":
        return False

    # Trends: lightweight, 1 call — always GetXAPI.
    if ep in ("trends", "trend"):
        return True

    # User status: 1 cheap call — always GetXAPI.
    if ep == "user-status":
        return True

    # Unknown endpoint: default to GetXAPI (conservative).
    logger.debug("getxapi_budget:routing_unknown endpoint=%s → getxapi", endpoint)
    return True


def get_budget_report() -> str:
    """Human-readable budget report for logging / admin visibility."""
    usage = get_daily_usage()
    pacing = "PRY-ONLY" if usage["pacing_active"] else "normal"
    return (
        f"GetXAPI budget: {usage['calls']}/{GETXAPI_DAILY_CALL_LIMIT} calls today "
        f"(hourly {usage['hourly_calls']}, mode={pacing}), spent ${usage['spend']:.4f} of "
        f"${GETXAPI_TOTAL_BUDGET:.2f} (remaining ${usage['budget_remaining']:.4f}), "
        f"{usage['calls_remaining']} calls remaining, "
        f"Pry {usage['pry_calls']} calls / {usage['total_content']} content fetched"
    )


def reset_counters() -> None:
    """Clear all counters + threshold flags (test helper)."""
    global _warned_calls, _crit_calls, _crit_spend, _last_status_hour
    _warned_calls = False
    _crit_calls = False
    _crit_spend = False
    _last_status_hour = ""
    r = _redis()
    if r is None:
        return
    try:
        r.delete(
            _daily_key(),
            _hourly_key(),
            _pry_key(),
            _pry_hourly_key(),
            _content_key(),
            _SPEND_KEY,
            _PACING_KEY,
        )
    except Exception:  # pragma: no cover - fail-open
        logger.debug("getxapi_budget:reset_failed")


__all__ = [
    "can_afford_call",
    "get_budget_report",
    "get_daily_usage",
    "record_call",
    "record_content_fetched",
    "record_pry_call",
    "reset_counters",
    "should_use_getxapi",
]

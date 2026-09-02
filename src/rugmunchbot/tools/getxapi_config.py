"""Central configuration for the GetXAPI (X/Twitter) integration.

Single source of truth for every GetXAPI setting the bot uses — the API key,
base URL, the hard budget (daily call cap + total dollar budget), the polling
intervals for each endpoint family, and the list of security accounts whose
tweets we monitor. Every value is env-overridable with a sensible default, so
the same module works in dev (no key, tiny budget), CI (mocked), and
production (gopass-resolved key, real $5 budget) without code changes.

Budget doctrine (the numbers that keep us under $5)
---------------------------------------------------
* GetXAPI charges ~$0.001 per call (≈1,000 calls per $1). A $5 budget buys
  ≈4,500 calls.
* ``GETXAPI_DAILY_CALL_LIMIT`` = 150/day — at 150/day the $5 envelope lasts
  ~30 days (4,500 / 150). This is the hard per-day ceiling the budget tracker
  enforces (``can_afford_call``).
* ``GETXAPI_TOTAL_BUDGET`` = $5.00 — the hard lifetime ceiling. The tracker
  logs CRITICAL when spend crosses $4.50 (90% of budget) and refuses calls
  once spend reaches $5.00.
* Polling intervals are tuned so the sum of all scheduled lanes stays well
  under 150 calls/day (see docs/getxapi-setup.md for the arithmetic).

The key NEVER enters git. Resolution order (mirrors ``apipass_x``):
env ``GETXAPI_KEY`` → key file ``$RMI_SECRETS_DIR/getxapi_api_key`` →
gopass ``rmi/getxapi/api_key``. Never logged.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

# ══════════════════════════════════════════════════════════════════════════
# CORE SETTINGS
# ══════════════════════════════════════════════════════════════════════════

#: GetXAPI API key — resolved lazily (env → key file → gopass), never logged.
GETXAPI_KEY: str = os.getenv("GETXAPI_KEY", "").strip()
#: Base URL for the GetXAPI v1 REST API.
GETXAPI_BASE_URL: str = os.getenv("GETXAPI_BASE_URL", "https://api.getxapi.com/v1").rstrip("/")

#: Hard per-day call ceiling (calls). 150/day x 30 days ~= 4,500 calls = $5.
GETXAPI_DAILY_CALL_LIMIT: int = int(os.getenv("GETXAPI_DAILY_CALL_LIMIT", "150"))
#: Hard lifetime dollar budget. $5 at ~$0.001/call ≈ 4,500 calls.
GETXAPI_TOTAL_BUDGET: float = float(os.getenv("GETXAPI_TOTAL_BUDGET", "5.0"))
#: Assumed per-call cost in dollars (used for spend accounting + reporting).
GETXAPI_COST_PER_CALL: float = float(os.getenv("GETXAPI_COST_PER_CALL", "0.001"))

# ══════════════════════════════════════════════════════════════════════════
# POLLING INTERVALS (minutes / hours) — tuned to stay under the daily cap
# ══════════════════════════════════════════════════════════════════════════

#: Search lane cadence (minutes). 30-min interval ≈ 48 calls/day.
GETXAPI_SEARCH_INTERVAL_MIN: int = int(os.getenv("GETXAPI_SEARCH_INTERVAL_MIN", "30"))
#: User-tweets lane cadence (hours). 6-hour interval ≈ 4 calls/day/account.
GETXAPI_USER_TWEETS_INTERVAL_HOURS: int = int(os.getenv("GETXAPI_USER_TWEETS_INTERVAL_HOURS", "6"))
#: Trends lane cadence (minutes). 60-min interval ≈ 24 calls/day.
GETXAPI_TRENDS_INTERVAL_MIN: int = int(os.getenv("GETXAPI_TRENDS_INTERVAL_MIN", "60"))
#: Mentions lane cadence (minutes). 60-min interval ≈ 24 calls/day.
GETXAPI_MENTIONS_INTERVAL_MIN: int = int(os.getenv("GETXAPI_MENTIONS_INTERVAL_MIN", "60"))
#: Account-status lane cadence (hours). 24-hour interval ≈ 15 calls/day
#: (one per monitored account).
GETXAPI_USER_STATUS_INTERVAL_HOURS: int = int(os.getenv("GETXAPI_USER_STATUS_INTERVAL_HOURS", "24"))

# ══════════════════════════════════════════════════════════════════════════
# MONITORED ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════

#: Crypto-security / wallet-security accounts whose tweets are security intel.
#: Each is a first-party source of exploit / hack / scam / wallet-security
#: news — the same set the X-ingest lane watches. Env-overridable as a
#: comma-separated list (``GETXAPI_SECURITY_ACCOUNTS``).
GETXAPI_SECURITY_ACCOUNTS: tuple[str, ...] = tuple(
    handle.strip().lstrip("@")
    for handle in os.getenv(
        "GETXAPI_SECURITY_ACCOUNTS",
        "zachxbt,peckshield,blockaid,certikalert,slowmist,beosin,"
        "chainalysis,ledger,metamask,rugmunchbot,"
        "scamsniffer,rektnews,officiallychad,spotonchain,lookonchain",
    ).split(",")
    if handle.strip()
)

# ══════════════════════════════════════════════════════════════════════════
# KEY RESOLUTION — env → key file → gopass, never logged, never in git
# ══════════════════════════════════════════════════════════════════════════

#: Canonical key file for the GetXAPI key (never in git). Defaults to the
#: fleet secret-store location; overridable so tests/containers can point
#: elsewhere.
_GETXAPI_KEY_FILE: str = os.getenv("GETXAPI_KEY_FILE") or os.path.join(
    os.getenv("RMI_SECRETS_DIR", "/srv/secrets"), "getxapi_api_key"
)
#: Canonical gopass path for the GetXAPI key (never in git).
_GETXAPI_GOPASS_PATH: str = "rmi/getxapi/api_key"


def _resolve_api_key() -> str:
    """GetXAPI key: env ``GETXAPI_KEY`` → key file
    ``$RMI_SECRETS_DIR/getxapi_api_key`` → gopass ``rmi/getxapi/api_key``.

    UNCACHED so a key added while the bot runs is picked up on the next tick
    without a restart. Returns "" when none is configured — callers fail open
    (the budget tracker still works; only the API calls themselves are gated
    on a key being present).
    """
    if GETXAPI_KEY:
        return GETXAPI_KEY
    # Fleet secret-store file (world-readable secret dir, mode 0600).
    try:
        with open(_GETXAPI_KEY_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    except (OSError, UnicodeDecodeError):
        pass
    try:
        value = (
            subprocess.check_output(  # noqa: S603 — gopass is the canonical secret store
                ["gopass", "show", _GETXAPI_GOPASS_PATH],  # noqa: S607 — fixed tool, arg is a secret name
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
            .decode()
            .strip()
        )
        if value:
            return value
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return ""


def api_key_configured() -> bool:
    """True when a GetXAPI key is available (env, key file, or gopass)."""
    return bool(_resolve_api_key())


def as_dict() -> dict[str, Any]:
    """Full config snapshot for logging / admin visibility (key redacted)."""
    return {
        "base_url": GETXAPI_BASE_URL,
        "key_configured": api_key_configured(),
        "daily_call_limit": GETXAPI_DAILY_CALL_LIMIT,
        "total_budget": GETXAPI_TOTAL_BUDGET,
        "cost_per_call": GETXAPI_COST_PER_CALL,
        "search_interval_min": GETXAPI_SEARCH_INTERVAL_MIN,
        "user_tweets_interval_hours": GETXAPI_USER_TWEETS_INTERVAL_HOURS,
        "trends_interval_min": GETXAPI_TRENDS_INTERVAL_MIN,
        "mentions_interval_min": GETXAPI_MENTIONS_INTERVAL_MIN,
        "user_status_interval_hours": GETXAPI_USER_STATUS_INTERVAL_HOURS,
        "security_accounts": list(GETXAPI_SECURITY_ACCOUNTS),
    }


__all__ = [
    "GETXAPI_BASE_URL",
    "GETXAPI_COST_PER_CALL",
    "GETXAPI_DAILY_CALL_LIMIT",
    "GETXAPI_KEY",
    "GETXAPI_MENTIONS_INTERVAL_MIN",
    "GETXAPI_SEARCH_INTERVAL_MIN",
    "GETXAPI_SECURITY_ACCOUNTS",
    "GETXAPI_TOTAL_BUDGET",
    "GETXAPI_TRENDS_INTERVAL_MIN",
    "GETXAPI_USER_STATUS_INTERVAL_HOURS",
    "GETXAPI_USER_TWEETS_INTERVAL_HOURS",
    "api_key_configured",
    "as_dict",
]

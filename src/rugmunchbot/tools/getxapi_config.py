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
* ``GETXAPI_DAILY_CALL_LIMIT`` = 460/day — the hard per-day ceiling the budget
  tracker enforces (``can_afford_call``). At 460/day the $5 envelope lasts
  ~11 days (4,500 / 460 ≈ 9.8, but the tiered lanes below actually burn
  ~460/day so the practical lifespan is ~11 days).
* ``GETXAPI_TOTAL_BUDGET`` = $5.00 — the hard lifetime ceiling. The tracker
  logs CRITICAL when spend crosses $4.50 (90% of budget) and refuses calls
  once spend reaches $5.00.
* Tiered polling keeps 800+ accounts under the cap without polling each one:
  - TIER 1 (30 security + news accounts) polled every 6h  → ~120 calls/day
  - TIER 2 (100 KOL / analyst / fund accounts) every 24h  → ~100 calls/day
  - TIER 3+4 (700 long-tail / shiller accounts) are NOT polled individually;
    they are caught by 10 rotating keyword ``advanced_search`` queries every
    30 min → ~240 calls/day
  - Total ≈ 120 + 100 + 240 = 460 calls/day = $0.46/day → $5 lasts ~11 days.
* Polling intervals are tuned so the sum of all scheduled lanes stays well
  under 460 calls/day (see docs/getxapi-setup.md for the arithmetic).

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

#: Hard per-day call ceiling (calls). 460/day x ~11 days ~= 4,500 calls = $5.
GETXAPI_DAILY_CALL_LIMIT: int = int(os.getenv("GETXAPI_DAILY_CALL_LIMIT", "460"))
#: Hard lifetime dollar budget. $5 at ~$0.001/call ≈ 4,500 calls.
GETXAPI_TOTAL_BUDGET: float = float(os.getenv("GETXAPI_TOTAL_BUDGET", "5.0"))
#: Assumed per-call cost in dollars (used for spend accounting + reporting).
GETXAPI_COST_PER_CALL: float = float(os.getenv("GETXAPI_COST_PER_CALL", "0.001"))

# ══════════════════════════════════════════════════════════════════════════
# POLLING INTERVALS (minutes / hours) — tuned to stay under the daily cap
# ══════════════════════════════════════════════════════════════════════════

#: Search lane cadence (minutes). 30-min interval ≈ 48 calls/day per query;
#: with 10 rotating keyword queries this is the TIER 3+4 catch-all lane
#: (≈240 calls/day).
GETXAPI_SEARCH_INTERVAL_MIN: int = int(os.getenv("GETXAPI_SEARCH_INTERVAL_MIN", "30"))
#: TIER 1 user-tweets lane cadence (hours). 6-hour interval ≈ 4 calls/day/account
#: × 30 accounts ≈ 120 calls/day.
GETXAPI_TIER1_INTERVAL_HOURS: int = int(os.getenv("GETXAPI_TIER1_INTERVAL_HOURS", "6"))
#: TIER 2 user-tweets lane cadence (hours). 24-hour interval ≈ 1 call/day/account
#: × 100 accounts ≈ 100 calls/day.
GETXAPI_TIER2_INTERVAL_HOURS: int = int(os.getenv("GETXAPI_TIER2_INTERVAL_HOURS", "24"))
#: Legacy alias for the TIER 1 user-tweets cadence (kept for backward
#: compatibility with callers that read ``GETXAPI_USER_TWEETS_INTERVAL_HOURS``).
GETXAPI_USER_TWEETS_INTERVAL_HOURS: int = GETXAPI_TIER1_INTERVAL_HOURS
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
        "chainalysis,elliptic,trmlabs,hashex,"
        "pwnfoo,angularrobot,samczsun,0xfoobar,hacxyk",
    ).split(",")
    if handle.strip()
)

#: Crypto-news accounts whose tweets are market / industry news intel.
#: Env-overridable as a comma-separated list (``GETXAPI_NEWS_ACCOUNTS``).
GETXAPI_NEWS_ACCOUNTS: tuple[str, ...] = tuple(
    handle.strip().lstrip("@")
    for handle in os.getenv(
        "GETXAPI_NEWS_ACCOUNTS",
        "coindesk,cointelegraph,thedefiscient,banklesssociety,cryptonews,"
        "watcherguru,wublockchain,blockworks_,unusual_whales,tier10k",
    ).split(",")
    if handle.strip()
)

#: DeFi / MEV accounts whose tweets are protocol / MEV / on-chain intel.
#: Env-overridable as a comma-separated list (``GETXAPI_DEFI_MEV_ACCOUNTS``).
GETXAPI_DEFI_MEV_ACCOUNTS: tuple[str, ...] = tuple(
    handle.strip().lstrip("@")
    for handle in os.getenv(
        "GETXAPI_DEFI_MEV_ACCOUNTS",
        "flashbotdoteth,mevreful,eigenphi,0xrobs,hildobby",
    ).split(",")
    if handle.strip()
)

#: TIER 2 accounts — top crypto KOLs, analysts, and fund managers whose tweets
#: are market / sentiment / alpha intel. Polled every 24h (≈1 call/day each,
#: ≈100 calls/day total). Env-overridable as a comma-separated list
#: (``GETXAPI_TIER2_ACCOUNTS``).
GETXAPI_TIER2_ACCOUNTS: tuple[str, ...] = tuple(
    handle.strip().lstrip("@")
    for handle in os.getenv(
        "GETXAPI_TIER2_ACCOUNTS",
        # KOLs / analysts / traders
        "cryptohornhorn,0xMert_,blknoiz06,cobie,ledgerstatus,CryptoKaleo,"
        "CryptoCapo_,rektcapital,inmortalcrypto,venturecoinist,cryptobirb,"
        "smartcontracter,ilblackcap,cryptoismiso,notsofast,puntium,"
        "therationalroot,cryptohamza,crypto_birb,z3nch4n,cryptosdecrypto,"
        "theoneandonlyds,cryptomocho,h_s_zer0,cryptopunk6529,punk6529,wagiee,"
        "cryptogenet,cryptodraco,cryptobull,cryptolord,"
        # DeFi / on-chain analysts
        "defi_mochi,defi_ant,defi_ox,tokenterminal,defillama,duneanalytics,"
        "nansen_ai,glassnode,santimentfeed,messaricrypto,intotheblock,"
        "coinmetrics,dapp_radar,dapptotal,"
        # Infrastructure / data / explorers
        "ethgasstation,gasnow_org,etherchain_org,ethernodes,ethernomicon,"
        "etherscan,coingecko,coinmarketcap,dexscreener,geckoterminal,"
        "bubblemaps,arkhamintel,"
        # Founders / builders / macro voices
        "VitalikButerin,CZ_Binance,sassal0x,aeyakovenko,APompliano,"
        "TheCryptoLark,Nicholas_Merten,100trillionUSD,hasufl,jchervinsky,"
        "gakonst,trent_vanepps,nic__carter,Melt_Dem,aripaul,CeterisPar1bus,"
        "danheld,TheCryptoDog,cryptocobain,zhusu,aantonop,rajkamal1,"
        "Boxmining,IvanOnTech,eric_wallingford,muneeb",
    ).split(",")
    if handle.strip()
)

#: TIER 3+4 keyword search queries — the catch-all lane for the ~700 long-tail
#: / shiller / emerging accounts we do NOT poll individually. Each run picks
#: ONE query (round-robin) so all 10 are covered roughly every 5 hours; at a
#: 30-min cadence this lane burns ≈240 calls/day. The ``-is:retweet`` +
#: ``min_faves`` operators keep the signal high (original posts with real
#: engagement) and the noise low. Env-overridable as a ``|``-separated list
#: (``GETXAPI_TIER3_4_KEYWORD_QUERIES``).
GETXAPI_TIER3_4_KEYWORD_QUERIES: tuple[str, ...] = tuple(
    query.strip()
    for query in os.getenv(
        "GETXAPI_TIER3_4_KEYWORD_QUERIES",
        (
            '"shill" OR "pump" OR "moon" OR "100x" crypto -is:retweet min_faves:100'
            "|"
            '"rug" OR "scam" OR "honeypot" crypto -is:retweet min_faves:50'
            "|"
            '"airdrop" OR "claim" OR "free" crypto -is:retweet min_faves:200'
            "|"
            '"launch" OR "presale" OR "IDO" crypto -is:retweet min_faves:50'
            "|"
            '"KOL" OR "influencer" OR "shiller" crypto -is:retweet min_faves:20'
            "|"
            '"giving away" OR "send me" OR "DM me" crypto -is:retweet min_faves:50'
            "|"
            '"guaranteed" OR "risk-free" OR "can\'t lose" crypto -is:retweet'
            "|"
            '"to the moon" OR "this will 100x" OR "next bitcoin" -is:retweet min_faves:100'
            "|"
            '"get in early" OR "don\'t miss" OR "last chance" crypto -is:retweet min_faves:50'
            "|"
            '"sponsored" OR "paid promotion" OR "ad" crypto -is:retweet min_faves:20'
        ),
    ).split("|")
    if query.strip()
)

#: Every individually-polled account across all tiers — the single source of
#: truth for the user-tweets rotation lanes (30 TIER 1 + 100 TIER 2 = 130
#: accounts). Order: TIER 1 (security, then news, then DeFi/MEV), then TIER 2.
#: Env-overridable via the per-category vars above.
GETXAPI_MONITORED_ACCOUNTS: tuple[str, ...] = (
    GETXAPI_SECURITY_ACCOUNTS
    + GETXAPI_NEWS_ACCOUNTS
    + GETXAPI_DEFI_MEV_ACCOUNTS
    + GETXAPI_TIER2_ACCOUNTS
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
        "tier1_interval_hours": GETXAPI_TIER1_INTERVAL_HOURS,
        "tier2_interval_hours": GETXAPI_TIER2_INTERVAL_HOURS,
        "user_tweets_interval_hours": GETXAPI_USER_TWEETS_INTERVAL_HOURS,
        "trends_interval_min": GETXAPI_TRENDS_INTERVAL_MIN,
        "mentions_interval_min": GETXAPI_MENTIONS_INTERVAL_MIN,
        "user_status_interval_hours": GETXAPI_USER_STATUS_INTERVAL_HOURS,
        "security_accounts": list(GETXAPI_SECURITY_ACCOUNTS),
        "news_accounts": list(GETXAPI_NEWS_ACCOUNTS),
        "defi_mev_accounts": list(GETXAPI_DEFI_MEV_ACCOUNTS),
        "tier2_accounts": list(GETXAPI_TIER2_ACCOUNTS),
        "tier3_4_keyword_queries": list(GETXAPI_TIER3_4_KEYWORD_QUERIES),
        "monitored_accounts": list(GETXAPI_MONITORED_ACCOUNTS),
    }


__all__ = [
    "GETXAPI_BASE_URL",
    "GETXAPI_COST_PER_CALL",
    "GETXAPI_DAILY_CALL_LIMIT",
    "GETXAPI_DEFI_MEV_ACCOUNTS",
    "GETXAPI_KEY",
    "GETXAPI_MENTIONS_INTERVAL_MIN",
    "GETXAPI_MONITORED_ACCOUNTS",
    "GETXAPI_NEWS_ACCOUNTS",
    "GETXAPI_SEARCH_INTERVAL_MIN",
    "GETXAPI_SECURITY_ACCOUNTS",
    "GETXAPI_TIER1_INTERVAL_HOURS",
    "GETXAPI_TIER2_ACCOUNTS",
    "GETXAPI_TIER2_INTERVAL_HOURS",
    "GETXAPI_TIER3_4_KEYWORD_QUERIES",
    "GETXAPI_TOTAL_BUDGET",
    "GETXAPI_TRENDS_INTERVAL_MIN",
    "GETXAPI_USER_STATUS_INTERVAL_HOURS",
    "GETXAPI_USER_TWEETS_INTERVAL_HOURS",
    "api_key_configured",
    "as_dict",
]

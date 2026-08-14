"""
RugMunchBot configuration — ENV-ONLY, no secrets.

OSS rewrite of the live production ``config.py`` (696 lines). The live
file hardcodes: secret-store paths, numeric owner/channel IDs, fee wallets,
Stars pricing tiers, and DEX referral codes. NONE of that ships here —
everything below is env-driven with empty defaults.

Self-hosters configure everything through environment variables. Empty
defaults mean the feature is disabled until the operator sets it.

Migration reference: docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md §3.

Env surface (documented in .env.example):

    TELEGRAM_BOT_TOKEN        Bot token (required to run)
    BOT_OWNER_IDS             Comma-separated numeric Telegram user IDs (admin commands)
    BOT_CHANNELS              JSON map of channel IDs, e.g. {"alerts": "-100..."}
    RMI_BACKEND_URL           Optional backend API base URL (default http://localhost:8000)
    RMI_API_KEY               Optional backend API key (X-API-Key header)
    SENTINEL_URL              Optional deep-scan endpoint (operator's own deployment)
    BOT_FREE_SCANS_PER_WEEK   Free-tier weekly scan quota (default 25)
    BOT_FREE_AI_MSGS_PER_WEEK Free-tier weekly AI message quota (default 15)
    BOT_DEX_REF_LINKS         JSON map of DEX referral links (ref codes are operator's own)
    BIRDEYE_API_KEY           Optional Birdeye API key for direct price lookups
    SEARXNG_URL               Self-hosted SearXNG meta-search URL (default http://127.0.0.1:8889)
    BOT_LLM_API_BASE          Local LLM OpenAI-compatible base URL (fallback chat)
    BOT_LLM_MODEL             Local LLM model name
    BOT_LLM_API_KEY           Optional LLM API key (empty = local only)
    BOT_DB_PATH               SQLite database path (default ./data/bot_users.db)
    BOT_ALERT_CHECK_INTERVAL  Seconds between background price-alert checks (default 300)
    BOT_DESCRIPTION           Bot profile description (Telegram /setdescription; empty default)
    BOT_SHORT_DESCRIPTION     Bot short description (Telegram /setshortdescription; empty default)
    LOG_LEVEL                 DEBUG | INFO | WARNING | ERROR (default INFO)

Public API (stable — matches the live module so ported handlers keep working):

    BOT_TOKEN, OWNER_IDS, CHANNELS, BACKEND_URL, API_KEY,
    DEX_REF_LINKS, TIERS, RISK_COLORS,
    ALERT_CHECK_INTERVAL, BOT_DESCRIPTION, BOT_SHORT_DESCRIPTION,
    check_backend_health,
    fmt_number, fmt_pct, fmt_chain, risk_bar, threat_indicator
"""

from __future__ import annotations

import json
import os
from typing import Any

# ── Bot identity ────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "RugMunchBot")


def _parse_owner_ids(raw: str) -> set[int]:
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}


# Empty default — admin/owner surfaces stay disabled until the operator sets it.
OWNER_IDS: set[int] = _parse_owner_ids(os.getenv("BOT_OWNER_IDS", ""))


def _parse_channels(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return {k: str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


# Empty default — broadcasts/alerts to named channels stay disabled.
CHANNELS: dict[str, str] = _parse_channels(os.getenv("BOT_CHANNELS", ""))

# ── Backend (optional, operator-supplied) ───────────────────────────────────
BACKEND_URL: str = os.getenv("RMI_BACKEND_URL", "http://localhost:8000").rstrip("/")
API_KEY: str = os.getenv("RMI_API_KEY", "").strip()
SENTINEL_URL: str = os.getenv("SENTINEL_URL", "").strip()


def backend_headers() -> dict[str, str]:
    """Headers for bot→backend calls. Empty dict when no API key is set."""
    return {"X-API-Key": API_KEY} if API_KEY else {}


async def check_backend_health(timeout: float = 3.0) -> bool:
    """Probe the operator's backend health endpoint.

    Tries ``{BACKEND_URL}/ready`` then ``{BACKEND_URL}/health``; any 2xx
    response counts as healthy. Fail-open: a missing/erroring backend
    returns ``False`` (never raises) so bot startup cannot hard-fail on a
    down or unconfigured operator backend — the bot keeps running with
    external providers as fallback.
    """
    import httpx

    for endpoint in ("/ready", "/health"):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                r = await client.get(f"{BACKEND_URL}{endpoint}", headers=backend_headers())
                if 200 <= r.status_code < 300:
                    return True
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError):
            continue
    return False


# ── Free tier (quota-only; billing/premium stays private) ───────────────────
BETA_MODE: bool = os.getenv("RMI_BOT_BETA_MODE", "false").lower() in ("true", "1", "yes")
FREE_SCANS_PER_WEEK: int = int(os.getenv("BOT_FREE_SCANS_PER_WEEK", "25"))
FREE_AI_MSGS_PER_WEEK: int = int(os.getenv("BOT_FREE_AI_MSGS_PER_WEEK", "15"))

# Minimal tier map — premium tiers are feature flags in the OSS build.
TIERS: dict[str, dict[str, Any]] = {
    "free": {
        "name": "Free",
        "emoji": "🆓",
        "scans_per_week": FREE_SCANS_PER_WEEK,
        "ai_msgs_per_week": FREE_AI_MSGS_PER_WEEK,
        "features": ["Basic risk score", "Contract safety check", "Liquidity overview"],
    },
}


# ── DEX referral links (operator's own ref codes) ───────────────────────────
def _parse_ref_links(raw: str) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


DEX_REF_LINKS: dict[str, dict[str, Any]] = _parse_ref_links(os.getenv("BOT_DEX_REF_LINKS", ""))

# ── External API defaults (public, no auth) ─────────────────────────────────
DEXSCREENER = "https://api.dexscreener.com/latest/dex"
DEXSCREENER_TOKEN_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"  # noqa: S105 — public URL constant
COINGECKO = "https://api.coingecko.com/api/v3"
GOPLUS = "https://api.gopluslabs.com/api/v1"
HONEYPOT = "https://api.honeypot.is/v2"

# ── Formatting helpers (ported verbatim from the live module) ───────────────
RISK_COLORS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "safe": "✅",
    "unknown": "⚪",
}


def fmt_number(n: Any) -> str:
    """Format large numbers: 1.2M, 450K, etc."""
    if n is None:
        return "N/A"
    try:
        n = float(n)
    except (ValueError, TypeError):
        return str(n)
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    if n >= 1e3:
        return f"${n / 1e3:.1f}K"
    if n >= 1:
        return f"${n:.4f}"
    if n >= 0.01:
        return f"${n:.6f}"
    return f"${n:.10f}"


def fmt_pct(n: Any) -> str:
    if n is None:
        return "N/A"
    try:
        return f"{float(n):.2f}%"
    except (ValueError, TypeError):
        return str(n)


def fmt_chain(chain: str) -> str:
    """Return chain emoji + name."""
    emojis = {
        "solana": "◎",
        "ethereum": "⟠",
        "bsc": "🔶",
        "polygon": "🟣",
        "arbitrum": "🔵",
        "optimism": "🔴",
        "base": "🔷",
        "avalanche": "🔺",
        "fantom": "👻",
        "tron": "🔻",
        "bitcoin": "₿",
    }
    return f"{emojis.get(chain.lower(), '⛓️')} {chain.capitalize()}"


def risk_bar(score: float, length: int = 10) -> str:
    """Generate a visual risk bar (0-100 scale)."""
    filled = int((score / 100) * length)
    if score >= 80:
        return "🔴" * filled + "⬜" * (length - filled) + f" {score}% DANGER"
    if score >= 60:
        return "🟠" * filled + "⬜" * (length - filled) + f" {score}% RISKY"
    if score >= 40:
        return "🟡" * filled + "⬜" * (length - filled) + f" {score}% CAUTION"
    if score >= 20:
        return "🟢" * filled + "⬜" * (length - filled) + f" {score}% LOW"
    return "✅" * filled + "⬜" * (length - filled) + f" {score}% SAFE"


def threat_indicator(label: str, found: bool) -> str:
    return f"{'🔴' if found else '✅'} {label}"


# ── Local LLM (chat fallback; OpenAI-compatible endpoint) ───────────────────
BOT_LLM_API_BASE: str = os.getenv("BOT_LLM_API_BASE", "").strip()
BOT_LLM_MODEL: str = os.getenv("BOT_LLM_MODEL", "").strip()
BOT_LLM_API_KEY: str = os.getenv("BOT_LLM_API_KEY", "").strip()

# ── Branding / operator URLs (env-only, empty defaults) ──────────────────────
WEBSITE_URL: str = os.getenv("BOT_WEBSITE_URL", "").rstrip("/")
WEB_APP_URL: str = os.getenv("BOT_WEB_APP_URL", f"{WEBSITE_URL}/scanner" if WEBSITE_URL else "")
TWITTER_URL: str = os.getenv("BOT_TWITTER_URL", "")
TELEGRAM_CHANNEL: str = os.getenv("BOT_TELEGRAM_CHANNEL", "")
TELEGRAM_GROUP: str = os.getenv("BOT_TELEGRAM_GROUP", "")
SUPPORT_EMAIL: str = os.getenv("BOT_SUPPORT_EMAIL", "")

# Bot profile blurbs — env-driven, empty default (Telegram keeps its own
# placeholder until the operator sets these; see checklist §3).
BOT_DESCRIPTION: str = os.getenv("BOT_DESCRIPTION", "")
BOT_SHORT_DESCRIPTION: str = os.getenv("BOT_SHORT_DESCRIPTION", "")

# ── Chain IDs (public EVM chain table, stable) ──────────────────────────────
CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "avalanche": 43114,
    "optimism": 10,
    "base": 8453,
    "fantom": 250,
}

# ── External API keys (env-only — never gopass, never files) ────────────────
BIRDEYE_API_KEY: str = os.getenv("BIRDEYE_API_KEY", "").strip()
COINGECKO_API_KEY: str = os.getenv("COINGECKO_API_KEY", "").strip()


# ── Channel roles → chat id (env JSON; no defaults → broadcast disabled) ────
def _resolve_channel_id(key: str) -> str | None:
    """Resolve a channel role (``alerts``/``news``/``alpha``/…) to its chat id.

    Env override per role wins, then ``BOT_CHANNELS`` JSON map, then ``None``
    (feature disabled). Returns ``None`` when the role is unknown/unset.
    """
    direct = os.getenv(f"BOT_CHANNEL_{key.upper()}", "").strip()
    if direct:
        return direct
    return CHANNELS.get(key)


# ── Channel job flags + cadence (env-driven; off by default in OSS) ─────────
def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


CHANNEL_JOBS_ENABLED: bool = _env_bool("RMI_CHANNELS_ENABLED", False)
NEWS_CHANNEL_ENABLED: bool = _env_bool("RMI_NEWS_CHANNEL_ENABLED", False)
ALERTS_CHANNEL_ENABLED: bool = _env_bool("RMI_ALERTS_CHANNEL_ENABLED", False)
ALPHA_CHANNEL_ENABLED: bool = _env_bool("RMI_ALPHA_CHANNEL_ENABLED", False)

NEWS_CHANNEL_INTERVAL_HOURS: float = _env_float("RMI_CHANNEL_NEWS_INTERVAL_HOURS", 4.0)
ALPHA_CHANNEL_INTERVAL_HOURS: int = _env_int("RMI_CHANNEL_ALPHA_INTERVAL_HOURS", 2)
ALERTS_CHANNEL_INTERVAL_MINUTES: int = _env_int("RMI_CHANNEL_ALERTS_INTERVAL_MINUTES", 30)

NEWS_CHANNEL_LIMIT: int = _env_int("RMI_CHANNEL_NEWS_LIMIT", 5)
ALPHA_CHANNEL_LIMIT: int = _env_int("RMI_CHANNEL_ALPHA_LIMIT", 5)
ALERTS_CHANNEL_LIMIT: int = _env_int("RMI_CHANNEL_ALERTS_LIMIT", 5)

# ── Alert engine cadence (env-driven; used by bot.py's JobQueue) ────────────
ALERT_CHECK_INTERVAL: int = _env_int("BOT_ALERT_CHECK_INTERVAL", 300)

# ── Billing packs (private in the live bot — OSS ships empty, operator fills) ─
TOP_UP_PACKS: dict[str, dict[str, Any]] = {}
BUY_STARS_PACKS: dict[str, dict[str, Any]] = {}

# ── Scam-school education topics (public educational content) ───────────────
SCAM_SCHOOL_TOPICS: dict[str, dict[str, str]] = {
    "honeypot": {
        "title": "🍯 Honeypot Tokens",
        "content": "A honeypot lets you buy but prevents you from selling — the contract only allows whitelisted addresses to sell.\n\n🔍 *How to spot:*\n• Can buy but can't sell\n• Only 1-2 sellers ever\n• Contract has sell restrictions\n• Honeypot.is flags it\n\n🛡️ *Protection:* Always test with a small amount first, and use /scan before buying.",
    },
    "rugpull": {
        "title": "🏃 Rug Pulls",
        "content": "Developers drain all liquidity, making the token worthless.\n\n🔍 *How to spot:*\n• Unlocked liquidity\n• Unrenounced contract\n• Dev holds >20% supply\n• New wallet, no history\n\n🛡️ *Protection:* Check /scan for LP lock status and dev wallet analysis.",
    },
    "bundling": {
        "title": "📦 Bundle Scams",
        "content": "Devs fund 10-50 wallets from one source to fake organic activity.\n\n🔍 *How to spot:*\n• Wallets funded from the same CEX/bridge\n• Identical buy amounts\n• Same-timestamp transactions\n\n🛡️ *Protection:* Use /scan bundle detection.",
    },
    "wash_trading": {
        "title": "🔄 Wash Trading",
        "content": "Artificially inflating volume by trading between own wallets.\n\n🔍 *How to spot:*\n• High volume, low unique traders\n• Same wallets trading back and forth\n• Volume doesn't match market cap\n\n🛡️ *Protection:* /scan checks volume authenticity.",
    },
    "fresh_wallets": {
        "title": "🆕 Fresh Wallet Red Flags",
        "content": "Most holders being brand-new wallets (<7 days old) often means fake holders.\n\n🔍 *How to spot:*\n• >50% wallets <7 days old\n• Wallets only hold 1 token\n• No history before buying\n\n🛡️ *Protection:* /scan shows wallet age distribution.",
    },
}


# ── Risk gauge (compact emoji bar) ───────────────────────────────────────────
def _risk_gauge(score: float) -> str:
    """Compact emoji risk gauge for scan results."""
    if score >= 80:
        return "🔴🔴🔴🔴🔴 EXTREME RISK"
    if score >= 60:
        return "🟠🟠🟠🟠⬜ HIGH RISK"
    if score >= 40:
        return "🟡🟡🟡⬜⬜ MODERATE"
    if score >= 20:
        return "🟢🟢⬜⬜⬜ LOW RISK"
    return "✅✅✅✅✅ SAFE"

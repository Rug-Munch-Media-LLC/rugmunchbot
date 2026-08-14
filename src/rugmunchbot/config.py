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
    LOG_LEVEL                 DEBUG | INFO | WARNING | ERROR (default INFO)

Public API (stable — matches the live module so ported handlers keep working):

    BOT_TOKEN, OWNER_IDS, CHANNELS, BACKEND_URL, API_KEY,
    DEX_REF_LINKS, TIERS, RISK_COLORS,
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

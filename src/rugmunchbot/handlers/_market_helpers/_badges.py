"""Chain aliases, badge tables and compact value rendering for market views.

Pure formatting only — no network I/O, no database access. Split out of
``handlers/_market_helpers.py`` (2026-10-23); the public surface is
re-exported from ``_market_helpers`` so every caller import stays valid.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


def _time_ago(dt_iso: str | None) -> str:
    """Convert ISO timestamp to human readable relative time."""
    if not dt_iso:
        return ""
    try:
        dt_iso_clean = dt_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(dt_iso_clean)
        diff = datetime.now(UTC) - dt
        total_secs = diff.total_seconds()
        if total_secs < 0:
            return "just now"
        if total_secs < 60:
            return f"{int(total_secs)}s ago"
        if total_secs < 3600:
            return f"{int(total_secs / 60)}m ago"
        if total_secs < 86400:
            return f"{int(total_secs / 3600)}h ago"
        return f"{diff.days}d ago"
    except (ConnectionError, OSError, TimeoutError, ValueError):
        # ValueError: malformed ISO from an upstream source must degrade to
        # an empty label, never crash the trending renderer.
        return ""


# Chain aliases accepted by /trending (backend RugCharts chain filter)
RUGCHARTS_CHAIN_ALIASES = {
    "sol": "solana",
    "eth": "ethereum",
    "bnb": "bsc",
    "arb": "arbitrum",
    "tron": "tron",
}
TRENDING_CHAINS = "solana · ethereum · base · bsc · arbitrum · tron"

# Chain badge emojis
CHAIN_BADGES = {
    "solana": "🟣",
    "ethereum": "🔵",
    "base": "🔵",
    "bsc": "🟡",
    "arbitrum": "🔵",
    "polygon": "🟣",
    "avalanche": "🔴",
    "optimism": "🔴",
    "tron": "🟢",
    "fantom": "👻",
}

# Liquidity tier thresholds (USD)
LIQ_TIERS = [
    (1_000_000, "💧 Deep"),
    (100_000, "💦 Medium"),
    (10_000, "💧 Shallow"),
    (0, "⚠️ Critical"),
]


def _resolve_chain(arg: str | None) -> str:
    """Normalize a /trending chain argument (aliases → canonical names).

    No argument (or an unrecognised alias) resolves to ``all`` — the fused
    multi-chain feed is the default view.
    """
    if not arg:
        return "all"
    chain = arg.lower().strip()
    return RUGCHARTS_CHAIN_ALIASES.get(chain, chain)


def _chain_badge(chain: str) -> str:
    """Return chain badge emoji."""
    return CHAIN_BADGES.get(chain.lower(), "⚪")


def _liq_badge(liquidity_usd: float) -> str:
    """Return liquidity tier badge."""
    for threshold, badge in LIQ_TIERS:
        if liquidity_usd >= threshold:
            return badge
    return "⚠️ Critical"


def _risk_emoji(risk_score: int | float | None) -> str:
    """Return risk emoji from score."""
    if risk_score is None:
        return "⚪"
    try:
        score = float(risk_score)
        if score >= 70:
            return "🔴"
        if score >= 40:
            return "🟡"
        return "🟢"
    except (TypeError, ValueError) as e:
        logger.debug("risk emoji score parse failed: %s", e)
        return "⚪"


def _fmt_price(price: Any) -> str:
    """Compact USD price for the trending detail line ("" when unknown/zero)."""
    try:
        v = float(price or 0)
    except (TypeError, ValueError):
        return ""
    if v >= 1:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.4f}"
    if v > 0:
        return f"${v:.8f}".rstrip("0")
    return ""

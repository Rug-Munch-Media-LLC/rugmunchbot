#!/usr/bin/env python3
"""Subscription tiers and one-time purchase packs."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._types import ScanPack, StarsPack, TierDefinition, TopUpPack

# ══════════════════════════════════════════════
# SUBSCRIPTION TIERS (Weekly Allotments)
# ══════════════════════════════════════════════
# Weekly model: resets every Monday 00:00 UTC.
# Prevents day-one dump abuse and smooths API load.
# BETA MODE: all premium features unlocked for everyone during public beta.
# Set to False when billing goes live. This overrides tier checks in db.py.


def _read_beta_mode() -> bool:
    """Beta-mode flag primitive (env ``RMI_BOT_BETA_MODE`` read per call).

    UNCACHED primitive: the ``@lru_cache`` lives on the ``_resolve_beta_mode``
    wrapper in the package ``__init__`` so a package reload resets it exactly
    like the old single-module config. Default OFF (False): billing/premium
    gates are enforced unless an operator explicitly opts into beta mode.
    """
    return os.environ.get("RMI_BOT_BETA_MODE", "false").lower() in ("true", "1", "yes")


TIERS: dict[str, TierDefinition] = {
    "free": {
        "name": "Free",
        "emoji": "🆓",
        "scans_per_week": 25,
        # BETA BUMP: 15 → 50 AI msgs/week while rugmunchbot is in FREE
        # PUBLIC BETA (all features unlocked). Revert to 15 when billing
        # goes live — this one value drives db.check_rate_limit/increment.
        "ai_msgs_per_week": 50,
        "price_monthly": 0,
        "features": [
            "25 scans/week",
            "50 AI questions/week (beta boost)",
            "Basic risk score",
            "Contract safety check",
            "Liquidity overview",
            "Top 10 holders",
            "Community access",
        ],
    },
    "scout": {
        "name": "Scout",
        "emoji": "🔍",
        "scans_per_week": 150,
        "ai_msgs_per_week": 75,
        "price_monthly": 29.99,
        "price_stars": 2999,
        "features": [
            "150 scans/week",
            "75 AI questions/week",
            "Advanced risk scoring",
            "Bundle detection",
            "Fake volume detection",
            "Dev wallet finder",
            "Holder analysis + concentration",
            "Smart money alerts",
            "Watchlist (10 tokens)",
            "Export reports",
        ],
    },
    "hunter": {
        "name": "Hunter",
        "emoji": "🎯",
        "scans_per_week": 400,
        "ai_msgs_per_week": 250,
        "price_monthly": 49.99,
        "price_stars": 4999,
        "features": [
            "400 scans/week",
            "250 AI questions/week",
            "Everything in Scout, plus:",
            "Full wallet forensics",
            "Cross-chain fund trace",
            "First buyer analysis",
            "Exchange flow tracking",
            "Fresh wallet detection",
            "Shill detection",
            "Social sentiment scan",
            "Watchlist (50 tokens)",
            "Real-time price alerts",
            "TA chart generation",
        ],
    },
    "pro": {
        "name": "Pro",
        "emoji": "👑",
        "scans_per_week": 1000,
        "ai_msgs_per_week": 500,
        "price_monthly": 99.99,
        "price_stars": 9999,
        "features": [
            "1,000 scans/week",
            "500 AI questions/week",
            "Everything in Hunter, plus:",
            "Deep investigation reports",
            "Smart money watch (live)",
            "Custom webhook integrations",
            "API access with tokens",
            "Priority support",
            "Early feature access",
            "Unlimited watchlists",
            "Multi-wallet tracking",
            "White-label reports",
        ],
    },
}

# ══════════════════════════════════════════════
# TOP-UP PACKS (Buy extra when you run out)
# ══════════════════════════════════════════════
# One-time purchases via Telegram Stars. No expiry -
# top-up balance carries over forever until used.
TOP_UP_PACKS: dict[str, TopUpPack] = {
    "scans_10": {
        "name": "10 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 10,
        "stars": 75,
    },
    "scans_50": {
        "name": "50 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 50,
        "stars": 300,
    },
    "scans_100": {
        "name": "100 Extra Scans",
        "emoji": "🔬",
        "type": "scans",
        "amount": 100,
        "stars": 500,
    },
    "ai_10": {
        "name": "10 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 10,
        "stars": 100,
    },
    "ai_50": {
        "name": "50 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 50,
        "stars": 400,
    },
    "ai_100": {
        "name": "100 Extra AI Messages",
        "emoji": "🤖",
        "type": "ai_msgs",
        "amount": 100,
        "stars": 700,
    },
}

# ══════════════════════════════════════════════
# BUY STARS PACKS (Purchase Stars via Telegram invoice)
# ══════════════════════════════════════════════
# Users pay Telegram Stars (XTR) and receive Stars credited
# to their local balance. ~$0.01 per Star.
BUY_STARS_PACKS: dict[str, StarsPack] = {
    "stars_100": {
        "name": "100 Stars",
        "emoji": "⭐",
        "stars": 100,
    },
    "stars_500": {
        "name": "500 Stars",
        "emoji": "⭐",
        "stars": 500,
    },
    "stars_1000": {
        "name": "1,000 Stars",
        "emoji": "⭐",
        "stars": 1000,
    },
    "stars_2500": {
        "name": "2,500 Stars",
        "emoji": "⭐",
        "stars": 2500,
    },
    "stars_5000": {
        "name": "5,000 Stars",
        "emoji": "⭐",
        "stars": 5000,
    },
}

# ══════════════════════════════════════════════
# SCAN PACKS (One-time deep analysis purchases)
# ══════════════════════════════════════════════
SCAN_PACKS: dict[str, ScanPack] = {
    "deep_scan": {
        "name": "🔬 Deep Token Scan",
        "description": "Complete forensic analysis of ONE token",
        "includes": [
            "Price history",
            "Holder RugMap",
            "Wallet clusters",
            "Scam indicators",
            "AI prediction",
            "Dev history",
        ],
        "stars": 250,
    },
    "monitor": {
        "name": "🛰️ Token Monitor",
        "description": "30 days of live monitoring for ONE token",
        "includes": [
            "LP remove / lock changes",
            "Whale + deployer wallet moves",
            "Tax / ownership / mint changes",
            "Price swings",
        ],
        "stars": 250,
    },
    "wallet_forensic": {
        "name": "🕵️ Wallet Forensic Report",
        "description": "Full wallet investigation - P&L, connections, history",
        "includes": [
            "Transaction history",
            "Counterparty analysis",
            "P&L breakdown",
            "Bundle links",
            "Scam DB check",
        ],
        "stars": 400,
    },
    "bundle_investigation": {
        "name": "🕸️ Bundle Investigation",
        "description": "Full cluster analysis - find every connected wallet",
        "includes": [
            "Funding source map",
            "Timing correlation",
            "Wash trade detection",
            "Holder overlap",
            "Exit strategy clues",
        ],
        "stars": 500,
    },
}

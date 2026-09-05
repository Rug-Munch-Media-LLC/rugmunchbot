"""Wallet intelligence — classify holder wallets into behavioral profiles.

Every wallet in the mega-sample gets labeled:
  CEX_HOTWALLET   — known exchange address (funds can move instantly)
  FRESH_WALLET    — recently created, no history (likely bundled)
  SNIPER          — bought within seconds/minutes of LP add
  INSIDER         — received tokens before public launch
  WHALE           — >5% of supply in one wallet
  ORGANIC         — none of the above (real user)

This gives degens the WHO behind the WHAT: not just "34% CEX-funded"
but "Binance hot wallet + OKX withdrawal + Bybit custody" — the exact
exchanges, so they know where the selling pressure will come from.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Known CEX hot wallets (EVM). Curated from public labels + our own data.
# This is a living list — expand as we identify more.
from rugmunchbot.tools.cex_database import (  # noqa: E402
    lookup as _cex_lookup,
    total_known as _cex_total_known,
)

# Minimum balance to classify as whale (>5% of supply is set dynamically)
WHALE_THRESHOLD_PCT = 5.0


def classify_wallet(
    address: str,
    *,
    percentage: float | None = None,
    first_seen_block: int | None = None,
    lp_add_block: int | None = None,
    funding_source: str | None = None,
) -> dict[str, Any]:
    """Classify a single wallet based on available signals.

    Priority: CEX > Insider > Sniper > Whale > Organic
    A wallet can have multiple tags; primary tag is the highest priority.
    """
    addr = address.lower().strip()
    tags: list[str] = []
    cex_label = None

    # 1. CEX check via comprehensive database
    cex_info = _cex_lookup(addr)
    if cex_info:
        cex_label = f"{cex_info['exchange']} {cex_info['type'].title()}"
        tags.append("CEX")
        tags.append(f"CEX:{cex_label}")

    # 2. Insider (received before LP add)
    if (
        first_seen_block is not None
        and lp_add_block is not None
        and first_seen_block < lp_add_block
    ):
        tags.append("INSIDER")

    # 3. Sniper (bought within ~3 blocks of LP add, roughly 36s on ETH)
    if (
        first_seen_block is not None
        and lp_add_block is not None
        and 0 <= (first_seen_block - lp_add_block) <= 3
    ):
        tags.append("SNIPER")

    # 4. Fresh (no block history = new wallet proxy)
    if first_seen_block is None and funding_source == "fresh":
        tags.append("FRESH")

    # 5. Whale
    if percentage is not None and percentage >= WHALE_THRESHOLD_PCT:
        tags.append("WHALE")

    # Primary classification
    if "CEX" in tags:
        primary = "CEX"
    elif "INSIDER" in tags:
        primary = "INSIDER"
    elif "SNIPER" in tags:
        primary = "SNIPER"
    elif "FRESH" in tags:
        primary = "FRESH"
    elif "WHALE" in tags:
        primary = "WHALE"
    else:
        primary = "ORGANIC"

    return {
        "address": address,
        "primary": primary,
        "tags": tags,
        "cex_label": cex_label,
        "percentage": percentage,
    }


def classify_sample(
    holders: list[dict[str, Any]],
    *,
    lp_add_block: int | None = None,
) -> dict[str, Any]:
    """Classify an entire holder sample into behavioral profiles.

    Returns aggregate stats + per-wallet classifications for flagged wallets.
    """
    classified: list[dict[str, Any]] = []
    profile_counts: dict[str, int] = {}
    cex_breakdown: dict[str, float] = {}  # exchange name → total % held

    _total_pct = sum(_f(h.get("percentage")) or 0 for h in holders) or 100.0

    for h in holders:
        addr = (h.get("address") or "").lower().strip()
        pct = _f(h.get("percentage"))
        result = classify_wallet(
            addr,
            percentage=pct,
            lp_add_block=lp_add_block,
        )
        classified.append(result)

        primary = result["primary"]
        profile_counts[primary] = profile_counts.get(primary, 0) + 1

        if result["cex_label"]:
            ex_name = result["cex_label"].rsplit(" ", 1)[0]  # strip number
            cex_pct = pct or 0.0
            cex_breakdown[ex_name] = round(cex_breakdown.get(ex_name, 0) + cex_pct, 2)

    return {
        "total_classified": len(classified),
        "profile_counts": profile_counts,
        "cex_breakdown": dict(sorted(cex_breakdown.items(), key=lambda x: x[1], reverse=True)),
        "flagged_wallets": [w for w in classified if w["primary"] != "ORGANIC"],
        "organic_count": profile_counts.get("ORGANIC", 0),
    }


async def enrich_holder_analysis(
    holders: list[dict[str, Any]],
    *,
    lp_add_block: int | None = None,
) -> dict[str, Any]:
    """Enrich a holder sample with per-wallet intelligence labels."""
    classification = classify_sample(holders, lp_add_block=lp_add_block)
    return {
        "wallet_profiles": classification,
        "enrichment_version": "1.0",
        "cex_addresses_known": _cex_total_known(),
    }

"""Referral tier mapping — standard (cards) vs ultra (deep/premium)."""

from __future__ import annotations

STANDARD = "standard"
ULTRA = "ultra"

#: Regular surfaces must use the standard account; deep/premium the ultra one.
TIER_BY_PURPOSE: dict[str, str] = {
    "scan": STANDARD,
    "card": STANDARD,
    "price": STANDARD,
    "trending": STANDARD,
    "alert": STANDARD,
    "news": STANDARD,
    "content": STANDARD,
    "menu": STANDARD,
    "deep": ULTRA,
    "deepscan": ULTRA,
    "premium": ULTRA,
    "watch": ULTRA,
    "sentinel": ULTRA,
}


def resolve_tier(purpose: str) -> str:
    """Map a bot surface name to a referral tier.

    Unknown or empty purposes fall back to ``STANDARD`` so a new surface can
    never silently spend the premium (ultra) attribution.
    """
    return TIER_BY_PURPOSE.get((purpose or "").strip().lower(), STANDARD)

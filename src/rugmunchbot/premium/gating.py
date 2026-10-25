#!/usr/bin/env python3
"""One tier-gate primitive for every paid surface.

Every premium feature asks this module the same question — "may this tier use
this?" — so the answer cannot drift feature to feature. The tier *names* come
from ``config.TIERS``; the *rank* is here, because rank is a product decision
and belongs in one place.

Refusal is a message, never a silent no-op: the reader learns the tier they
have, the tier they need, and the one command that lifts it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot.config import TIERS

if TYPE_CHECKING:
    from collections.abc import Callable

#: Ordered tier ranks. Two paid tiers are aliases of the top rank on purpose:
#: ``elite``/``whale`` are legacy billing names kept so old rows stay valid.
RANK: dict[str, int] = {
    "free": 0,
    "scout": 1,
    "hunter": 2,
    "pro": 3,
    "elite": 3,
    "whale": 3,
    "beta": 3,
}

#: Rank that unlocks every paid surface.
PREMIUM_RANK = 1

#: The feature -> minimum rank table. One row per shipped premium surface.
FEATURES: dict[str, int] = {
    "deepscan_forensic": PREMIUM_RANK,
    "portfolio_risk": PREMIUM_RANK,
    "watch_alerts": PREMIUM_RANK,
    "scam_feed": PREMIUM_RANK,
    "kol_tracker": 2,
}


def rank_of(tier: str | None) -> int:
    """Rank for a tier name; anything unrecognised is free."""
    return RANK.get(str(tier or "").strip().lower(), 0)


def allows(tier: str | None, feature: str) -> bool:
    """True when ``tier`` clears the feature's minimum rank.

    An unknown feature is refused — a typo must not unlock a paid surface.
    """
    needed = FEATURES.get(feature)
    return needed is not None and rank_of(tier) >= needed


def refusal(tier: str | None, feature: str) -> str:
    """The refusal card: what they have, what they need, how to lift it."""
    need = FEATURES.get(feature, PREMIUM_RANK)
    need_name = next((n for n, r in RANK.items() if r == need), "scout")
    have = str(tier or "free").strip().lower() or "free"
    have_name = TIERS.get(have, TIERS["free"])["name"]
    need_label = TIERS.get(need_name, TIERS["scout"])["name"]
    return (
        f"🔒 <b>{feature.replace('_', ' ').title()}</b> needs <b>{need_label}</b>.\n"
        f"You are on <b>{have_name}</b>.\n\n"
        f"Run /premium to see your quota and the upgrade path, "
        f"or /pricing for plans."
    )


def gate(
    user_id: int,
    feature: str,
    *,
    resolve_tier: Callable[[int], str] | None = None,
) -> tuple[bool, str]:
    """``(allowed, refusal_text)`` for one user and one feature.

    ``resolve_tier`` is the DI seam for tests: production passes nothing and
    the tier comes from the database, tests pass a pure lookup.
    """
    tier = (resolve_tier or _db_tier)(user_id)
    if allows(tier, feature):
        return True, ""
    return False, refusal(tier, feature)


def _db_tier(user_id: int) -> str:
    """Tier from the store — imported lazily so this module stays I/O-free."""
    from rugmunchbot import db

    return db.get_user_tier(user_id)

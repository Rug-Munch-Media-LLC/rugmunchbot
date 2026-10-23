"""Central referral helper — tiers, Jupiter accounts, disclosures.

Every bot surface resolves referral links through this package so the
standard/ultra Jupiter accounts and the disclosure live in ONE place.
"""

from __future__ import annotations

from ._disclosure import DISCLOSURE, disclosure_text
from ._jupiter import ACCOUNT_BY_TIER, jupiter_referral
from ._jupiter_api import PLATFORM_FEE_BPS, jupiter_swap_params
from ._polymarket import POLYMARKET_ENV_KEY, POLYMARKET_UNCONFIGURED, polymarket_referral
from ._render import referral_line
from ._tiers import STANDARD, TIER_BY_PURPOSE, ULTRA, resolve_tier
from ._types import Referral

__all__ = [
    "ACCOUNT_BY_TIER",
    "DISCLOSURE",
    "PLATFORM_FEE_BPS",
    "POLYMARKET_ENV_KEY",
    "POLYMARKET_UNCONFIGURED",
    "STANDARD",
    "TIER_BY_PURPOSE",
    "ULTRA",
    "Referral",
    "disclosure_text",
    "jupiter_referral",
    "jupiter_swap_params",
    "polymarket_referral",
    "referral_line",
    "resolve_tier",
]

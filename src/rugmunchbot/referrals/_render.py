"""Render referral lines for Telegram HTML surfaces."""

from __future__ import annotations

import html

from ._disclosure import disclosure_text
from ._jupiter import jupiter_referral
from ._tiers import ULTRA


def referral_line(
    token: str = "", chain: str = "solana", purpose: str = "scan"
) -> str:
    """One HTML line with the tier-appropriate Jupiter link + disclosure.

    Empty string when the surface has no referral (e.g. an EVM token), so
    callers can append unconditionally without branching.
    """
    ref = jupiter_referral(token=token, chain=chain, purpose=purpose)
    if not ref.available or not ref.url:
        return ""
    label = "Trade on Jupiter Ultra" if ref.tier == ULTRA else "Trade on Jupiter"
    url = html.escape(ref.url, quote=True)
    return f'\U0001f310 <a href="{url}">{label}</a> \u00b7 {disclosure_text(ref)}'

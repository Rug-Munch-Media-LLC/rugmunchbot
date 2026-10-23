"""GMGN perpetual-futures referral — the perps lane CTA.

GMGN perps (public beta) lists BTC, ETH, SOL and ZEC against USDC at
``https://gmgn.ai/perpetual/{SYMBOL}.PERP_USDC`` and binds the website
referral from ``?ref=<code>``.

Two hard rules, both enforced here so no caller can drift:

1. **No invented markets.** A symbol outside :data:`VERIFIED_PERP_MARKETS`
   returns ``url=None`` plus a reason — never a guessed GMGN path.
2. **No referral code in source.** The code is public marketing config, not
   a credential, so it is resolved from the environment (``RMI_REF_GMGN``
   full URL, or ``RMI_REF_GMGN_CODE`` slug). Unset = ``url=None`` + reason.

Perps rebate terms (10-30% of trading fees is the general programme copy;
perps terms are not separately documented) are NOT asserted anywhere — see
``docs/perps-lane.md``.
"""

from __future__ import annotations

import os
from urllib.parse import urlencode

from ._tiers import STANDARD
from ._types import Referral

GMGN_PERPS_PLATFORM = "gmgn_perps"
GMGN_PERPS_BASE = "https://gmgn.ai/perpetual"
GMGN_PERP_QUOTE = "PERP_USDC"
GMGN_REF_URL_KEY = "RMI_REF_GMGN"
GMGN_REF_CODE_KEY = "RMI_REF_GMGN_CODE"

#: Markets confirmed live on GMGN perps public beta (vs USDC). Anything not
#: listed here has no verified URL shape and must stay ``None``.
VERIFIED_PERP_MARKETS: frozenset[str] = frozenset({"BTC", "ETH", "SOL", "ZEC"})


def gmgn_perps_referral(symbol: str) -> Referral:
    """Resolve the GMGN perps CTA for *symbol*.

    Returns a :class:`Referral` whose ``url`` is ``None`` whenever the market
    is unverified or the code is unconfigured, with ``reason`` set so callers
    render ``null`` + reason instead of a fabricated link.
    """
    sym = (symbol or "").strip().upper()
    if sym not in VERIFIED_PERP_MARKETS:
        return Referral(
            GMGN_PERPS_PLATFORM,
            STANDARD,
            None,
            None,
            f"gmgn-perps:unverified-market:{sym or 'none'}",
        )
    url = (os.environ.get(GMGN_REF_URL_KEY) or "").strip()
    if url:
        if not url.startswith("https://"):
            return Referral(GMGN_PERPS_PLATFORM, STANDARD, None, None, "gmgn-perps:invalid-url")
        return Referral(GMGN_PERPS_PLATFORM, STANDARD, url, None)
    code = (os.environ.get(GMGN_REF_CODE_KEY) or "").strip()
    if not code:
        return Referral(
            GMGN_PERPS_PLATFORM, STANDARD, None, None, "gmgn-perps:referral-not-configured"
        )
    query = urlencode({"ref": code})
    return Referral(
        GMGN_PERPS_PLATFORM,
        STANDARD,
        f"{GMGN_PERPS_BASE}/{sym}.{GMGN_PERP_QUOTE}?{query}",
        code,
    )


__all__ = [
    "GMGN_PERPS_BASE",
    "GMGN_PERPS_PLATFORM",
    "GMGN_PERP_QUOTE",
    "GMGN_REF_CODE_KEY",
    "GMGN_REF_URL_KEY",
    "VERIFIED_PERP_MARKETS",
    "gmgn_perps_referral",
]

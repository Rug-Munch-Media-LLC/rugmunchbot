"""Jupiter referral — UI trade link plus the public referral account."""

from __future__ import annotations

from rugmunchbot.config import (
    DEX_REF_LINKS,
    JUPITER_FEE_ACCOUNT,
    JUPITER_ULTRA_ACCOUNT,
)

from ._tiers import STANDARD, ULTRA, resolve_tier
from ._types import Referral

JUPITER_PLATFORM = "jupiter"

#: Public on-chain fee recipients — never keys, safe to log and to send.
ACCOUNT_BY_TIER = {STANDARD: JUPITER_FEE_ACCOUNT, ULTRA: JUPITER_ULTRA_ACCOUNT}

#: Jupiter is Solana-only; generic cards post a real SOL→USDC swap page.
DEFAULT_SWAP_SYMBOL = "USDC"


def jupiter_referral(
    token: str = "", chain: str = "solana", purpose: str = "scan"
) -> Referral:
    """Resolve the Jupiter referral for one bot surface.

    ``account`` is the PUBLIC fee recipient a swap integration passes as
    ``referralAccount``; ``url`` is the UI trade link carrying the ``ref=``
    code. Non-Solana chains return ``available=False`` with a reason rather
    than a misleading Jupiter link.
    """
    tier = resolve_tier(purpose)
    chain_l = (chain or "solana").strip().lower()
    if chain_l != "solana":
        return Referral(
            JUPITER_PLATFORM, tier, None, None, f"jupiter:unsupported-chain:{chain_l}"
        )
    token_s = (token or "").strip() or DEFAULT_SWAP_SYMBOL
    url = DEX_REF_LINKS[JUPITER_PLATFORM]["url"].format(
        token=token_s, chain=chain_l, chain_id=1
    )
    return Referral(JUPITER_PLATFORM, tier, url, ACCOUNT_BY_TIER[tier])

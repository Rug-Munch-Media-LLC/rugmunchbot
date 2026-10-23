"""Jupiter swap request params — public referral account, never a key."""

from __future__ import annotations

from ._jupiter import jupiter_referral

#: Verified referral cut: 50 bps of the swap fee.
PLATFORM_FEE_BPS = 50


def jupiter_swap_params(
    token: str = "", chain: str = "solana", purpose: str = "deepscan"
) -> dict[str, object]:
    """Params a swap integration merges into a Jupiter order/swap request.

    Returns only the PUBLIC ``referralAccount`` and the fee bps — no key and
    no signing material ever leaves the secret store. Empty dict when the
    surface has no referral (e.g. a non-Solana chain) so callers can branch
    on truthiness.
    """
    ref = jupiter_referral(token=token, chain=chain, purpose=purpose)
    if not ref.available or not ref.account:
        return {}
    return {"referralAccount": ref.account, "platformFeeBps": PLATFORM_FEE_BPS}

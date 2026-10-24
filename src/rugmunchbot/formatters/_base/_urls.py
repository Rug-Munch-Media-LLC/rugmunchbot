"""Chain slugs and canonical DEX / explorer URLs.

One home for the chain-name translations every deep link needs: the scan
footer, the trade-button rows and the wallet report all resolve a chain
through these maps, so a new chain is added exactly once. The footer
composition lives in ``_links.py``; this module builds URLs only.
"""

from __future__ import annotations

import os

from rugmunchbot.partners._gmgn import gmgn_url as _gmgn_url

REF_CODES = {
    # GMGN attribution is the token slug (`rugmunch_<contract>`), built by
    # rugmunchbot.partners.gmgn_url — not a ?ref= param.
    "dextools": "",
}

DEXSCREENER_CHAINS = {
    "solana": "solana",
    "ethereum": "ethereum",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "tron": "tron",
    "polygon": "polygon",
    "avalanche": "avax",
    "optimism": "optimism",
}

GMGN_CHAINS = {
    "solana": "sol",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "avalanche": "avax",
    "fantom": "fantom",
    "tron": "tron",
    "robinhood": "robinhood",
}

DEXTOOLS_CHAINS = {
    "solana": "sol",
    "ethereum": "ether",
    "base": "base",
    "bsc": "bnb",
    "arbitrum": "arbitrum",
    "polygon": "polygon",
    "avalanche": "avalanche",
    "optimism": "optimism",
    "tron": "tron",
}

EXPLORER_DOMAINS = {
    "ethereum": "etherscan.io",
    "bsc": "bscscan.com",
    "polygon": "polygonscan.com",
    "arbitrum": "arbiscan.io",
    "optimism": "optimistic.etherscan.io",
    "base": "basescan.org",
    "avalanche": "snowtrace.io",
    "fantom": "ftmscan.com",
    "tron": "tronscan.org",
}

SOLANA_EXPLORER = "https://solscan.io/account/{address}"


def _ref_code(platform: str) -> str:
    """Resolve the owner's referral code for a platform.

    Env overrides (``RMI_REF_GMGN`` / ``RMI_REF_DEXTOOLS``) take precedence
    over ``REF_CODES``; an empty result omits the ``?ref=`` param.
    """
    env_name = f"RMI_REF_{platform.upper()}"
    return os.environ.get(env_name, REF_CODES.get(platform, ""))


def dexscreener_url(address: str, chain: str) -> str:
    """DexScreener token page URL — tap-to-open chart for any token."""
    slug = DEXSCREENER_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    return f"https://dexscreener.com/{slug}/{address}"


def gmgn_url(address: str, chain: str) -> str:
    """GMGN token page — thin positional delegate to ``partners.gmgn_url``.

    Kept as a named function because ~40 call sites pass positionally; the
    URL shape itself lives in exactly one place.
    """
    return _gmgn_url(token=address, chain=chain)


def dextools_url(address: str, chain: str) -> str:
    """DexTools pair page URL — chart explorer for any token.

    Appends the owner's ``?ref=`` param when a DexTools ref code is
    configured (``REF_CODES["dextools"]`` or ``RMI_REF_DEXTOOLS``); omits
    it when empty.
    """
    slug = DEXTOOLS_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    url = f"https://www.dextools.io/app/en/{slug}/pair-explorer/{address}"
    ref = _ref_code("dextools")
    if ref:
        url += f"?ref={ref}"
    return url


def explorer_url(address: str, chain: str) -> str:
    """Block explorer URL for a token/wallet on the given chain."""
    chain_l = (chain or "").lower()
    if chain_l == "solana":
        return SOLANA_EXPLORER.format(address=address)
    domain = EXPLORER_DOMAINS.get(chain_l, "etherscan.io")
    return f"https://{domain}/address/{address}"

#!/usr/bin/env python3
"""DEX referral links, Jupiter fee accounts, and chain id mapping."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._types import DexRefLink

# ══════════════════════════════════════════════
# DEX REFERRAL LINKS
# ══════════════════════════════════════════════
# These generate revenue when users trade through our links.
# Format: {platform: {url_template, chains, emoji, description}}
#
# LINK TAXONOMY (audited 2026-08-14 — every link must land users on the
# ACTUAL token's page, not a generic home page):
#   [chart]    token/chart page — `{token}` placeholder must be filled with
#              the scan address (GMGN / DexScreener / DexTools / Birdeye).
#   [swap]     token-specific swap page — acceptable, user lands on the exact
#              token pair (Jupiter / 1inch / PancakeSwap). Kept intentionally.
#   [signup]   Telegram bot signup — NO per-token URL exists; the ref code is
#              embedded in the `start=` payload (OdinBot / Banana Gun).
#   [home]     home page with ref param — NO verifiable per-token deep link
#              (Axiom / Padre). Kept + flagged [REVIEW]: if the platform adds
#              a token route (e.g. /trade/{addr}), switch the template to it.
# The `{token}` / `{chain}` / `{chain_id}` placeholders are filled by
# formatters/_base.py `_ref_platform_buttons` for every platform. Tests:
# tests/test_ref_links_chart.py locks in that the address always appears.


DEX_REF_LINKS: dict[str, DexRefLink] = {
    "jupiter": {
        "name": "Jupiter",
        "emoji": "🪐",
        "url": "https://jup.ag/swap/SOL-{token}?ref=rugmunch",  # [swap] token pair
        "chains": ["solana"],
        "description": "Best Solana DEX aggregator",
    },
    "photon": {
        "name": "Photon",
        "emoji": "⚡",
        "url": "https://photon-sol.tinyastro.io/en/lp/{token}?handle=rugmunch",  # [chart] token terminal
        "chains": ["solana", "ethereum", "base"],
        "description": "Fast Solana trading terminal",
    },
    "gmgn": {
        "name": "GMGN.ai",
        "emoji": "📊",
        # [home] chain-agnostic landing page — token-less content lanes
        # (news / alpha / scam alert) have no address to deep-link.
        "home": "https://gmgn.ai/?ref=rugmunch",
        # [chart] token chart page. NOTE: `sol` slug is hardcoded — correct for
        # Solana (the dominant use case); EVM scans land on GMGN's /sol/ route
        # which resolves by address. Flagged [REVIEW] if cross-chain routing
        # must be explicit (then use gmgn_url() slug mapping in _base.py).
        "url": "https://gmgn.ai/sol/token/{token}?ref=rugmunch",
        "chains": [
            "solana",
            "ethereum",
            "base",
            "bsc",
            "polygon",
            "arbitrum",
            "optimism",
            "avalanche",
            "fantom",
            "tron",
            "robinhood",
        ],
        "description": "Smart money tracking + trading",
    },
    "odinbot": {
        "name": "OdinBot",
        "emoji": "🧿",
        # [signup] Telegram bot — no per-token URL; ref rides in `start=`.
        "url": "https://t.me/OdinBot?start=x5pyi0",
        "chains": ["solana"],
        "description": "Solana trading bot",
    },
    "banana": {
        "name": "Banana Gun",
        "emoji": "🍌",
        # [signup] Telegram bot — no per-token URL; ref rides in `start=`.
        "url": "https://t.me/BananaGunBot?start=rugmunch",
        "chains": ["solana", "ethereum", "base"],
        "description": "Sniper + trading bot",
    },
    "axiom": {
        "name": "Axiom",
        "emoji": "🧮",
        # [home] no verifiable per-token deep link (SPA, no documented route).
        # Kept with ref — [REVIEW]: switch to a token page when one exists.
        "url": "https://axiom.trade/?ref=crmuncher",
        "chains": ["solana"],
        "description": "Smart-money trading terminal",
    },
    "padre": {
        "name": "Padre",
        "emoji": "⛪",
        # [home] redirects to axiom.trade/@handle — still a profile/home page,
        # no per-token deep link. Kept with ref — [REVIEW] if a token route
        # is added.
        "url": "https://padre.trade/?ref=crm",
        "chains": ["solana", "ethereum", "base"],
        "description": "DeFi trading bot",
    },
    "birdeye": {
        "name": "Birdeye",
        "emoji": "🦅",
        "url": "https://birdeye.so/token/{token}",  # [chart] token chart page (no ref program)
        "chains": ["solana", "ethereum", "base", "bsc", "arbitrum"],
        "description": "Token analytics & charts",
    },
    "oneinch": {
        "name": "1inch",
        "emoji": "🦄",
        "url": "https://1inch.com/swap?src={chain_id}:ETH&dst={chain_id}:{token}",  # [swap] token pair (no ref program)
        "chains": ["ethereum", "base", "arbitrum", "bsc", "polygon", "avalanche"],
        "description": "EVM DEX aggregator",
    },
    "pancakeswap": {
        "name": "PancakeSwap",
        "emoji": "🥞",
        "url": "https://pancakeswap.finance/swap?inputCurrency=BNB&outputCurrency={token}",  # [swap] token pair (no ref program)
        "chains": ["bsc", "ethereum", "arbitrum", "base"],
        "description": "BSC's #1 DEX",
    },
}

# ══════════════════════════════════════════════
# JUPITER REFERRAL FEE ACCOUNTS (programmatic swap execution)
# ══════════════════════════════════════════════
# On-chain fee-recipient wallets for the Jupiter referral program. These are
# the PROGRAMMATIC counterpart to the ``?ref=rugmunch`` UI-attribution links
# in ``DEX_REF_LINKS["jupiter"]`` above (deep links only attribute the click;
# they never move funds). Any swap executed through Jupiter's program with
# ``feeAccount`` / ``platformFeeBps`` set routes a share of the swap fee to
# these wallets.
#
# Where they are used:
#   - walletpress plugin swap routing (walletpress repo) — the Solana swap
#     path sets ``feeAccount`` = ``JUPITER_FEE_ACCOUNT`` on the Jupiter v6
#     swap instruction so every executed swap pays the RugMunch referral fee.
#   - Future Jupiter Ultra / swap-execution paths in THIS repo must reuse
#     these constants instead of re-pasting the wallet strings.
JUPITER_FEE_ACCOUNT = (
    "8ouwE3sx6im8pDVwKRZ6n2QNjZwLmrBQ1XTiHhsXge39"  # Jupiter v6 Swap fee recipient
)
JUPITER_ULTRA_ACCOUNT = (
    "Dkrbs5cWeaVspAbCHUM4ds6kJdZeuJkgmaje9w9Urtfh"  # Jupiter Ultra fee recipient
)

# Chain ID mapping for 1inch
CHAIN_IDS = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "avalanche": 43114,
    "optimism": 10,
    "base": 8453,
    "fantom": 250,
}

"""Canonical source links — footer, referral codes, DEX/explorer URLs."""

from __future__ import annotations

import html
import os

from telegram import InlineKeyboardButton

from rugmunchbot.config import (
    DEX_REF_LINKS,
    TELEGRAM_CHANNEL,
    WEB_APP_URL,
)
from rugmunchbot.formatters._base._primitives import sep
from rugmunchbot.partners import select_partners
from rugmunchbot.partners._gmgn import gmgn_url as _gmgn_url
from rugmunchbot.referrals import DISCLOSURE, referral_line

REF_CODES = {
    # GMGN attribution is the token slug (`rugmunch_<contract>`), built by
    # rugmunchbot.partners.gmgn_url — not a ?ref= param.
    "dextools": "",
}


DEX_BUTTON_PRIORITY = (
    "gmgn",
    "jupiter",
    "photon",
    "banana",
    "odinbot",
    "axiom",
    "padre",
    "birdeye",
    "oneinch",
    "pancakeswap",
)

DEX_BUTTONS_PER_ROW = 2

UNKNOWN_CHAIN_MINIMAL = ("odinbot", "banana")


_NAME_OVERRIDES = {"gmgn": "GMGN.ai"}


def _name(platform: str) -> str:
    """Display label for a partner key (config name, with GMGN's dot form)."""
    if platform in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[platform]
    dex = DEX_REF_LINKS.get(platform)
    return str(dex["name"]) if dex else platform.title()


def _ref_code(platform: str) -> str:
    """Resolve the owner's referral code for a platform.

    Env overrides (``RMI_REF_GMGN`` / ``RMI_REF_DEXTOOLS``) take precedence
    over ``REF_CODES``; an empty result omits the ``?ref=`` param.
    """
    env_name = f"RMI_REF_{platform.upper()}"
    return os.environ.get(env_name, REF_CODES.get(platform, ""))


def footer_links(address: str = "", chain: str = "", *, purpose: str = "scan") -> str:
    """Bot footer with links and ONE referral disclosure.

    With a token: at most THREE referral links from the partner selector
    (Jupiter + GMGN first, chain-filtered, never invented) plus the
    non-referral chart utilities (DexScreener, DexTools, RugCharts, RugMaps)
    on one line. Without one: a tier-aware Jupiter line. ``purpose`` picks
    the tier (standard vs ultra). HTML-safe (``<a>`` only)."""
    chain_l = (chain or "solana").lower()
    bot = f'\U0001f50d Rug Munch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'
    if not address:
        ref = referral_line(chain=chain_l, purpose=purpose)
        return f"\n{sep()}\n{ref}\n{bot}" if ref else f"\n{sep()}\n{bot}"
    # Ref attribution comes from ONE place: the partner selector caps the
    # strip at 3 links with Jupiter + GMGN leading the smart-money lane, so a
    # scan footer can never drift into an 8-link referral wall again.
    refs = [ref for ref in select_partners("smart_money", chain=chain_l, token=address) if ref.url]
    ref_parts = [
        f'<a href="{html.escape(str(ref.url), quote=True)}">{html.escape(_name(ref.platform))}</a>'
        for ref in refs
    ]
    ref_parts += [
        f'<a href="{html.escape(dexscreener_url(address, chain_l), quote=True)}">DexScreener</a>',
        f'<a href="{html.escape(dextools_url(address, chain_l), quote=True)}">DexTools</a>',
    ]
    charts = (
        f'\U0001f4c8 <a href="https://rugmunch.io/rugcharts?token={address}&chain={chain_l}">RugCharts</a>'
        f' | \U0001f5fa\ufe0f <a href="https://rugmunch.io/rugmaps?token={address}&chain={chain_l}">RugMaps</a>'
    )
    lines = [f"{' | '.join(ref_parts)} | {charts}"]
    # ONE disclosure line per post, and only when a referral link is present.
    if refs:
        lines.append(DISCLOSURE)
    return f"\n{sep()}\n" + "\n".join(lines) + f"\n{bot}"


def web_scan_button(address: str, chain: str = "") -> InlineKeyboardButton:
    url = f"{WEB_APP_URL}?token={address}"
    if chain:
        url += f"&chain={chain}"
    return InlineKeyboardButton("\U0001f310 Full Report on RugMunch.io", url=url)


#: Cross-channel promotion block for scan posts (drives adoption). The
#: canonical handles mirror ``channels_registry`` public handles — keep in
#: sync when a channel is added/renamed.
_SHILL_CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("\U0001f50d", "https://t.me/RugMunchBot", "@RugMunchBot"),
    ("\U0001f310", "https://rugmunch.io", "rugmunch.io"),
    ("\U0001f52c", "https://t.me/rmiscans", "@rmiscans"),
    ("\U0001f6a8", "https://t.me/rmialerts", "@rmialerts"),
    ("\U0001f4f0", "https://t.me/rmicryptonews", "@rmicryptonews"),
    ("\U0001f40b", "https://t.me/rmialpha", "@rmialpha"),
    ("\U0001f4e1", "https://t.me/cryptorugmuncher", "@cryptorugmuncher"),
)


def scan_shill_footer(purpose: str = "scan", chain: str = "") -> str:
    """Shill footer for scan posts: the bot, the site + every RugMunch channel.

    Rendered at the end of every scan report so readers reach the product from
    anywhere; the trailing referral line is tier-aware (``purpose``) and the
    text is HTML-safe for Telegram (``<a>`` only).
    """
    lines = [f"\n{sep()}\n\U0001f4e2 <b>RugMunch Intelligence</b>"]
    for emoji, url, label in _SHILL_CHANNELS:
        lines.append(f'{emoji} <a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
    ref = referral_line(chain=chain, purpose=purpose)
    if ref:
        lines.append(ref)
    return "\n".join(lines)


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


def dexscreener_url(address: str, chain: str) -> str:
    """DexScreener token page URL — tap-to-open chart for any token."""
    slug = DEXSCREENER_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    return f"https://dexscreener.com/{slug}/{address}"


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


def gmgn_url(address: str, chain: str) -> str:
    """GMGN token page — thin positional delegate to ``partners.gmgn_url``.

    Kept as a named function because ~40 call sites pass positionally; the
    URL shape itself lives in exactly one place.
    """
    return _gmgn_url(token=address, chain=chain)


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
        return f"https://solscan.io/account/{address}"
    domains = {
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
    domain = domains.get(chain_l, "etherscan.io")
    return f"https://{domain}/address/{address}"

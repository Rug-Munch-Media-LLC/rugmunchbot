"""Canonical source links — footer, referral codes, DEX/explorer URLs."""

from __future__ import annotations

import html
import os

from telegram import InlineKeyboardButton

from rugmunchbot.config import (
    CHAIN_IDS,
    DEX_REF_LINKS,
    TELEGRAM_CHANNEL,
    WEB_APP_URL,
)
from rugmunchbot.formatters._base._primitives import sep

REF_CODES = {
    # GMGN already ships with `ref=rugmunch` in config.DEX_REF_LINKS — keep
    # that attribution here so the footer GMGN link matches the keyboard.
    # Change to "" (or set RMI_REF_GMGN) to drop the param.
    "gmgn": "rugmunch",
    # fill in your ref code
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


def _ref_code(platform: str) -> str:
    """Resolve the owner's referral code for a platform.

    Env overrides (``RMI_REF_GMGN`` / ``RMI_REF_DEXTOOLS``) take precedence
    over ``REF_CODES``; an empty result omits the ``?ref=`` param.
    """
    env_name = f"RMI_REF_{platform.upper()}"
    return os.environ.get(env_name, REF_CODES.get(platform, ""))


def footer_links(address: str = "", chain: str = "") -> str:
    """Bot footer with links. When a token is provided, includes the full
    referral trade-link set (GMGN · Jupiter · Photon · Birdeye · 1inch ·
    PancakeSwap · DexScreener · DexTools) plus RugCharts → RugMaps as a
    clean SINGLE tappable-text line — every platform that embeds the token
    address carries our ``?ref=`` code (see ``DEX_REF_LINKS`` / ``REF_CODES``)
    so the report ends with one links footer instead of a button stack.
    HTML-safe for Telegram (only ``<a>`` tags)."""
    if address:
        chain_l = (chain or "").lower()
        # Referral platforms that embed the token address (chain-filtered).
        ref_parts: list[str] = []
        for key in DEX_BUTTON_PRIORITY:
            dex = DEX_REF_LINKS.get(key)
            if not dex or chain_l not in dex.get("chains", []):
                continue
            if "{token}" not in dex["url"]:
                continue  # signup/home bots have no per-token URL — skip in text footer
            url = dex["url"].format(
                token=address,
                chain=chain_l,
                chain_id=CHAIN_IDS.get(chain_l, 1),
            )
            ref_parts.append(
                f'<a href="{html.escape(url, quote=True)}">{html.escape(dex["name"])}</a>'
            )
        # Chart links (DexScreener no-ref + DexTools ref when configured).
        ref_parts.append(
            f'<a href="{html.escape(dexscreener_url(address, chain_l), quote=True)}">DexScreener</a>'
        )
        ref_parts.append(
            f'<a href="{html.escape(dextools_url(address, chain_l), quote=True)}">DexTools</a>'
        )
        trade = " | ".join(ref_parts)
        charts = (
            f'\U0001f4c8 <a href="https://rugmunch.io/rugcharts?token={address}&chain={chain_l}">RugCharts</a>'
            f' | \U0001f5fa\ufe0f <a href="https://rugmunch.io/rugmaps?token={address}&chain={chain_l}">RugMaps</a>'
        )
        return (
            f"\n{sep()}\n{trade} | {charts}\n"
            f'\U0001f50d Rug Munch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'
        )
    return f'\n{sep()}\n\U0001f50d Rug Munch Bot | <a href="{TELEGRAM_CHANNEL}">/help</a> | @RugMunchBot'


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


def scan_shill_footer() -> str:
    """Shill footer for scan posts: the bot, the site + every RugMunch channel.

    Rendered at the end of every scan report so readers can reach the product
    from anywhere (t.me/rugmunchbot is the bot handle; the rest are the
    public Telegram channels). HTML-safe for Telegram (``<a>`` only).
    """
    lines = [f"\n{sep()}\n\U0001f4e2 <b>RugMunch Intelligence</b>"]
    for emoji, url, label in _SHILL_CHANNELS:
        lines.append(f'{emoji} <a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
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
    """GMGN token page URL — smart-money tracking + trading terminal.

    Appends the owner's ``?ref=`` param when a GMGN ref code is configured
    (``REF_CODES["gmgn"]`` or ``RMI_REF_GMGN``); omits it when empty.
    """
    slug = GMGN_CHAINS.get((chain or "").lower(), (chain or "solana").lower())
    url = f"https://gmgn.ai/{slug}/token/{address}"
    ref = _ref_code("gmgn")
    if ref:
        url += f"?ref={ref}"
    return url


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

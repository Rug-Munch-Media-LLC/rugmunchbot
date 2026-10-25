"""Canonical source links — scan footer, referral disclosure, shill block.

URL shapes and chain slugs live in ``_urls.py`` and are re-exported here so
existing ``formatters._base._links`` imports keep working unchanged.
"""

from __future__ import annotations

import html

from telegram import InlineKeyboardButton

from rugmunchbot.config import (
    DEX_REF_LINKS,
    TELEGRAM_CHANNEL,
    WEB_APP_URL,
)
from rugmunchbot.formatters._base._primitives import sep
from rugmunchbot.formatters._base._urls import (
    DEXSCREENER_CHAINS as DEXSCREENER_CHAINS,
    DEXTOOLS_CHAINS as DEXTOOLS_CHAINS,
    GMGN_CHAINS as GMGN_CHAINS,
    REF_CODES as REF_CODES,
    _ref_code as _ref_code,
    dexscreener_url as dexscreener_url,
    dextools_url as dextools_url,
    explorer_url as explorer_url,
    gmgn_url as gmgn_url,
)
from rugmunchbot.partners import select_partners
from rugmunchbot.referrals import DISCLOSURE, referral_line

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

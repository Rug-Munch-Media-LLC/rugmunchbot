#!/usr/bin/env python3
"""Tap-to-action buttons for token displays."""

from __future__ import annotations

from telegram import InlineKeyboardButton

from rugmunchbot.formatters import dexscreener_url, explorer_url


def token_buttons(
    address: str, chain: str, *, include_scan: bool = True
) -> list[InlineKeyboardButton]:
    """Tap-to-action row for any token display: [📊 Scan] [📈 Chart] [🔍 Explorer].

    Scan re-runs the security scan via the ``scan_`` callback; Chart opens
    the DexScreener page; Explorer opens the block explorer (Solscan /
    Etherscan-family by chain). Full addresses stay inside the message text
    in ``<code>`` blocks — button labels only ever carry the short form.
    """
    buttons: list[InlineKeyboardButton] = []
    if include_scan:
        buttons.append(
            InlineKeyboardButton("\U0001f4ca Scan", callback_data=f"scan_{address}_{chain}")
        )
    buttons.append(InlineKeyboardButton("\U0001f4c8 Chart", url=dexscreener_url(address, chain)))
    buttons.append(InlineKeyboardButton("\U0001f50d Explorer", url=explorer_url(address, chain)))
    return buttons

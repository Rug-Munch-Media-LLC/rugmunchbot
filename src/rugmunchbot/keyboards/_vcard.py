#!/usr/bin/env python3
"""Verdict-card keyboard — three VISIBLE buttons, nothing hidden.

The competitor scan card ships seven buttons that render as blank space on
clients without the custom glyphs, so the whole action row is undiscoverable.
This keyboard is deliberately tiny and every label is plain text + one
standard emoji:

  * ``Refresh``     — re-runs the scan and edits the SAME message in place;
  * ``Full report`` — swaps the card for the full legacy report in place;
  * ``First buyers``— swaps the card for the top-10 first-buyer list in place;
  * ``DexScreener`` — a real URL button (no callback), so it always renders.

``callback_data`` carries only the address (Telegram caps it at 64 bytes and
a Solana address alone is 44); the chain is re-derived with ``detect_chain``
in the callback handler.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

__all__ = ["vcard_kb"]


def vcard_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Refresh / full-report / DexScreener keyboard for one scan card.

    The DexScreener URL helper is imported lazily: ``formatters`` reaches back
    into the keyboard package during its own import, so a module-level import
    here would close the cycle.
    """
    from rugmunchbot.formatters._base import dexscreener_url

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f504 Refresh", callback_data=f"vcard_refresh_{address}"
                ),
                InlineKeyboardButton(
                    "\U0001f4ca Full report", callback_data=f"vcard_full_{address}"
                ),
            ],
            [InlineKeyboardButton("\U0001f9ea First buyers", callback_data=f"vcard_fb_{address}")],
            [InlineKeyboardButton("\U0001f50e DexScreener", url=dexscreener_url(address, chain))],
        ]
    )

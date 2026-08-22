#!/usr/bin/env python3
"""Post-scan / post-analysis result keyboards."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import WEBSITE_URL
from rugmunchbot.formatters import web_scan_button


def scan_actions_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Post-scan action row (v6): 📊 View Chart · 🗺️ View RugMap · 📤 Share · 🔍 Deep Scan."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f4c8 View Chart", callback_data=f"chart_{address}_{chain}"
                ),
                InlineKeyboardButton(
                    "\U0001f5fa\ufe0f View RugMap", callback_data=f"rugmap_{address}_{chain}"
                ),
            ],
            [
                InlineKeyboardButton("\U0001f4e4 Share", url=_share_token_url(address, chain)),
                InlineKeyboardButton(
                    "\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f6f0\ufe0f Monitor (250\u2605)",
                    callback_data=f"monitor_{address}_{chain}",
                ),
                InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main"),
            ],
        ]
    )


def scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Post-scan action keyboard (v7 — decluttered 2026-08-16).

    Reference links (GMGN/DexScreener/DexTools/RugCharts/RugMaps) render as
    tappable text in the report footer (``footer_links``) — no duplicate
    link-button rows here. What remains are ACTION buttons: one tap runs a
    real flow (holders/research/wallet/TA/alert/watch/deep/share).
    """
    rows = [
        [web_scan_button(address, chain)],
        [
            InlineKeyboardButton("\U0001f465 Holders", callback_data=f"holders_{address}_{chain}"),
            InlineKeyboardButton(
                "\U0001f50d Research", callback_data=f"research_{address}_{chain}"
            ),
        ],
        [
            InlineKeyboardButton(
                "\U0001f9db Wallet Check", callback_data=f"wallet_{address}_{chain}"
            ),
            InlineKeyboardButton("\U0001f4ca TA", callback_data=f"ta_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton(
                "\U0001f514 Price Alert", callback_data=f"alert_{address}_{chain}"
            ),
            InlineKeyboardButton("\u2b50 Watch", callback_data=f"watch_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton(
                "\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"
            ),
            InlineKeyboardButton("\U0001f4e4 Share", callback_data=f"share_{address}_{chain}"),
        ],
        [
            InlineKeyboardButton(
                "\U0001f6f0\ufe0f Monitor (250\u2605)", callback_data=f"monitor_{address}_{chain}"
            ),
        ],
        [InlineKeyboardButton("\u2b50 Unlock Premium Features", callback_data="menu_pricing")],
        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
    ]
    return InlineKeyboardMarkup(rows)


def holders_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton(
                    "\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}"
                ),
                InlineKeyboardButton(
                    "\U0001f9db Wallet", callback_data=f"wallet_{address}_{chain}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f514 Set Alert", callback_data=f"alert_{address}_{chain}"
                ),
                InlineKeyboardButton(
                    "\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"
                ),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def trace_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton(
                    "\U0001f9db Wallet Analysis", callback_data=f"wallet_{address}_{chain}"
                ),
                InlineKeyboardButton(
                    "\U0001f50d Scan Token", callback_data=f"scan_{address}_{chain}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"
                ),
                InlineKeyboardButton("\U0001f440 Watch", callback_data=f"watch_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def investigate_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f310 Full Report", url=f"{WEBSITE_URL}/markets/token/{address}"
                )
            ],
            [
                InlineKeyboardButton(
                    "\U0001f9db Wallet", callback_data=f"wallet_{address}_{chain}"
                ),
                InlineKeyboardButton("👥 Holders", callback_data=f"holders_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f50d Scan", callback_data=f"scan_{address}_{chain}"),
                InlineKeyboardButton("🔍 Trace", callback_data=f"trace_{address}_{chain}"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def _share_token_url(address: str, chain: str) -> str:
    """t.me/share/url deep link advertising a scanned token (v6)."""
    from urllib.parse import quote

    return (
        "https://t.me/share/url?url="
        + quote(f"{WEBSITE_URL}/scanner?token={address}&chain={chain}")
        + "&text="
        + quote("Scanned with Rug Munch — check this token before you buy!")
    )


def deep_scan_result_kb(address: str, chain: str) -> InlineKeyboardMarkup:
    """Extended keyboard shown after scan results with all premium actions."""
    return InlineKeyboardMarkup(
        [
            [web_scan_button(address, chain)],
            [
                InlineKeyboardButton("👥 Holders", callback_data=f"holders_{address}_{chain}"),
                InlineKeyboardButton("🔍 Trace", callback_data=f"trace_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f50d Research", callback_data=f"research_{address}_{chain}"
                ),
                InlineKeyboardButton(
                    "\U0001f4c8 Sentiment", callback_data=f"sentiment_{address}_{chain}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f9db Wallet Check", callback_data=f"wallet_{address}_{chain}"
                ),
                InlineKeyboardButton("\U0001f4ca TA", callback_data=f"ta_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f514 Set Alert", callback_data=f"alert_{address}_{chain}"
                ),
                InlineKeyboardButton("\u2b50 Watch", callback_data=f"watch_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("\U0001f4c8 Chart", callback_data=f"chart_{address}_{chain}"),
                InlineKeyboardButton(
                    "\U0001f5fa\ufe0f RugMap", callback_data=f"rugmap_{address}_{chain}"
                ),
                InlineKeyboardButton("\U0001f4e4 Share", url=_share_token_url(address, chain)),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"
                ),
                InlineKeyboardButton(
                    "\U0001f575\ufe0f Investigate", callback_data=f"investigate_{address}_{chain}"
                ),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )

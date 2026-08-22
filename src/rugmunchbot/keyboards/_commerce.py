#!/usr/bin/env python3
"""Commerce keyboards — pricing, top-ups, Stars packs, Scam School."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import (
    BUY_STARS_PACKS,
    SCAM_SCHOOL_TOPICS,
    SUPPORT_EMAIL,
    TIERS,
    TOP_UP_PACKS,
    WEBSITE_URL,
)


def pricing_kb() -> InlineKeyboardMarkup:
    """Premium pricing buttons — Stars-based (no fake $/mo framing).

    Tiers are priced in Telegram Stars (their real ``sub_`` invoice price,
    ``price_stars``); the working on-demand premium features (Monitor / Deep
    Scan) are promoted on top and reached from scan results + /monitor.
    """
    rows = [
        [
            InlineKeyboardButton(
                "\U0001f6f0\ufe0f Monitor (250\u2605/30d) \u2014 /monitor <addr>",
                callback_data="menu_monitor_howto",
            )
        ],
        [InlineKeyboardButton("\u2b50 Buy Stars", callback_data="menu_buystars")],
    ]
    for key in ["scout", "hunter", "pro"]:
        t = TIERS[key]
        rows.append(
            [
                InlineKeyboardButton(
                    f"{t['emoji']} {t['name']} - {t['price_stars']:,}\u2605/30d",
                    callback_data=f"sub_{key}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton("\U0001f310 Compare Plans on Website", url=f"{WEBSITE_URL}/pricing")]
    )
    rows.append([InlineKeyboardButton("\U0001f4e7 Enterprise", url=f"mailto:{SUPPORT_EMAIL}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def topup_kb() -> InlineKeyboardMarkup:
    rows = []
    rows.append([InlineKeyboardButton("\U0001f52c Scan Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "scans":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\U0001f916 AI Message Packs", callback_data="noop")])
    for key, pack in TOP_UP_PACKS.items():
        if pack["type"] == "ai_msgs":
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                        callback_data=f"topup_{key}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\u2b50 Buy Stars", callback_data="menu_buystars")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def buy_stars_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, pack in BUY_STARS_PACKS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    f"{pack['emoji']} {pack['name']} - \u2b50{pack['stars']}",
                    callback_data=f"buystars_{key}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)


def scamschool_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, topic in SCAM_SCHOOL_TOPICS.items():
        rows.append([InlineKeyboardButton(topic["title"], callback_data=f"scam_{key}")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Back", callback_data="menu_main")])
    return InlineKeyboardMarkup(rows)

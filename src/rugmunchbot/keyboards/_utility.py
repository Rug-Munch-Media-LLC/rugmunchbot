#!/usr/bin/env python3
"""Utility keyboards — navigation, socials, paywall, upgrade nudges, API."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from rugmunchbot.config import TELEGRAM_CHANNEL, TELEGRAM_GROUP, TWITTER_URL, WEBSITE_URL


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")]]
    )


def social_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f310 Website", url=WEBSITE_URL),
                InlineKeyboardButton("\U0001d54f Twitter", url=TWITTER_URL),
            ],
            [
                InlineKeyboardButton("\U0001f4e2 Alerts", url=TELEGRAM_CHANNEL),
                InlineKeyboardButton("\U0001f4ac Chat", url=TELEGRAM_GROUP),
            ],
        ]
    )


def paywall_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("\u2b50 View Plans", callback_data="menu_pricing")],
            [InlineKeyboardButton("\U0001f50b Buy Top-Up", callback_data="menu_topup")],
            [InlineKeyboardButton("\U0001f310 rugmunch.io", url=WEBSITE_URL)],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
        ]
    )


def upgrade_nudge_kb(current_tier: str) -> InlineKeyboardMarkup:
    """Keyboard shown when user hits usage limits."""
    buttons = []
    if current_tier == "free":
        buttons.append([InlineKeyboardButton("🔍 Scout - 2,999★/30d", callback_data="sub_scout")])
        buttons.append([InlineKeyboardButton("🎯 Hunter - 4,999★/30d", callback_data="sub_hunter")])
    elif current_tier == "scout":
        buttons.append([InlineKeyboardButton("🎯 Hunter - 4,999★/30d", callback_data="sub_hunter")])
        buttons.append([InlineKeyboardButton("👑 Pro - 9,999★/30d", callback_data="sub_pro")])
    elif current_tier == "hunter":
        buttons.append([InlineKeyboardButton("👑 Pro - 9,999★/30d", callback_data="sub_pro")])
    buttons.append([InlineKeyboardButton("💳 View All Plans", callback_data="menu_pricing")])
    buttons.append([InlineKeyboardButton("🔄 Buy Top-Up", callback_data="menu_topup")])
    buttons.append([InlineKeyboardButton("🔙 Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)


def api_kb(has_token: bool = False) -> InlineKeyboardMarkup:
    """Keyboard for /api command."""
    buttons = [
        [InlineKeyboardButton("📖 API Documentation", url=f"{WEBSITE_URL}/docs")],
    ]
    if has_token:
        buttons.append([InlineKeyboardButton("🔄 Revoke Token", callback_data="api_revoke")])
    buttons.append([InlineKeyboardButton("🔙 Menu", callback_data="menu_main")])
    return InlineKeyboardMarkup(buttons)

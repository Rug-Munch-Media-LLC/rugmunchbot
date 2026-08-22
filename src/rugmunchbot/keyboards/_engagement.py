#!/usr/bin/env python3
"""Engagement keyboards (v11 — additive, 2026-08-14).

First-run quick-start + returning-user daily hook. Every callback reuses an
EXISTING surface (run_scan / run_trending / run_market / menu_cat_help) —
zero callback-handler changes needed.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def quick_start_kb() -> InlineKeyboardMarkup:
    """First-run quick-start: the 3 core actions + a "More" button → /menu.

    Scan / Price / Ask AI mirror the example actions in the onboarding
    welcome message; the final "More" row opens the full /menu capability
    atlas via the ``menu_full`` callback (renders the existing ``MENU_TEXT``).
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f50d Scan", callback_data="run_scan"),
                InlineKeyboardButton("\U0001f4b0 Price", callback_data="run_price"),
            ],
            [InlineKeyboardButton("\U0001f4ac Ask AI", callback_data="run_chat")],
            [InlineKeyboardButton("\U0001f4d6 More \u25be", callback_data="menu_full")],
        ]
    )


def daily_hook_kb() -> InlineKeyboardMarkup:
    """Returning-user daily hook: one-tap "what's hot right now" + market pulse.

    A lightweight daily-digest quick action (button, not a scheduler) that
    triggers the existing trending / market tools for instant value on return.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "\U0001f525 What's hot right now", callback_data="run_trending"
                ),
                InlineKeyboardButton("\U0001f4ca Market pulse", callback_data="run_market"),
            ]
        ]
    )


def merge_menu_kb(
    main_kb: InlineKeyboardMarkup, extra_kb: InlineKeyboardMarkup
) -> InlineKeyboardMarkup:
    """Append ``extra_kb`` rows to ``main_kb`` (additive merge, no mutation).

    Keeps existing buttons intact and just adds the engagement rows on top —
    used by /start to attach the daily hook to the welcome-menu keyboard.
    """
    rows = [list(row) for row in main_kb.inline_keyboard]
    rows.extend(list(row) for row in extra_kb.inline_keyboard)
    return InlineKeyboardMarkup(rows)

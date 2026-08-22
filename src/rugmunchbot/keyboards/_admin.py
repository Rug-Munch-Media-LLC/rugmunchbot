#!/usr/bin/env python3
"""Owner admin dashboard keyboards."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def admin_kb() -> InlineKeyboardMarkup:
    """Owner admin dashboard keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\U0001f4ca Stats", callback_data="admin_stats"),
                InlineKeyboardButton("\U0001f465 User Lookup", callback_data="admin_user_prompt"),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f4e2 Broadcast", callback_data="admin_broadcast_prompt"
                ),
                InlineKeyboardButton("\U0001f4dd Logs", callback_data="admin_logs"),
            ],
            [
                InlineKeyboardButton(
                    "\U0001f6e1\ufe0f Bot ON/OFF", callback_data="admin_toggle_bot"
                ),
                InlineKeyboardButton("\U0001f4ac Chat ON/OFF", callback_data="admin_toggle_chat"),
            ],
            [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
        ]
    )


def owner_admin_reply_kb() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard pinned to the input field for the owner (v7).

    Shows the admin commands as tappable buttons in every private chat with
    the bot. The button labels are plain text; the message handler routes
    them to the matching /admin_* command (see handlers/message.py).
    """
    return ReplyKeyboardMarkup(
        [
            [
                KeyboardButton("\U0001f6e1\ufe0f Admin Dashboard"),
                KeyboardButton("\U0001f4ca Stats"),
            ],
            [
                KeyboardButton("\U0001f4e2 Broadcast"),
                KeyboardButton("\U0001f465 User Lookup"),
            ],
            [
                KeyboardButton("\U0001f504 Bot ON/OFF"),
                KeyboardButton("\U0001f4ac Chat ON/OFF"),
            ],
            [KeyboardButton("\U0001f3d9\ufe0f Main Menu")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Rug Munch Admin console",
    )


def admin_confirm_kb(action: str, payload: str = "") -> InlineKeyboardMarkup:
    """Generic confirm/cancel keyboard for destructive admin actions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("\u2705 Confirm", callback_data=f"admin_confirm_{action}"),
                InlineKeyboardButton("\u274c Cancel", callback_data="admin_cancel"),
            ]
        ]
    )

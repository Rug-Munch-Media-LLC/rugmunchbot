"""RugMunchBot — first-run onboarding + engagement hooks (additive).

New-user flow: when a brand-new user sends /start, ``cmd_start`` detects the
first run and shows a punchy welcome with a 2-3 button quick-start keyboard
instead of the full menu card. Every quick-start button reuses an EXISTING
callback surface (``run_scan`` / ``run_trending`` / ``menu_cat_help``), so no
callback-handler changes are needed and no new scheduler is introduced.

Returning users get the standard welcome card plus the daily hook
(``daily_hook_kb`` — "what's hot right now" + market pulse) merged into the
menu keyboard. Both buttons also reuse existing ``run_*`` callbacks.

First-run detection is a heuristic that needs NO schema migration: a brand-new
``/start`` creates the ``users`` row in the same request (``db.get_or_create_user``),
so ``created_at`` is within a few seconds of "now" and all usage counters are
zero. Returning users have an old ``created_at`` and/or usage on record.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from rugmunchbot.handlers._engagement import channels_menu_text
from rugmunchbot.keyboards import quick_start_kb

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telegram import Update
    from telegram.ext import ContextTypes

#: A row is "first run" when created within this many seconds AND unused.
FIRST_RUN_WINDOW_SECONDS: int = 60

#: Quick-start actions shown to a brand-new user. Each callback is an EXISTING
#: surface handled by ``handlers/callback`` — no new handler registration.
ONBOARDING_QUICK_START: tuple[tuple[str, str], ...] = (
    ("\U0001f50d Scan a token", "run_scan"),
    ("\U0001f525 What's trending", "run_trending"),
    ("\U0001f4ac What can you do?", "menu_cat_help"),
)

#: Daily hook actions merged into the returning-user menu. Existing callbacks:
#: ``run_trending`` (live trending feed) and ``run_market`` (live market data).
DAILY_HOOK_ACTIONS: tuple[tuple[str, str], ...] = (
    ("\U0001f525 What's hot right now", "run_trending"),
    ("\U0001f4ca Market pulse", "run_market"),
)


def _usage_count(user_row: Mapping[str, object], key: str) -> int:
    """Read an integer usage counter off a SQLite row dict (strict-safe)."""
    value = user_row.get(key)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def is_first_run(user_row: Mapping[str, object]) -> bool:
    """True when ``user_row`` (from ``db.get_or_create_user``) is a fresh user.

    Conservative heuristic — never misfires for active users:
      * the row must have been created within the last ``FIRST_RUN_WINDOW_SECONDS``
        (a brand-new /start creates the row in this same request), AND
      * the user must have zero recorded usage (``total_scans`` /
        ``total_ai_msgs`` both 0).
    """
    created = user_row.get("created_at")
    if not isinstance(created, (int, float)):
        return False
    if int(time.time()) - int(created) > FIRST_RUN_WINDOW_SECONDS:
        return False
    if _usage_count(user_row, "total_scans") > 0:
        return False
    return not _usage_count(user_row, "total_ai_msgs") > 0


def welcome_message(name: str = "") -> str:
    """Punchy first-run welcome: greeting, value pitch, try-me one-liner.

    HTML-safe for Telegram: only balanced ``<b>`` / ``<i>`` tags, no raw
    markdown, no ALL-CAPS, no emoji spam. The required try-me line is kept
    to exactly one line so it is scannable in a chat.
    """
    greeting = f"Hey {name} \U0001f44b" if name else "Hey \U0001f44b"
    return (
        f"{greeting} <b>welcome to Rug Munch</b> \u2014 the crypto "
        f"scam-detection copilot.\n\n"
        f"Paste any token address and I'll tell you if it's safe to buy. "
        f"Ask questions in plain English \u2014 no commands needed.\n\n"
        f"<i>Try me: paste any token address or ask \u201cis $BONK safe?\u201d</i>\n\n"
        f"{channels_menu_text()}\n\n"
        f"Quick start:"
    )


async def send_onboarding_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the first-run welcome with the quick-start keyboard.

    Pure additive helper — caller (``cmd_start``) decides when a user is new
    via :func:`is_first_run`. ``ctx`` is accepted for PTB handler symmetry.
    """
    del ctx  # unused — kept for the standard PTB handler signature
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    await message.reply_text(
        welcome_message(user.first_name or ""),
        parse_mode=ParseMode.HTML,
        reply_markup=quick_start_kb(),
        disable_web_page_preview=True,
    )


__all__ = [
    "DAILY_HOOK_ACTIONS",
    "FIRST_RUN_WINDOW_SECONDS",
    "ONBOARDING_QUICK_START",
    "is_first_run",
    "send_onboarding_welcome",
    "welcome_message",
]

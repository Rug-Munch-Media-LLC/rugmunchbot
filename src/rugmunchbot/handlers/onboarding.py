"""RugMunchBot — first-run onboarding + engagement hooks (additive).

New-user flow: when a brand-new user sends /start, ``cmd_start`` detects the
first run and shows a polished welcome — a 3-line "what the bot is" pitch,
the 3 core actions with examples (``/scan <address>``, ``$SOL``,
``/chat <question>``), and a quick-action keyboard whose last row is a
"More" button that opens the full /menu capability atlas (``menu_full``).

Every quick-start button reuses an EXISTING wired surface (``run_scan`` /
``run_price`` / ``run_chat``) plus the single new ``menu_full`` callback
branch (renders the existing ``MENU_TEXT`` atlas) — no new command handlers
and no new scheduler.

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

from app.domains.telegram.rugmunchbot.handlers._help_text import project_links_section
from app.domains.telegram.rugmunchbot.keyboards import quick_start_kb

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telegram import Update
    from telegram.ext import ContextTypes

#: A row is "first run" when created within this many seconds AND unused.
FIRST_RUN_WINDOW_SECONDS: int = 60

#: Quick-start actions shown to a brand-new user. ``run_scan`` / ``run_price`` /
#: ``run_chat`` are existing surfaces handled by ``handlers/callback``;
#: ``menu_full`` is the "More" button that renders the existing ``/menu`` atlas
#: (one additive callback branch in ``handlers/callback``).
ONBOARDING_QUICK_START: tuple[tuple[str, str], ...] = (
    ("\U0001f50d Scan", "run_scan"),
    ("\U0001f4b0 Price", "run_price"),
    ("\U0001f4ac Ask AI", "run_chat"),
    ("\U0001f4d6 More", "menu_full"),
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
    """Polished first-run welcome: 3-line "what the bot is" + 3 core actions.

    Line 1-3 explain what the bot IS (Rug Munch Intelligence Analyst — scan
    any token for rug/honeypot/bundle risk, AI-written verdicts, prices,
    charts, news, wallet intel). Then the three core actions with concrete
    examples. HTML-safe for Telegram: only balanced ``<b>`` / ``<code>``
    tags, no raw markdown, no ALL-CAPS, no emoji spam. The quick-action
    keyboard (``quick_start_kb``) carries the tappable actions + the
    "More" button that opens /menu.
    """
    greeting = f"Hey {name} \U0001f44b" if name else "Hey \U0001f44b"
    return (
        f"{greeting} \u2014 I'm the <b>Rug Munch Intelligence Analyst</b>, "
        f'crypto intelligence at <a href="https://rugmunch.io">rugmunch.io</a>.\n\n'
        f"Scan any token for rug, honeypot &amp; bundle risk.\n"
        f"Get AI-written verdicts, prices, charts, news and wallet intel.\n\n"
        f"<b>Try these</b>\n"
        f"\U0001f50d <code>/scan &lt;address&gt;</code> \u2014 audit any token before you buy\n"
        f"\U0001f4b0 <code>$SOL</code> \u2014 instant price check\n"
        f"\U0001f4ac <code>/chat &lt;question&gt;</code> \u2014 ask me anything\n\n"
        f"{project_links_section()}\n\n"
        f"Tap an action below \U0001f447"
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

"""RugMunchBot — first-run onboarding (one screen, three buttons).

Product rule (2026-10-23): a first-time user should understand the product in
one screen and act in one tap. No 43-command wall, no feature list. Three
buttons cover the three jobs people actually arrive with:

    Scan a token      → "is this safe to buy?"      (``run_scan``)
    Live desk         → "what is happening?"        (``run_desk`` → /desk)
    Talk to the desk  → "explain this to me"        (``run_chat``)

Below the buttons sits a five-command quickstart — the same five advertised
in the menu — so a returning user can type instead of tap. Everything else
lives under ``/more`` (progressive disclosure, see ``bot/_commands.py``).

First-run detection needs NO schema migration: a brand-new ``/start`` creates
the ``users`` row in the same request, so ``created_at`` is within a few
seconds of "now" and all usage counters are zero.
"""

from __future__ import annotations

import html
import time
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from rugmunchbot.handlers._help_text import project_links_section

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telegram import Update
    from telegram.ext import ContextTypes

#: A row is "first run" when created within this many seconds AND unused.
FIRST_RUN_WINDOW_SECONDS: int = 60

#: The three jobs a new user arrives with — one tap each. Every callback is an
#: existing, wired branch (``run_scan`` / ``run_desk`` / ``run_chat``).
ONBOARDING_ACTIONS: tuple[tuple[str, str], ...] = (
    ("\U0001f50d Scan a token", "run_scan"),
    ("\U0001f4f0 Live desk", "run_desk"),
    ("\U0001f4ac Talk to the desk", "run_chat"),
)

#: Five commands, one line each — the quickstart shown under the buttons.
QUICKSTART: tuple[tuple[str, str], ...] = (
    ("/scan <address>", "rug, honeypot and bundle risk in one card"),
    ("/desk", "the latest write-ups, with source and timestamp"),
    ("$SOL", "instant price for any ticker"),
    ("/alerts", "set a price or risk alert and walk away"),
    ("/more", "every command, grouped — when you want depth"),
)

#: Daily hook actions merged into the returning-user menu.
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


def onboarding_kb() -> InlineKeyboardMarkup:
    """Three primary actions (one row each) + a single "all commands" row."""
    rows = [[InlineKeyboardButton(label, callback_data=data)] for label, data in ONBOARDING_ACTIONS]
    rows.append([InlineKeyboardButton("\U0001f4cb All commands \u25be", callback_data="menu_full")])
    return InlineKeyboardMarkup(rows)


def welcome_message(name: str = "") -> str:
    """One-screen value prop + five-command quickstart (HTML, Telegram-safe).

    Copy discipline: one sentence on what this is, three concrete promises,
    then the quickstart. No hype adjectives, no ALL-CAPS, no emoji spam —
    the product has to sound like the desk that writes the research.
    """
    greeting = f"Hey {name} \U0001f44b" if name else "Hey \U0001f44b"
    steps = "\n".join(
        f"\u2022 <code>{html.escape(cmd)}</code> \u2014 {html.escape(desc)}"
        for cmd, desc in QUICKSTART
    )
    return (
        f"{greeting} \u2014 I'm the <b>Rug Munch Intelligence Analyst</b>.\n\n"
        "Paste any token address and I tell you whether it is a rug before you "
        "buy: contract risk, honeypots, bundle wallets, holder concentration, "
        "deployer history \u2014 then an AI verdict in plain English.\n\n"
        f"<b>Three things you can do right now</b>\n"
        "\U0001f50d Scan a token \u2014 <code>/scan &lt;address&gt;</code>\n"
        "\U0001f4f0 Read the desk \u2014 <code>/desk</code> (source + timestamp on every story)\n"
        "\U0001f4ac Ask anything \u2014 <code>/chat why is $SOL dumping?</code>\n\n"
        f"<b>Quickstart</b>\n{steps}\n\n"
        f"{project_links_section()}\n\n"
        "Tap an action below \U0001f447"
    )


async def send_onboarding_welcome(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the first-run welcome with the three-action keyboard.

    ``ctx`` is accepted for PTB handler symmetry (unused).
    """
    del ctx
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    await message.reply_text(
        welcome_message(user.first_name or ""),
        parse_mode=ParseMode.HTML,
        reply_markup=onboarding_kb(),
        disable_web_page_preview=True,
    )


__all__ = [
    "DAILY_HOOK_ACTIONS",
    "FIRST_RUN_WINDOW_SECONDS",
    "ONBOARDING_ACTIONS",
    "QUICKSTART",
    "is_first_run",
    "onboarding_kb",
    "send_onboarding_welcome",
    "welcome_message",
]

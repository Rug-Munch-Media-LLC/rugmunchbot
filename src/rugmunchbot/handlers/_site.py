"""Website-only surfaces exposed as compact bot commands (parity, 2026-10-24).

Three abilities used to be reachable only on rugmunch.io: the documentation
hub, the MCP/developer reference and the web dashboard. Duplicating a web UI
inside Telegram would be a second source of truth, so each command renders a
short, honest summary and hands off with an "Open on rugmunch.io" button
(``_site_links``). Registration is exported as ``SITE_COMMANDS`` so the menu
owner (``bot/_commands.py``) wires it with /desk, /plans and /more instead of
scattering three more ``_reg`` calls through ``_entry``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot.formatters import sep
from rugmunchbot.handlers._site_links import site_row, with_site_link

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

DOCS_TEXT = (
    "\U0001f4da <b>Documentation</b>\n{sep}\n\n"
    "The platform guide lives on rugmunch.io \u2014 scanning, scam school,\n"
    "API access, pricing and the FAQ, all in one page.\n\n"
    "<b>What it covers</b>\n"
    "  \u2022 How scans and risk verdicts are produced\n"
    "  \u2022 API keys, tiers and rate limits\n"
    "  \u2022 Scam-school material and pricing\n\n"
    "Building an agent? The tool-level reference \u2014 x402 payments, wallet\n"
    "connect, auth hierarchy, MCP integration \u2014 is on /mcpdocs."
)

MCP_TEXT = (
    "\U0001f9e9 <b>MCP / Developer Docs</b>\n{sep}\n\n"
    "RMI ships an MCP server so agents can call the scanner and intel\n"
    "endpoints directly. The reference is on rugmunch.io/mcp-docs.\n\n"
    "<b>Sections</b>\n"
    "  \u2022 API reference \u00b7 auth hierarchy \u00b7 discovery\n"
    "  \u2022 x402 payments \u00b7 free trials \u00b7 payment options\n"
    "  \u2022 MCP integration \u00b7 anti-abuse fingerprinting\n\n"
    "Keys and quotas: /api \u00b7 webhooks: /webhook"
)

DASH_TEXT = (
    "\U0001f4ca <b>Dashboard</b>\n{sep}\n\n"
    "Your web dashboard on rugmunch.io aggregates what the bot shows one\n"
    "command at a time: portfolio, watches, alerts and scan history in one\n"
    "screen, with charts.\n\n"
    "In-chat shortcuts \u2192 /portfolio \u00b7 /watchlist \u00b7 /alerts \u00b7 /account"
)


async def _reply(update: Update, text: str, markup: InlineKeyboardMarkup) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        text.format(sep=sep()),
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


async def cmd_docs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/docs -- platform documentation hub (+ one tap to the MCP reference)."""
    kb = InlineKeyboardMarkup([site_row("mcpdocs", "\U0001f9e9 MCP docs on rugmunch.io")])
    await _reply(update, DOCS_TEXT, with_site_link("docs", kb))


async def cmd_mcpdocs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/mcpdocs -- developer/MCP reference (API, x402, auth, integration)."""
    kb = InlineKeyboardMarkup([site_row("docs", "\U0001f4da Platform docs on rugmunch.io")])
    await _reply(update, MCP_TEXT, with_site_link("mcpdocs", kb))


async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/dashboard -- web dashboard hand-off; in-chat commands stay first-class."""
    await _reply(update, DASH_TEXT, with_site_link("dashboard"))


#: (command, handler) pairs registered by the menu owner (bot/_commands.py).
SITE_COMMANDS: tuple[tuple[str, Any], ...] = (
    ("docs", cmd_docs),
    ("mcpdocs", cmd_mcpdocs),
    ("dashboard", cmd_dashboard),
)

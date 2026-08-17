#!/usr/bin/env python3
"""
RugMunchBot -- /chat command: explicit freestyle AI chat entry point (v5).

Usage:
  /chat                  -> welcome card with capabilities + examples
  /chat <question>       -> full AI pipeline (backend RAG chat, news-aware)
  /chat reset            -> clear your conversation context

The actual pipeline lives in handlers/chat_ai/ (chat_ai_response) and is
shared with free-text messages and /ask, so /chat behaves identically to
just typing a message.

Group etiquette: since v12 the free-text pipeline (handlers/chat_ai/
_routing.py::ai_chat_handler) only fires when the bot is explicitly
addressed — private DMs always, and groups only on @mention or reply.
/chat is an explicit command and always works, in any chat type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.chat_ai import chat_ai_response
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.chat")

CHAT_WELCOME = (
    "\U0001f916 <b>Rug Munch AI \u2014 Freestyle Chat</b>\n\n"
    "Ask me anything about <b>crypto</b> \u2014 tokens, wallets, security, "
    "DeFi, trading, coding...\n\n"
    "\U0001f50d <b>Tokens & Security</b> \u2014 audits, honeypots, rug pulls, contracts\n"
    "\U0001f9db <b>Wallets</b> \u2014 holdings, risk, smart money, entity attribution\n"
    "\U0001f4c8 <b>Markets</b> \u2014 prices, trends, liquidity, sentiment, news\n"
    "\U0001f4b1 <b>DeFi</b> \u2014 DEXs, liquidity pools, yields, wash trading\n"
    "\U0001f469\u200d\U0001f4bb <b>Coding</b> \u2014 Solidity, Rust, Python, web3 dev\n"
    "\U0001f4da <b>Education</b> \u2014 scam patterns, DeFi concepts, on-chain analysis\n\n"
    "<b>Try:</b>\n"
    "\u2022 /chat <code>how do honeypots work?</code>\n"
    "\u2022 /chat <code>write a Solidity ERC-20 with a tax</code>\n"
    "\u2022 /chat <code>is this wallet risky?</code> <i>+ paste address</i>\n\n"
    "\U0001f4ac <b>Tip:</b> I answer free text in private DMs and when you "
    "@mention or reply to me in groups \u2014 in shared rooms I stay quiet "
    "unless you call me. I keep the last 5 messages for context (use "
    "<code>/chat reset</code> to clear).\n\n"
    "\U0001f6ab See a scam? <code>/report &lt;address&gt;</code> to flag it.\n\n"
    "\U0001f4f3 Free tier: 15 AI msgs/week \u00b7 Scout: 75 \u00b7 Hunter: 250 \u00b7 Pro: 500"
)


async def cmd_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Freestyle AI chat entry point (also reached by free text)."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    db.record_activity(cmd=True)

    query = " ".join(ctx.args or []).strip()

    # /chat reset — clear conversation context
    if query.lower() in ("reset", "clear", "new", "restart"):
        db.clear_chat_history(user.id)
        await update.message.reply_text(
            "\U0001f9f9 <b>Conversation reset</b>\n\nI've cleared our chat context. We start fresh!",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    # No args -> welcome card with capabilities
    if not query:
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("\U0001f50d /ask \u2014 Deep RAG answer", callback_data="run_ask"),
                    InlineKeyboardButton("\U0001f9e0 /research \u2014 RAG research", callback_data="run_research"),
                ],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Main Menu", callback_data="menu_main")],
            ]
        )
        await update.message.reply_text(CHAT_WELCOME, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # Question -> shared AI pipeline (quota + flood gated inside)
    await chat_ai_response(update, ctx, query, user)

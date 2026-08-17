#!/usr/bin/env python3
"""
RugMunchBot -- /ask command: deep AI answer (v5).

Explicit "chat with the intelligence base" command for deeper questions.
After the AI-chat dedup (single free-text pipeline), /ask routes through the
SAME synthesis path as free text and /chat: ``chat_ai.chat_ai_response``
calls the backend ``POST /api/v1/chat`` endpoint, which auto-RAGs across
threat intel / scam intel / news_v2 and answers with news-aware grounding.

Usage: /ask <question>
Counts against the user's weekly AI-message quota (same gate as chat) and
the per-second / per-minute flood limit (AI surface). Use /research to see
raw source documents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.handlers.chat_ai import chat_ai_response
from app.domains.telegram.rugmunchbot.keyboards import main_menu_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.ask")


async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Deep AI answer via the single backend-RAG pipeline. Usage: /ask <question>"""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    query = " ".join(ctx.args or []).strip()
    if not query:
        await update.message.reply_text(
            "\U0001f916 <b>/ask \u2014 Deep AI Answers</b>\n\n"
            "Ask a question and I'll synthesize an answer from the RMI "
            "intelligence base (threat intel, scam patterns, news, agent "
            "memory).\n\n"
            "<b>Examples:</b>\n"
            "\u2022 /ask <code>how do honeypots work</code>\n"
            "\u2022 /ask <code>signs of a rug pull</code>\n"
            "\u2022 /ask <code>what is bundle buying</code>\n\n"
            "\U0001f4a1 Use <code>/research</code> to see raw source documents.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(user.id),
        )
        return

    # Delegate to the single merged AI pipeline (same as free text and
    # /chat): chat_enabled gate, injection check, weekly quota, flood
    # control, gibberish gate, backend RAG chat + chunked answer. The
    # pipeline posts its own "Thinking..." placeholder.
    await chat_ai_response(update, ctx, query, user)

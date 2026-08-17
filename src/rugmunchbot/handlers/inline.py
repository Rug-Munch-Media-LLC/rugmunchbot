#!/usr/bin/env python3
"""
RugMunchBot -- Inline query handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)

from app.domains.telegram.rugmunchbot.formatters import is_evm, is_sol

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def handle_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries: offer scan/deployer results for EVM/Solana addresses."""
    query = update.inline_query.query.strip()  # type: ignore[union-attr]  # PTB None-able fields
    if not query:
        return
    results = []
    if is_evm(query) or is_sol(query):
        results.append(
            InlineQueryResultArticle(
                id="scan_" + query[:10],
                title=f"\U0001f50d Scan {query}",
                description="Run a full security scan + scoring breakdown",
                input_message_content=InputTextMessageContent(f"/scan {query}"),
            )
        )
        results.append(
            InlineQueryResultArticle(
                id="dep_" + query[:10],
                title=f"\U0001f468\u200d\U0001f4bb Deployer {query}",
                description="Deployer fingerprint & reputation check",
                input_message_content=InputTextMessageContent(f"/deployer {query}"),
            )
        )
        results.append(
            InlineQueryResultArticle(
                id="hype_" + query[:10],
                title=f"\U0001f525 Hype {query}",
                description="Quick 0-100 hype score",
                input_message_content=InputTextMessageContent(f"/hype {query}"),
            )
        )
    if len(query) >= 2 and len(query) <= 10 and query.isalpha():
        results.append(
            InlineQueryResultArticle(
                id="sym_" + query,
                title=f"\U0001fa99 Lookup ${query.upper()}",
                description="Find token price and info",
                input_message_content=InputTextMessageContent(f"${query.upper()}"),
            )
        )
    if results:
        await update.inline_query.answer(results, cache_time=30, is_personal=True)  # type: ignore[union-attr]  # PTB None-able fields

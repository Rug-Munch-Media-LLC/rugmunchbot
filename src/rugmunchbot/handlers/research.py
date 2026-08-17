#!/usr/bin/env python3
"""
RugMunchBot -- /research command: RAG-powered deep research.

Queries the RAG system across threat_intel, rmi_agent_memory, and scam_intel
collections to provide AI-powered research on tokens, addresses, and scam patterns.
Hunter+ tier feature.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    WEBSITE_URL,
    backend_headers,
)
from app.domains.telegram.rugmunchbot.formatters import (
    is_owner,
    paywall_text,
    sep,
    short_addr,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import paywall_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.research")

# Address detection regexes
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

_RESEARCH_COLLECTIONS = [
    "threat_intel",
    "rmi_agent_memory",
    "scam_intel",
    "known_scams",
]

_TOPIC_SUGGESTIONS = {
    "honeypot": "How honeypot tokens work and detection patterns",
    "bundling": "Bundle buying patterns and detection techniques",
    "wash_trading": "Wash trading detection and volume manipulation",
    "rugpull": "Common rug pull strategies and warning signs",
    "smart_money": "Smart money wallet tracking and analysis",
    "fresh_wallet": "Fresh wallet detection and risk assessment",
    "mev": "MEV attacks and sandwich bot detection",
    "pump_dump": "Pump and dump scheme detection",
    "liquidity": "Liquidity analysis and LP lock verification",
}


async def _query_rag(query: str, collections: list[str] | None = None, top_k: int = 5) -> list[dict[str, Any]]:
    """Query the RAG system across multiple collections."""
    if collections is None:
        collections = _RESEARCH_COLLECTIONS

    all_hits = []
    client = get_shared_client(30)
    for collection in collections:
        try:
            r = await client.post(
                f"{BACKEND_URL}/api/v1/rag/v2/search",
                headers=backend_headers(),
                json={
                    "query": query,
                    "collection": collection,
                    "top_k": top_k,
                    "min_similarity": 0.0,
                    "filters": {},
                },
            )
            if r.status_code == 200:
                data = r.json()
                hits = data.get("hits", [])
                for h in hits:
                    # Keep the backend's truthful resolved-collection
                    # label when present; only fall back to the requested
                    # name for unlabeled hits.
                    h.setdefault("collection", collection)
                all_hits.extend(hits)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("RAG query failed for %s: %s", collection, e)
            continue

    # Deduplicate by content
    seen = set()
    deduped = []
    for h in sorted(all_hits, key=lambda x: x.get("score", 0), reverse=True):
        content = h.get("content", "")[:100]
        if content not in seen:
            seen.add(content)
            deduped.append(h)

    return deduped[:top_k]


def _format_research_result(query: str, hits: list[dict[str, Any]]) -> str:
    """Format RAG research results into a readable message."""
    s = sep()
    d = thin_sep()

    lines = [
        "\U0001f50d <b>RAG Research Results</b>",
        s,
        f"<b>Query:</b> <i>{html.escape(query[:100])}</i>",
        f"<b>Sources:</b> {len(hits)} relevant documents found",
        s,
    ]

    for i, hit in enumerate(hits, 1):
        content = hit.get("content", "")[:500]
        score = hit.get("score", 0)
        collection = hit.get("collection", "unknown")
        metadata = hit.get("metadata", {})

        # Collection badge
        col_emoji = {
            "threat_intel": "\U0001f6e1\ufe0f",
            "rmi_agent_memory": "\U0001f916",
            "scam_intel": "\U0001f50d",
            "known_scams": "\U0001f6ab",
        }.get(collection, "\U0001f4c4")

        # Source info with clickable link
        source = metadata.get("source", "") or metadata.get("url", "") or ""
        title = metadata.get("title", "") or metadata.get("name", "") or collection
        if source:
            if source.startswith("http"):
                source_str = f"\n  📎 <a href='{html.escape(source, quote=True)}'>{html.escape(title[:60])}</a>"
            else:
                source_str = f"\n  📎 <i>{html.escape(source[:100])}</i>"
        else:
            source_str = ""

        lines.extend(
            [
                f"<b>{i}. {col_emoji} [{collection}]</b> (relevance: {score:.0%}){source_str}",
                d,
                html.escape(content),
                "",
            ]
        )

    # Add footer with suggestions
    lines.extend(
        [
            s,
            "<b>\U0001f4a1 Research Tips</b>",
            d,
            "\u2022 Be more specific for better results",
            "\u2022 Include contract addresses for token-specific research",
            "\u2022 Try: /research <code>honeypot detection patterns</code>",
            "\u2022 Try: /research <code>address</code> (auto-detects)",
        ]
    )

    return "\n".join(lines)


async def cmd_research(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """RAG-powered deep research across intelligence collections.

    Usage: /research <query>
    Hunter+ tier feature.

    Examples:
      /research honeypot detection patterns
      /research 0x6982508145454Ce325dDbE47a25d4ec3d2311933
      /research bundle washing techniques
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    # Show topic suggestions if no args
    if not ctx.args:
        kb_rows = []
        for key, desc in list(_TOPIC_SUGGESTIONS.items())[:6]:
            kb_rows.append([InlineKeyboardButton(desc[:35], callback_data=f"research_{key}")])
        kb_rows.append([InlineKeyboardButton("\U0001f310 Full Web Research", url=f"{WEBSITE_URL}/research")])
        kb_rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])

        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f50d <b>RAG-Powered Research</b>\n\n"
            "Ask anything about crypto security, scam patterns, or specific tokens.\n\n"
            "<b>Examples:</b>\n"
            "\u2022 /research <code>honeypot detection patterns</code>\n"
            "\u2022 /research <code>0x6982...1933</code>\n"
            "\u2022 /research <code>how to detect wash trading</code>\n\n"
            "<b>Quick Topics:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb_rows),
        )
        return

    # Tier check
    tier = db.get_user_tier(user.id)
    if tier in ("free", "scout") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f50d <b>RAG Research</b>\n\n"
            "This feature requires <b>Hunter tier</b> or higher.\n\n"
            "\U0001f3af <b>Hunter tier</b> — 400 scans/wk + RAG research\n"
            "\U0001f6f0\ufe0f <b>Monitor (250\u2605/30d)</b> — live LP/whale + "
            "tax/ownership/mint alerts · 🔬 <b>Deep Scan (250\u2605)</b> — full forensics",
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return

    query = " ".join(ctx.args).strip()

    # Auto-detect address
    address = None
    for arg in ctx.args:
        if EVM_RE.match(arg) or SOL_RE.match(arg):
            address = arg
            break

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Researching...</b>\n\n"
        f"\U0001f4ac <i>{html.escape(query[:100])}</i>\n\n"
        f"\U0001f50d Querying RAG collections: "
        f"{', '.join(_RESEARCH_COLLECTIONS)}...\n"
        f"\u23f3 This may take 15-30 seconds",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Build richer query if address detected
        search_query = query
        if address:
            search_query = f"{query} token contract {short_addr(address)}"

        hits = await _query_rag(search_query, top_k=5)

        if not hits:
            await msg.edit_text(
                f"\U0001f50d <b>No Results Found</b>\n\n"
                f"No relevant documents found for: <i>{html.escape(query[:100])}</i>\n\n"
                f"<b>Tips:</b>\n"
                f"\u2022 Try different keywords\n"
                f"\u2022 Be more specific\n"
                f"\u2022 Check if the RAG system has relevant data\n"
                f"\u2022 Try /investigate for deep token analysis",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("\U0001f504 Try Again", callback_data="menu_main")],
                        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                    ]
                ),
            )
            return

        formatted = _format_research_result(query, hits)

        # Build keyboard with address actions if one was detected
        if address:
            chain = "solana" if SOL_RE.match(address) else "ethereum"
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [InlineKeyboardButton("\U0001f9db Wallet Check", callback_data=f"wallet_{address}_{chain}")],
                    [
                        InlineKeyboardButton(
                            "\U0001f52c Deep Investigate", callback_data=f"investigate_{address}_{chain}"
                        )
                    ],
                    [InlineKeyboardButton("\U0001f310 Web Report", url=f"{WEBSITE_URL}/markets/token/{address}")],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            )
        else:
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f310 Research on Web", url=f"{WEBSITE_URL}/research")],
                    [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                ]
            )

        db.increment_usage(user.id, scan=True)

        # Handle long messages
        if len(formatted) > 4000:
            summary = formatted[:3500] + f"\n\n... <i>Result truncated. {len(hits)} documents found.</i>"
            await msg.edit_text(summary, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)
        else:
            await msg.edit_text(formatted, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True)

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_research_error", exc_info=True)
        logger.error(f"Research error: {e}")
        await msg.edit_text(
            f"\u274c <b>Research Failed</b>\n\n"
            f"Could not complete research for: <i>{html.escape(query[:100])}</i>\n\n"
            f"Please try again or use simpler terms.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]
            ),
        )

#!/usr/bin/env python3
"""
V3 AI analysis handler — /analyze (DeepSeek-8B grounded in scanner context)
plus the ai_ callback action from scan reports.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import OWNER_IDS
from handlers.menu import back_kb, pricing_kb, scan_result_kb_v3
from services.formatting import format_ai_verdict
from services.llm import analyze_token
from services.scanner import ScanUnavailableError, detect_chain, scan_token_v2
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


def _short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr


def _is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


async def _run_analysis(user_id: int, address: str, chain: str | None) -> tuple[str, dict]:
    """Fetch scanner context then LLM verdict. Raises on LLM failure."""
    scan_ctx: dict = {}
    try:
        scan_ctx = await scan_token_v2(address, chain, depth="quick")
        scan_ctx.setdefault("address", address)
        scan_ctx.setdefault("chain", detect_chain(address))
    except ScanUnavailableError as exc:
        logger.debug("analyze: scanner context unavailable (%s); continuing without it", exc)
    except Exception as exc:
        logger.debug("analyze: scanner context error: %s", exc)
    verdict = await analyze_token(address, scan_ctx)
    db.increment_usage(user_id, ai_msg=True)
    return verdict, scan_ctx


async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🤖 <b>AI Analysis</b>\n\n"
            "Usage: /analyze <code>address</code>\n\n"
            "RugMunch AI analyses a token using scanner intelligence.\n"
            "🅱️ <b>Free Beta</b> — 30 AI messages/week for all users!",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = args[0].strip()
    chain = args[1] if len(args) > 1 else None
    detected = detect_chain(address)
    if detected == "unknown":
        await update.message.reply_text(
            "❌ <b>Invalid address</b>\n\nPlease double-check and try again.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not _is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
        if not allowed:
            await update.message.reply_text(
                "⚡ <b>Weekly AI Limit Reached</b>\n\n"
                "🅱️ <b>Free Beta:</b> 30 AI messages/week — you've used them all!\n"
                "Pro tiers launching soon with more AI capacity.\n\n"
                "Sign up at rugmunch.io for early adopter perks!",
                parse_mode=ParseMode.HTML,
                reply_markup=pricing_kb(),
            )
            return

    msg = await update.message.reply_text(
        f"🤖 <b>Analysing with RugMunch AI...</b>\n<code>{_short(address)}</code>\n"
        "🅱️ <b>Free Beta</b> — AI analysis unlocked for all!",
        parse_mode=ParseMode.HTML,
    )
    try:
        verdict, scan_ctx = await _run_analysis(user.id, address, chain)
    except Exception as exc:
        logger.error("AI analysis failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            "❌ <b>AI Analysis Unavailable</b>\n\n"
            "The AI backend is not responding right now. Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    await msg.edit_text(
        format_ai_verdict(address, verdict, scan_ctx),
        parse_mode=ParseMode.HTML,
        reply_markup=scan_result_kb_v3(address, chain or scan_ctx.get("chain") or detected),
        disable_web_page_preview=True,
    )


async def handle_ai_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """AI analysis from the scan-report '🤖 AI Analysis' button."""
    query = update.callback_query
    if query is None:
        return
    user = query.from_user
    parts = (query.data or "").split("_", 2)
    if len(parts) < 3:
        return
    address, chain = parts[1], parts[2]
    if not _is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
        if not allowed:
            await query.edit_message_text(
                "⚡ <b>Weekly AI Limit Reached</b>\n\n"
                "🅱️ <b>Free Beta:</b> 30 AI messages/week — you've used them all!\n"
                "Sign up at rugmunch.io for early adopter perks!",
                parse_mode=ParseMode.HTML,
                reply_markup=pricing_kb(),
            )
            return
    await query.edit_message_text(
        f"🤖 <b>Analysing with RugMunch AI...</b>\n<code>{_short(address)}</code>\n"
        "🅱️ <b>Free Beta</b> — AI analysis unlocked for all!",
        parse_mode=ParseMode.HTML,
    )
    try:
        verdict, scan_ctx = await _run_analysis(user.id, address, chain)
    except Exception as exc:
        logger.error("Callback AI analysis failed for %s: %s", _short(address), exc)
        await query.edit_message_text(
            "❌ <b>AI Analysis Unavailable</b>\n\nPlease try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    await query.edit_message_text(
        format_ai_verdict(address, verdict, scan_ctx),
        parse_mode=ParseMode.HTML,
        reply_markup=scan_result_kb_v3(address, chain or scan_ctx.get("chain") or "unknown"),
        disable_web_page_preview=True,
    )

#!/usr/bin/env python3
"""
V3 scan handlers — /scan (RugMunchScan v2 → v1), /deepscan (SENTINEL,
premium-gated), /risk (quick) plus scan_/deep_/watch_ callback actions.

The legacy DataBus path (bot.scan_token) is injected at startup via
set_databus_fallback() so /scan never regresses when both v2 and v1 fail.
"""

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import DEEP_SCAN_STARS_COST, OWNER_IDS, TIERS
from handlers.menu import back_kb, pricing_kb, scan_result_kb_v3
from services.formatting import (
    format_deepscan_report,
    format_quick_risk,
    format_v3_scan_report,
)
from services.scanner import ScanUnavailableError, detect_chain, scan_token_v2, sentinel_scan
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

SPAM_COOLDOWN = 3.0
_user_last_action: dict[int, float] = {}

# Legacy DataBus fallback — set by bot.py at startup (never regress).
_databus_fallback = None


def set_databus_fallback(fn) -> None:
    """Inject the legacy DataBus scan path (async scan_token(address, chain))."""
    global _databus_fallback
    _databus_fallback = fn


def _short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr


def _is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def _check_spam(user_id: int) -> bool:
    now = time.time()
    last = _user_last_action.get(user_id, 0.0)
    if now - last < SPAM_COOLDOWN and not _is_owner(user_id):
        return True
    _user_last_action[user_id] = now
    return False


def _rate_limited(user_id: int, action: str = "scan") -> bool:
    allowed, _used, _limit = db.check_rate_limit(user_id, action)
    return not allowed


def _paywall_text(user_id: int, action: str = "scan") -> str:
    tier = db.get_user_tier(user_id)
    tc = TIERS.get(tier, TIERS["free"])
    limit = tc.get("scans_per_week", 25) if action == "scan" else tc.get("ai_msgs_per_week", 15)
    scans, ai = db.get_weekly_usage(user_id)
    used = scans if action == "scan" else ai
    return (
        f"⚡ <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"📊 <b>{tc['name']} Tier:</b> {used}/{limit} used this week\n\n"
        f"<b>Unlock more power:</b>\n"
        f"🔍 <b>Scout ($29.99/mo)</b> — 150 scans/wk\n"
        f"🎯 <b>Hunter ($49.99/mo)</b> — 400 scans/wk\n"
        f"👑 <b>Pro ($99.99/mo)</b> — 1,000 scans/wk\n\n"
        f"Or buy one-time top-ups — never expire!"
    )


def _validate_address(address: str, chain: str | None) -> tuple[str, bool]:
    detected = detect_chain(address)
    if detected == "unknown":
        return "unknown", False
    if chain and chain.lower() not in ("solana", "ethereum", "base", "bsc", "arbitrum", "polygon"):
        return detected, False
    return detected, True


# ── /scan ──


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🔍 <b>Token Scanner</b>\n\n"
            "Usage: /scan <code>address</code>\n\n"
            "<b>Examples:</b>\n"
            "  /scan <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>\n"
            "  /scan <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = args[0].strip()
    chain = args[1] if len(args) > 1 else None
    detected, ok = _validate_address(address, chain)
    if not ok:
        await update.message.reply_text(
            "❌ <b>Invalid address</b>\n\n"
            "I couldn't recognise that as a Solana or EVM contract address. "
            "Please double-check and try again.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not _is_owner(user.id) and _rate_limited(user.id, "scan"):
        await update.message.reply_text(
            _paywall_text(user.id, "scan"),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"🔍 <b>Scanning token...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Querying RugMunchScan...",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await scan_token_v2(address, chain, depth="standard")
        data.setdefault("address", address)
        data.setdefault("chain", detected)
        db.increment_usage(user.id, scan=True)
        db.log_scan(
            user.id, address, data.get("chain", detected), "v2", data.get("risk_score") or 0
        )
        await msg.edit_text(
            format_v3_scan_report(data),
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb_v3(address, data.get("chain", detected)),
            disable_web_page_preview=True,
        )
    except ScanUnavailableError:
        await _fallback_scan(msg, user.id, address, chain or detected)
    except Exception as exc:
        logger.error("V3 scan failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ <b>Scan Failed</b>\n\nError: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _fallback_scan(msg, user_id: int, address: str, chain: str) -> None:
    """Legacy DataBus fallback so /scan never regresses; graceful error otherwise."""
    if _databus_fallback is not None:
        try:
            result = await _databus_fallback(address, chain)
            sources = result.get("_data_source", {})
            if sources.get("price") is None and sources.get("detail") is None:
                raise ScanUnavailableError("no data")
            db.increment_usage(user_id, scan=True)
            db.log_scan(
                user_id,
                address,
                result.get("chain", chain),
                "basic",
                result.get("risk_score") or 0,
            )
            await msg.edit_text(
                format_v3_scan_report(result),
                parse_mode=ParseMode.HTML,
                reply_markup=scan_result_kb_v3(address, result.get("chain", chain)),
                disable_web_page_preview=True,
            )
            return
        except Exception as exc:
            logger.debug("DataBus fallback failed for %s: %s", _short(address), exc)
    await msg.edit_text(
        "❌ <b>Scanner Temporarily Unavailable</b>\n\n"
        f"RugMunchScan could not fetch data for <code>{_short(address)}</code>.\n\n"
        "Please try again later.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
    )


# ── /deepscan (premium) ──


def _can_deepscan(user_id: int) -> tuple[bool, str]:
    """Scout+ tier, or pay-per-scan with ⭐250 (one-time)."""
    tier = db.get_user_tier(user_id)
    if tier in ("scout", "hunter", "pro"):
        return True, "tier"
    user = db.get_or_create_user(user_id)
    if (user.get("stars_balance") or 0) >= DEEP_SCAN_STARS_COST:
        return True, "stars"
    return False, "none"


def _deepscan_gate_text() -> str:
    return (
        "🔒 <b>Deep Scan requires Scout+ or ⭐250</b>\n\n"
        "SENTINEL forensic analysis is a premium feature:\n"
        "• Deployer history & cluster mapping\n"
        "• Bundle / wash-trade detection\n"
        "• Holder bubble map & scam indicators\n\n"
        "🔍 <b>Scout ($29.99/mo)</b> — includes deep scans\n"
        "⭐ 250 Stars — pay per scan (no subscription)"
    )


async def cmd_deepscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🧠 <b>Deep Scan (SENTINEL)</b>\n\n"
            "Usage: /deepscan <code>address</code>\n\n"
            "Premium forensic analysis — Scout+ or ⭐250 per scan.",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    address = args[0].strip()
    chain = args[1] if len(args) > 1 else None
    detected, ok = _validate_address(address, chain)
    if not ok:
        await update.message.reply_text(
            "❌ <b>Invalid address</b>\n\nPlease double-check and try again.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not _is_owner(user.id) and _rate_limited(user.id, "scan"):
        await update.message.reply_text(
            _paywall_text(user.id, "scan"),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    allowed, how = _can_deepscan(user.id)
    if not allowed:
        await update.message.reply_text(
            _deepscan_gate_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    if how == "stars" and not db.spend_stars(user.id, DEEP_SCAN_STARS_COST, "deep_scan"):
        await update.message.reply_text(
            "❌ <b>Not enough Stars</b>\n\nYou need ⭐250 for a deep scan.",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"🧠 <b>Running SENTINEL deep forensic scan...</b>\n<code>{_short(address)}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await sentinel_scan(address, chain)
        data.setdefault("chain", detected)
        db.increment_usage(user.id, scan=True)
        db.log_scan(
            user.id,
            address,
            data.get("chain", detected),
            "deep",
            data.get("risk_score") or data.get("score") or 0,
        )
        await msg.edit_text(
            format_deepscan_report(data),
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb_v3(address, data.get("chain", detected)),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        if how == "stars":
            db.add_stars(user.id, DEEP_SCAN_STARS_COST, "refund_deep_scan")
        logger.error("Deep scan failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            "❌ <b>Deep Scan Failed</b>\n\n"
            "SENTINEL could not complete the forensic scan. Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── /risk (quick) ──


async def cmd_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "⚡ <b>Quick Risk</b>\n\nUsage: /risk <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = args[0].strip()
    chain = args[1] if len(args) > 1 else None
    detected, ok = _validate_address(address, chain)
    if not ok:
        await update.message.reply_text(
            "❌ <b>Invalid address</b>\n\nPlease double-check and try again.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not _is_owner(user.id) and _rate_limited(user.id, "scan"):
        await update.message.reply_text(
            _paywall_text(user.id, "scan"),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"⚡ <b>Fetching quick risk...</b>\n<code>{_short(address)}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await scan_token_v2(address, chain, depth="quick")
        data.setdefault("address", address)
        data.setdefault("chain", detected)
        db.increment_usage(user.id, scan=True)
        db.log_scan(
            user.id,
            address,
            data.get("chain", detected),
            "quick",
            data.get("risk_score") or 0,
        )
        await msg.edit_text(
            format_quick_risk(data),
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb_v3(address, data.get("chain", detected)),
            disable_web_page_preview=True,
        )
    except ScanUnavailableError:
        await msg.edit_text(
            "❌ <b>Scanner Temporarily Unavailable</b>\n\nPlease try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    except Exception as exc:
        logger.error("Quick risk failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ <b>Risk Check Failed</b>\n\nError: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── Callbacks: scan_ / deep_ / watch_ ──


async def handle_scan_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Full scan from the inline '🔍 Full Scan' / alert action button."""
    query = update.callback_query
    if query is None:
        return
    user = query.from_user
    parts = (query.data or "").split("_", 2)
    if len(parts) < 3:
        return
    address, chain = parts[1], parts[2]
    if not _is_owner(user.id) and _rate_limited(user.id, "scan"):
        await query.edit_message_text(
            _paywall_text(user.id, "scan"),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    await query.edit_message_text(
        f"🔍 <b>Scanning token...</b>\n<code>{_short(address)}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await scan_token_v2(address, chain, depth="standard")
        data.setdefault("address", address)
        data.setdefault("chain", chain)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, address, data.get("chain", chain), "v2", data.get("risk_score") or 0)
        await query.edit_message_text(
            format_v3_scan_report(data),
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb_v3(address, data.get("chain", chain)),
            disable_web_page_preview=True,
        )
    except ScanUnavailableError:
        await _fallback_scan(query.message, user.id, address, chain)
    except Exception as exc:
        logger.error("Callback scan failed for %s: %s", _short(address), exc)
        await query.edit_message_text(
            f"❌ <b>Scan Failed</b>\n\nError: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def handle_deep_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Deep scan from the scan-report '🔬 Deep Scan' button."""
    query = update.callback_query
    if query is None:
        return
    user = query.from_user
    parts = (query.data or "").split("_", 2)
    if len(parts) < 3:
        return
    address, chain = parts[1], parts[2]

    allowed, how = _can_deepscan(user.id)
    if not allowed:
        await query.edit_message_text(
            _deepscan_gate_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    if how == "stars" and not db.spend_stars(user.id, DEEP_SCAN_STARS_COST, "deep_scan"):
        await query.edit_message_text(
            "❌ <b>Not enough Stars</b>\n\nYou need ⭐250 for a deep scan.",
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return

    await query.edit_message_text(
        f"🧠 <b>Running SENTINEL deep forensic scan...</b>\n<code>{_short(address)}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await sentinel_scan(address, chain)
        data.setdefault("chain", chain)
        db.increment_usage(user.id, scan=True)
        db.log_scan(
            user.id,
            address,
            data.get("chain", chain),
            "deep",
            data.get("risk_score") or data.get("score") or 0,
        )
        await query.edit_message_text(
            format_deepscan_report(data),
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb_v3(address, data.get("chain", chain)),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        if how == "stars":
            db.add_stars(user.id, DEEP_SCAN_STARS_COST, "refund_deep_scan")
        logger.error("Callback deep scan failed for %s: %s", _short(address), exc)
        await query.edit_message_text(
            "❌ <b>Deep Scan Failed</b>\n\nSENTINEL could not complete the forensic scan.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def handle_watch_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Add to watchlist from the scan-report '👀 Watch' button."""
    query = update.callback_query
    if query is None:
        return
    user = query.from_user
    parts = (query.data or "").split("_", 2)
    if len(parts) < 3:
        return
    address, chain = parts[1], parts[2]
    ok = db.add_watchlist(user.id, address, chain)
    if ok:
        await query.answer("✅ Added to watchlist!")
    else:
        await query.answer("❌ Watchlist full or requires Scout tier", show_alert=True)

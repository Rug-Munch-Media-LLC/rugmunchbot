#!/usr/bin/env python3
"""
V3 scan handlers — /scan (RugMunchScan v2 → v1), /deepscan (SENTINEL,
premium-gated), /risk (quick) plus scan_/deep_/watch_ callback actions.

Deep scan reports are composed from the base SENTINEL report plus the typed
profile sections (wallet breakdown / bundle / deployer) rendered by
``formatters.wallet_breakdown``, then split into ≤4096-char Telegram chunks.
If the backend does not expose SENTINEL yet (HTTP 404), /deepscan falls back
to a "Deep Scan Coming Soon" message instead of a generic failure.

The legacy DataBus path (bot.scan_token) is injected at startup via
set_databus_fallback() so /scan never regresses when both v2 and v1 fail.
"""

import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import OWNER_IDS, TIERS, WEBSITE_URL
from handlers.menu import back_kb, pricing_kb, scan_result_kb_v3
from services.formatting import (
    format_comprehensive_deepscan,
    format_deepscan_report,
    format_quick_risk,
    format_v3_scan_report,
)
from services.scanner import (
    ScanUnavailableError,
    detect_chain,
    run_deep_scan_aggregate,
    scan_token_v2,
)
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

# formatters.wallet_breakdown is a newer module and may expose either of two
# APIs depending on when it was written; load both defensively so /deepscan
# never regresses if one variant is missing.
try:
    from formatters.wallet_breakdown import (
        format_deepscan_profile_sections as _profile_sections,
    )
except ImportError:  # pragma: no cover - depends on formatter module variant
    _profile_sections = None
try:
    from formatters.wallet_breakdown import format_deep_scan_summary as _deep_summary
except ImportError:  # pragma: no cover - depends on formatter module variant
    _deep_summary = None

logger = logging.getLogger(__name__)

SPAM_COOLDOWN = 3.0
MAX_MESSAGE_CHARS = 4096
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
    limit = tc.get("scans_per_week", 50) if action == "scan" else tc.get("ai_msgs_per_week", 30)
    scans, ai = db.get_weekly_usage(user_id)
    used = scans if action == "scan" else ai
    return (
        f"⚡ <b>Weekly {action.title()} Limit Reached</b>\n\n"
        f"🅱️ <b>Free Beta — {tc['name']}:</b> {used}/{limit} used this week\n\n"
        f"<b>Pro tiers coming soon!</b> During beta, enjoy generous free limits.\n"
        f"Want more? Sign up at rugmunch.io for early adopter perks!\n\n"
        f"Or buy one-time top-ups — never expire!"
    )


def _validate_address(address: str, chain: str | None) -> tuple[str, bool]:
    detected = detect_chain(address)
    if detected == "unknown":
        return "unknown", False
    if chain and chain.lower() not in ("solana", "ethereum", "base", "bsc", "arbitrum", "polygon"):
        return detected, False
    return detected, True


# ── deep-scan report chunking (>4096 chars → multiple messages) ──


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Split an oversized single section at newline boundaries (rare)."""
    lines = text.split("\n")
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = line[:max_chars] if len(line) > max_chars else line
    if current:
        chunks.append(current)
    return chunks


def _chunk_report_sections(sections: list[str], max_chars: int | None = None) -> list[str]:
    """Join report sections into Telegram-safe chunks (≤ max_chars each).

    Splits only at section boundaries so HTML tags are never cut mid-tag.
    If a single section is itself oversized, falls back to newline-level
    hard splits. ``max_chars`` resolves to MAX_MESSAGE_CHARS at call time so
    it can be tuned in tests.
    """
    if max_chars is None:
        max_chars = MAX_MESSAGE_CHARS
    chunks: list[str] = []
    current = ""
    for section in sections:
        if not section:
            continue
        if len(section) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_split(section, max_chars))
            continue
        candidate = section if not current else f"{current}\n\n{section}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = section
    if current:
        chunks.append(current)
    return chunks


def _deepscan_chunks(data: dict) -> list[str]:
    """Base SENTINEL report + typed profile sections, split into chunks.

    Uses whichever formatter API is present: the standalone deep-scan summary
    (format_deep_scan_summary) when available, otherwise the base report plus
    profile sections (format_deepscan_profile_sections).
    """
    if _deep_summary is not None:
        try:
            chunks = cast("Callable[[dict], list[str]]", _deep_summary)(data)
            if chunks:
                return chunks
        except Exception as exc:  # fall through on any formatter issue
            logger.debug("format_deep_scan_summary failed: %s", exc)
    sections = [format_deepscan_report(data)]
    if _profile_sections is not None:
        sections.extend(_profile_sections(data))
    return _chunk_report_sections(sections)


async def _deliver_deepscan(
    ctx: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    data: dict,
    address: str,
    chain: str,
    edit_fn,
) -> None:
    """Edit the "scanning..." message with the first chunk, then send the
    remaining chunks as follow-up messages (Telegram 4096-char limit)."""
    chunks = _deepscan_chunks(data)
    kb = scan_result_kb_v3(address, chain)
    await edit_fn(
        chunks[0],
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    for chunk in chunks[1:]:
        await ctx.bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


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
        # 🅱️ Beta signup encouragement on first scan
        user_stats = db.get_user_stats(user.id)
        if user_stats.get("total_scans", 0) <= 1:
            signup_nudge = (
                "\n\n🅱️ <b>Liking RugMunch?</b> Sign up at "
                f'<a href="{WEBSITE_URL}/signup">rugmunch.io</a> to track your scans '
                "and get personalized alerts! 🚀"
            )
        else:
            signup_nudge = ""
        await msg.edit_text(
            format_v3_scan_report(data) + signup_nudge,
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
    """🅱️ Free Beta — all users get deep scan access."""
    return True, "beta"


def _deepscan_gate_text() -> str:
    return (
        "🅱️ <b>Deep Scan — Free During Beta!</b>\n\n"
        "SENTINEL forensic analysis is fully unlocked for all users:\n"
        "• Deployer history & cluster mapping\n"
        "• Bundle / wash-trade detection\n"
        "• Holder bubble map & scam indicators\n\n"
        "⭐ <b>Pro tiers launching soon</b> — sign up at rugmunch.io for early access!"
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
            "Premium forensic analysis — Scout+ or ⭐250 per scan.\n\n"
            "<b>What you get:</b>\n"
            "• Full safety rating with 5-category breakdown\n"
            "• Deployer investigation & wallet history\n"
            "• Bundle / sniper / wash-trade detection\n"
            "• Holder distribution & concentration\n"
            "• Wallet clusters & funding source tracing\n"
            "• SENTINEL forensic modules (when available)",
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

    allowed, _how = _can_deepscan(user.id)
    if not allowed:
        await update.message.reply_text(
            _deepscan_gate_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    # 🅱️ Free Beta — no stars deduction needed

    msg = await update.message.reply_text(
        f"🧠 <b>Running deep forensic scan...</b>\n<code>{_short(address)}</code>\n\n"
        f"🅱️ <b>Free Beta</b> — enjoy unlimited deep scans!\n\n"
        f"⏳ Aggregating: scoring API · deployer profile · wallet labels · holder bundles · cluster analysis · SENTINEL...",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Use the comprehensive aggregator — calls scoring, deployer, wallet,
        # holders, token-graph, and SENTINEL in parallel
        data = await run_deep_scan_aggregate(address, chain or detected)
        data["chain"] = chain or detected

        db.increment_usage(user.id, scan=True)
        db.log_scan(
            user.id,
            address,
            data["chain"],
            "deep",
            data.get("risk_score") or 0,
        )

        # Format the comprehensive report into Telegram-safe chunks
        chunks = format_comprehensive_deepscan(data)
        kb = scan_result_kb_v3(address, data["chain"])

        # Send first chunk (edits the "scanning..." message)
        await msg.edit_text(
            chunks[0],
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
        # Send remaining chunks as follow-up messages
        for chunk in chunks[1:]:
            await ctx.bot.send_message(
                chat_id=msg.chat.id,
                text=chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

    except Exception as exc:
        logger.error("Deep scan failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            "❌ <b>Deep Scan Failed</b>\n\n"
            "The deep forensic scan could not be completed. Please try again later.",
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
    if query is None or query.message is None:
        return
    user = query.from_user
    parts = (query.data or "").split("_", 2)
    if len(parts) < 3:
        return
    address, chain = parts[1], parts[2]

    allowed, _how = _can_deepscan(user.id)
    if not allowed:
        await query.edit_message_text(
            _deepscan_gate_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=pricing_kb(),
        )
        return
    # 🅱️ Free Beta — no stars deduction needed

    await query.edit_message_text(
        f"🧠 <b>Running deep forensic scan...</b>\n<code>{_short(address)}</code>\n\n"
        "🅱️ <b>Free Beta</b> — enjoy unlimited deep scans!\n\n"
        "⏳ Aggregating: scoring API · deployer profile · wallet labels · holder bundles · cluster analysis...",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await run_deep_scan_aggregate(address, chain)
        data["chain"] = chain
        db.increment_usage(user.id, scan=True)
        db.log_scan(
            user.id,
            address,
            chain,
            "deep",
            data.get("risk_score") or 0,
        )
        chunks = format_comprehensive_deepscan(data)
        kb = scan_result_kb_v3(address, chain)
        await query.edit_message_text(
            chunks[0],
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
        for chunk in chunks[1:]:
            await ctx.bot.send_message(
                chat_id=query.message.chat.id,
                text=chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    except Exception as exc:
        logger.error("Callback deep scan failed for %s: %s", _short(address), exc)
        await query.edit_message_text(
            "❌ <b>Deep Scan Failed</b>\n\nDeep forensic scan could not complete.",
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

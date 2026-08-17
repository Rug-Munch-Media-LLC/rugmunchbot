#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (cmd_scan + _poll_v2_scan_completion).

Split out of ``handlers/_scan_handlers.py`` (degodded 2026-08-16). Re-exported
by ``handlers._scan_handlers`` and ``handlers.scanning`` so existing
importers/tests keep working unchanged.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    backend_headers,
)
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    format_scan_report,
    is_owner,
    paywall_text,
)
from app.domains.telegram.rugmunchbot.handlers._image import _maybe_send_token_image
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    failure_card,
    token_not_found_card,
)
from app.domains.telegram.rugmunchbot.handlers._scan_handlers._progress import (
    _edit_stage_if_slow,
    _stop_task,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._delivery import _keep_typing
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    deep_scan_result_kb,
    scan_result_kb,
    upgrade_nudge_kb,
)
from app.domains.telegram.rugmunchbot.observability import record_scan
from app.domains.telegram.rugmunchbot.scanner import (
    scan_token,
    scan_token_v2,
    v2_token_to_legacy,
)
from app.domains.telegram.rugmunchbot.states import check_spam
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client
from app.domains.telegram.rugmunchbot.tools.scanner import (
    _scan_cache_get,
    _scan_cache_set,
    _supplement_deployer_socials,
    render_ai_verdict,
)

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Scan a token contract: usage, rate-limit gate, then live security scan."""
    record_scan("scan")
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f50d <b>Token Scanner</b>\n\n"
            "Usage: /scan <code>address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /scan <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /scan <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "\U0001f4a1 <i>Or just paste a contract address directly!</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            # Show upgrade nudge when hitting limit
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    started = time.monotonic()
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Scanning token...</b>\n<code>{target}</code>\n\n\u23f3 Running multi-source security checks...",
        parse_mode=ParseMode.HTML,
    )
    # Human-analyst feel: keep the typing bubble alive while the providers
    # run; the placeholder is refreshed with staged progress only when the
    # scan is genuinely slow (see _edit_stage_if_slow gates below).
    typing_task = asyncio.create_task(_keep_typing(msg.chat))
    try:
        # Short-TTL scan cache: repeat /scan of the same token is instant —
        # skip the whole provider chain (v2 POST + DexScreener supplement +
        # legacy fallback) within the 30s window. Fail-open: a miss rescans.
        chain_key = chain or detect_chain(target)
        cached = _scan_cache_get(target, chain_key)
        if cached is not None:
            logger.debug("cmd_scan cache hit for %s", target)
            result = cached
        else:
            result_v2 = await scan_token_v2(target, chain)
            if result_v2 is not None:
                result = v2_token_to_legacy(result_v2)
                # Supplement with DexScreener data when the v2 payload left
                # liquidity / deployer / socials unresolved (free-tier scans
                # skip the backend deployer module, so this is what fills the
                # report's Deployer + Socials blocks). One fetch, fail-open,
                # real data only.
                result = await _supplement_deployer_socials(result, target, chain or str(result.get("chain") or ""))
            else:
                # v2 unavailable → fall back to legacy multi-source scanner
                result = await scan_token(target, chain)
            _scan_cache_set(target, chain_key, result)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                token_not_found_card(target, cmd="scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        # Stage 2 (gated >3s): providers answered — surface the holder /
        # deployer analysis step only when the scan actually took time.
        await _edit_stage_if_slow(
            msg,
            f"\U0001f468\u200d\U0001f4bb <b>Analyzing deployer + holders...</b>\n<code>{target}</code>",
            started=started,
            min_elapsed=3.0,
        )
        report = format_scan_report(result)
        # AI verdict summary on top: the snappy LLM reads the structured scan
        # facts into a 2-4 sentence plain-English verdict + "what to watch".
        # Fail-open — when the LLM is unavailable the report's existing
        # template verdict (format_verdict_header) stays as-is.
        ai_verdict = await render_ai_verdict(result)
        if ai_verdict:
            report = f"{ai_verdict}\n\n{report}"
        # Stage 3 (gated >6s): the verdict is composed — show it only once the
        # whole flow has genuinely taken time (LLM verdict + providers).
        await _edit_stage_if_slow(
            msg,
            f"\U0001f9e0 <b>Writing verdict...</b>\n<code>{target}</code>",
            started=started,
            min_elapsed=6.0,
        )
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "basic", result["risk_score"])
        # Use richer keyboard for premium users — reference/trade links now
        # live in the report footer, so the keyboard is actions only.
        user_tier = db.get_user_tier(user.id)
        if user_tier in ("hunter", "pro"):
            result_kb = deep_scan_result_kb(target, result["chain"])
        else:
            result_kb = scan_result_kb(target, result["chain"])
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=result_kb,
            disable_web_page_preview=True,
        )
        # Token image "pops up" as a photo reply (fail-open, text stays).
        await _maybe_send_token_image(msg, result)
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.error(f"Scan error: {e}")
        await msg.edit_text(
            failure_card("Scan Failed", "The scanner hit an unexpected error.", ACTION_CHECK_ADDRESS),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    finally:
        # The report (or failure card) has landed — drop the typing bubble.
        await _stop_task(typing_task)


async def _poll_v2_scan_completion(address: str, chain: str, max_wait_s: float = 24.0) -> dict[str, Any] | None:
    """Poll the v2 scan status endpoint until background phases complete.

    The v2 scan API returns within ~25s while deep phases (wallet metrics,
    SENTINEL, news) continue in a background task. /deepscan waits a little
    longer so the wallet-composition section has real data. Returns the
    completed status payload (legacy serializer shape), or None on
    timeout/failure — the caller then renders whatever the fast path had.
    """
    url = f"{BACKEND_URL}/api/v2/scanner/token/scan/{chain}/{address}/status"
    deadline = time.monotonic() + max_wait_s
    await asyncio.sleep(4)  # head start for the background phases
    while time.monotonic() < deadline:
        try:
            client = get_shared_client(10)
            r = await client.get(url, headers=backend_headers())
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("background_status") == "complete":
                    return data
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as exc:
            logger.debug("v2 scan status poll failed for %s: %s", address[:12], exc)
        await asyncio.sleep(4)
    return None

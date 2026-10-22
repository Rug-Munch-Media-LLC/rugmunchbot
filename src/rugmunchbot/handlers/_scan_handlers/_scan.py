#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (cmd_scan + _poll_v2_scan_completion).

Split out of ``handlers/_scan_handlers.py`` (degodded 2026-08-16). Re-exported
by ``handlers._scan_handlers`` and ``handlers.scanning`` so existing
importers/tests keep working unchanged.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import (
    BACKEND_URL,
    backend_headers,
)
from rugmunchbot.formatters import detect_chain, is_owner, paywall_text
from rugmunchbot.formatters._vcard import format_verdict_card
from rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    failure_card,
    token_not_found_card,
)
from rugmunchbot.handlers._scan_handlers._progress import (
    _edit_stage_if_slow,
    _stop_task,
)
from rugmunchbot.handlers._scanning_helpers import _merge_completed_scan
from rugmunchbot.handlers.chat_ai._delivery import _keep_typing
from rugmunchbot.keyboards import back_kb, upgrade_nudge_kb, vcard_kb
from rugmunchbot.observability import record_scan
from rugmunchbot.scanner import (
    apply_parity,
    scan_token,
    scan_token_v2,
    v2_token_to_legacy,
)
from rugmunchbot.states import check_spam
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client
from rugmunchbot.tools.scanner import (
    _scan_cache_get,
    _scan_cache_set,
    _supplement_deployer_socials,
)

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

# 2026-08-25 quality push: how long /scan waits for the engine's background
# phases (bundle/cluster, wallet metrics, first buyers) before rendering the
# basic card. The engine computes these for EVERY tier — the bot just waits
# briefly so the FIRST card already carries them. Bounded so UX stays snappy;
# /deepscan keeps its longer budget. Env-overridable for ops.
_BASIC_SCAN_POLL_MAX_S = float(os.getenv("RMI_SCAN_BASIC_POLL_MAX_S", "9"))
_BASIC_SCAN_POLL_HEAD_START_S = 2.5
_BASIC_SCAN_POLL_INTERVAL_S = 2.5


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
    # One message per scan: the skeleton lands immediately (paste-to-scan must
    # feel instant), then the SAME message is edited with the verdict card.
    # Deliberately no hash / no full address in the skeleton — it is replaced
    # seconds later and the card carries the identity.
    short = f"{target[:4]}\u2026{target[-4:]}" if len(target) > 10 else target
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f504 Scanning <code>{short}</code>\u2026",
        parse_mode=ParseMode.HTML,
    )
    # Human-analyst feel: keep the typing bubble alive while the providers
    # run; the placeholder is refreshed with staged progress only when the
    # scan is genuinely slow (see _edit_stage_if_slow gates below).
    typing_task = asyncio.create_task(_keep_typing(msg.chat))
    try:
        # 2026-08-25 quality push: resolve $TICKER / free-text targets to a
        # REAL contract address BEFORE scanning. The old flow passed
        # "$smolcat" into every provider as if it were an address — all
        # missed, yet the report still rendered a confident empty verdict.
        from rugmunchbot.tools.scanner._resolve_target import resolve_scan_target

        resolved = await resolve_scan_target(target, chain)
        if resolved is None:
            await msg.edit_text(
                token_not_found_card(target, cmd="scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        if resolved.resolved_from == "dexscreener_search":
            target = resolved.address
            chain = resolved.chain or chain
            await msg.edit_text(  # type: ignore[union-attr]
                f"\U0001f504 <b>Scanning</b> <code>${resolved.query}</code>\n"
                f"<code>{target}</code>\n\n"
                "\u23f3 Resolved from live DEX data \u2014 running multi-source security checks...",
                parse_mode=ParseMode.HTML,
            )
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
                # 2026-08-25 quality push: bundle/cluster analysis, wallet
                # metrics (fresh/CEX-funded %) and first-buyers complete in
                # the engine's background phases seconds AFTER the fast-path
                # response. Wait briefly (bounded) so the FIRST basic card
                # already carries them — one scanner, the bot only renders.
                # Fail-open: on timeout we render whatever the fast path had.
                if result_v2.get("background_status") == "running":
                    completed = await _poll_v2_scan_completion(
                        target,
                        chain_key,
                        max_wait_s=_BASIC_SCAN_POLL_MAX_S,
                        head_start_s=_BASIC_SCAN_POLL_HEAD_START_S,
                        interval_s=_BASIC_SCAN_POLL_INTERVAL_S,
                    )
                    if completed:
                        _merge_completed_scan(result_v2, completed)
                result = await apply_parity(v2_token_to_legacy(result_v2), target, chain_key)
            else:
                # v2 unavailable → fall back to legacy multi-source scanner
                from rugmunchbot.tools.scanner._section_log import log_section_fallback

                log_section_fallback("scan", "v2", "legacy", reason="v2_unavailable")
                result = await scan_token(target, chain)
            # Market/deployer/socials backfill runs on BOTH paths (2026-08-25):
            # the report's Market Data / Deployer / Socials blocks must show
            # real liquidity, volume, price change, creator and links even
            # when the v2 backend left them unresolved. Idempotent,
            # never clobbers existing values, real data only.
            result = await _supplement_deployer_socials(
                result, target, chain or str(result.get("chain") or "")
            )
            _scan_cache_set(target, chain_key, result)
        # A token is "not found" when the v2 scanner couldn't even compute a
        # safety score (no DexScreener data, no chain data at all). If safety_score
        # is set (even 0), the scanner found the token on-chain — pump.fun tokens
        # often have no name/symbol but still get scored.
        has_scan_data = (
            result.get("safety_score") is not None
            or result.get("modules_run")
            or bool(result.get("evidence"))
        )
        if result["name"] == "Unknown" and not result["errors"] and not has_scan_data:
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
        # Verdict-first card (PirbView wins): verdict word + score + symbol on
        # line 1, one safety row, per-cohort clusters, provenance on every
        # number, market last. Rendered deterministically (no LLM round-trip,
        # no second message) so the card lands as fast as the data does. The
        # full legacy report stays one tap away on the card's keyboard.
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "basic", result["risk_score"])
        await msg.edit_text(
            format_verdict_card(result),
            parse_mode=ParseMode.HTML,
            reply_markup=vcard_kb(target, result["chain"]),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.error(f"Scan error: {e}")
        await msg.edit_text(
            failure_card(
                "Scan Failed", "The scanner hit an unexpected error.", ACTION_CHECK_ADDRESS
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    finally:
        # The report (or failure card) has landed — drop the typing bubble.
        await _stop_task(typing_task)


async def _poll_v2_scan_completion(
    address: str,
    chain: str,
    max_wait_s: float = 24.0,
    head_start_s: float = 4.0,
    interval_s: float = 4.0,
) -> dict[str, Any] | None:
    """Poll the v2 scan status until background phases complete.

    The v2 scan API returns within ~25s while deep phases (wallet metrics,
    SENTINEL, news) continue in a background task. /deepscan waits a little
    longer so the wallet-composition section has real data; /scan uses a
    shorter bounded budget (see ``_BASIC_SCAN_POLL_MAX_S``). Returns the
    completed result payload (legacy serializer shape — the dict carrying
    ``wallet_metrics`` / ``bundle_analysis`` / ``first_buyers`` /
    ``evidence`` top-level, exactly what ``_merge_completed_scan`` reads),
    or None on timeout/failure — the caller then renders whatever the fast
    path had.

    Thin client (2026-09-07): the bot polls ONLY the backend HTTP status
    endpoint — the vendored in-process scanner engine is no longer invoked
    from handlers (backend owns the data).

    Payload shape: the status endpoint nests the scan result under
    ``result`` (``{"state": ..., "phase": ..., "result": {...},
    "updated_at": ...}`` — also used by the 1h result-cache path). Older
    backends returned the flat payload. Both shapes are accepted.
    """
    url = f"{BACKEND_URL}/api/v2/scanner/token/scan/{chain}/{address}/status"
    deadline = time.monotonic() + max_wait_s
    await asyncio.sleep(head_start_s)  # head start for the background phases
    while time.monotonic() < deadline:
        try:
            client = get_shared_client(10)
            r = await client.get(url, headers=backend_headers())
            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, dict):
                    continue
                payload = data.get("result")
                if not isinstance(payload, dict):
                    payload = data
                # Complete when the nested/flat result says so, OR the
                # wrapper reports state=complete (result-cache path).
                if (
                    payload.get("background_status") == "complete"
                    or data.get("state") == "complete"
                ):
                    return payload
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as exc:
            logger.debug("v2 scan status poll failed for %s: %s", address[:12], exc)
        await asyncio.sleep(interval_s)
    return None

#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (cmd_deepscan + sentinel_scan).

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
    DEXSCREENER,
    backend_headers,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    format_deep_scan_report,
    format_sentinel_deepscan_section,
    format_wallet_composition_section,
    is_owner,
    paywall_text,
    render_analyst_read_block,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.handlers._image import _maybe_send_token_image
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    ACTION_TRY_AGAIN,
    failure_card,
    token_not_found_card,
)
from app.domains.telegram.rugmunchbot.handlers._scan_handlers._progress import (
    _edit_stage_if_slow,
    _staged_progress,
    _stop_task,
)
from app.domains.telegram.rugmunchbot.handlers._scan_handlers._scan import _poll_v2_scan_completion
from app.domains.telegram.rugmunchbot.handlers._scanning_helpers import (
    _ds_created_iso,
    _ds_pairs,
    _merge_completed_scan,
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
    sentinel_scan as fetch_sentinel_scan,
    v2_token_to_legacy,
)
from app.domains.telegram.rugmunchbot.states import check_spam
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client
from app.domains.telegram.rugmunchbot.tools.scanner import (
    _supplement_deployer_socials,
)

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def cmd_deepscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Full multi-phase deep scan (v6).

    Runs ALL scanner phases and enriches the result with RugMaps cluster
    data + on-chain intel for a much more detailed report than /scan.
    Slower (~1-2 min) but shows every phase it ran.
    """
    record_scan("deepscan")
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f52c <b>Deep Token Scan</b>\n\n"
            "Usage: /deepscan <code>address</code> [chain]\n\n"
            "Runs ALL phases:\n"
            "\u2460 Market data (price, volume, liquidity)\n"
            "\u2461 Contract security (honeypot, mint, taxes)\n"
            "\u2462 Holder analysis (top holders, concentration)\n"
            "\u2463 Deployer fingerprint (dev history, rugs)\n"
            "\u2464 Funding analysis (fresh wallets, bundles)\n"
            "\u2465 AI risk verdict + RugMap cluster data\n\n"
            "\u23f3 Slower than /scan (\u2248 1-2 min) but far more detailed.",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    started = time.monotonic()
    msg = await update.message.reply_text(
        f"\U0001f52c <b>Deep scan started...</b>\n"
        f"<code>{target}</code>\n\n"
        f"\u25b6\ufe0f \u2460 Market \u2192 \u2461 Security \u2192 \u2462 Holders \u2192 "
        f"\u2463 Deployer \u2192 \u2464 Funding \u2192 \u2465 AI verdict\n"
        f"\u23f3 This can take up to 2 minutes...",
        parse_mode=ParseMode.HTML,
    )
    # Keep the typing bubble alive for the whole multi-phase run.
    typing_task = asyncio.create_task(_keep_typing(msg.chat))
    # Phase 1: Market data
    await asyncio.sleep(2)
    try:
        await msg.edit_text(
            f"\U0001f52c <b>Deep scan in progress...</b>\n"
            f"<code>{target}</code>\n\n"
            f"\u2705 \u2460 Market \u2192 \u25b6\ufe0f \u2461 Security \u2192 \u2462 Holders \u2192 "
            f"\u2463 Deployer \u2192 \u2464 Funding \u2192 \u2465 AI verdict",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.debug("deepscan phase-1 progress edit failed", exc_info=True)
    try:
        result_v2 = await scan_token_v2(target, chain)
        if (
            result_v2 is not None
            and result_v2.get("background_status") == "running"
            and not result_v2.get("wallet_metrics")
        ):
            # Deep phases still running — wait briefly so the wallet
            # composition (fresh/CEX/brand-new %) lands before we render.
            completed = await _poll_v2_scan_completion(target, chain or result_v2.get("chain") or detect_chain(target))
            if completed:
                _merge_completed_scan(result_v2, completed)
        # Phase 2: Security + Holders complete
        try:
            await msg.edit_text(
                f"\U0001f52c <b>Deep scan in progress...</b>\n"
                f"<code>{target}</code>\n\n"
                f"\u2705 \u2460 Market \u2705 \u2461 Security \u2705 \u2462 Holders \u2192 "
                f"\u25b6\ufe0f \u2463 Deployer \u2192 \u2464 Funding \u2192 \u2465 AI verdict",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            logger.debug("deepscan phase-2 progress edit failed", exc_info=True)
        if result_v2 is not None:
            result = v2_token_to_legacy(result_v2)
            # Fill deployer + socials (and backfill liquidity) from
            # DexScreener when the v2 payload left them unresolved.
            result = await _supplement_deployer_socials(result, target, chain or str(result.get("chain") or ""))
        else:
            result = await scan_token(target, chain)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                token_not_found_card(target, cmd="deepscan"),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        # ── Enrichment phase (best-effort, non-blocking) ──
        enrichment_lines: list[str] = []
        chain_norm = chain or result.get("chain") or detect_chain(target)
        token_created_ts: str | None = None
        try:
            client = get_shared_client(15)
            g_r, i_r = await asyncio.gather(
                client.get(
                    f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
                    params={"token_address": target, "chain": chain_norm, "limit": 30},
                    headers=backend_headers(),
                    timeout=12,
                ),
                client.get(
                    f"{DEXSCREENER}/tokens/{target}",
                    timeout=10,
                ),
                return_exceptions=True,
            )
            if isinstance(g_r, httpx.Response) and g_r.status_code == 200:
                gd = g_r.json()
                nodes = gd.get("nodes") or []
                links = gd.get("links") or []
                enrichment_lines.append(
                    f"\U0001f5fa\ufe0f <b>RugMap Cluster</b>\n{thin_sep()}\n"
                    f"\U0001f465 Nodes: <b>{len(nodes)}</b> | Links: <b>{len(links)}</b>"
                )
                if gd.get("risk_level"):
                    enrichment_lines.append(
                        f"\u26a0\ufe0f Graph Risk: <b>{gd.get('risk_level', 'unknown').upper()}</b> "
                        f"({gd.get('risk_score', 0)})"
                    )
                if gd.get("similar_scams"):
                    enrichment_lines.append(f"\U0001f6a8 Similar scams: {len(gd['similar_scams'])}")
            if isinstance(i_r, httpx.Response) and i_r.status_code == 200:
                pairs = _ds_pairs(i_r.json())
                if pairs:
                    p = pairs[0]
                    token_created_ts = _ds_created_iso(p)
                    enrichment_lines.append(
                        f"\U0001f4c8 <b>Market Snapshot</b>\n{thin_sep()}\n"
                        f"\U0001f4b0 {fmt_number(p.get('priceUsd', 0))} | "
                        f"\U0001f4c8 MCap {fmt_number(p.get('marketCap') or 0)} | "
                        f"\U0001f4a7 Liq {fmt_number(p.get('liquidity', {}).get('usd') or 0)}\n"
                        f"\U0001f4ca 24h Vol {fmt_number(p.get('volume', {}).get('h24') or 0)} | "
                        f"24h {fmt_pct(p.get('priceChange', {}).get('h24') or 0)}"
                    )
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError):
            logger.debug("deepscan enrichment unavailable for %s", target, exc_info=True)

        # Wallet composition from the own funding_analysis engine (fresh /
        # brand-new / CEX-funded %) — the deepscan-only forensic block.
        # ``token_created_ts`` (DexScreener pair creation) lets the formatter
        # suppress fresh/brand-new stats when the indexer's coverage starts
        # long after the token launched (coverage artifact, not a signal).
        wallet_section = format_wallet_composition_section(result, token_created_ts=token_created_ts)
        if wallet_section:
            enrichment_lines.append(wallet_section)

        # Stage 3 (gated >10s): enrichment is done — the verdict is being
        # composed. Deep scans are always slow, so this reads naturally.
        await _edit_stage_if_slow(
            msg,
            f"\U0001f9e0 <b>Writing verdict...</b>\n<code>{target}</code>",
            started=started,
            min_elapsed=10.0,
        )

        # Canonical deep-scan report: the /scan skeleton plus deep-only intel
        # (LP lock, bundle, snipers/insiders) and the enrichment sections
        # (RugMap cluster, market snapshot, wallet composition) rendered
        # INSIDE the report — before the footer, never appended after it.
        report = format_deep_scan_report(result, extra_sections=enrichment_lines)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "deep", result["risk_score"])
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
        logger.debug("cmd_deepscan_error", exc_info=True)
        logger.error(f"Deep scan error: {e}")
        await msg.edit_text(
            failure_card("Deep Scan Failed", "The deep scanner hit an unexpected error.", ACTION_CHECK_ADDRESS),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    finally:
        # The report (or failure card) has landed — drop the typing bubble.
        await _stop_task(typing_task)


async def sentinel_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """SENTINEL deep forensic scan — POST /api/v1/x402-tools/sentinel_scan.

    Standalone command for the 9-module SENTINEL pipeline (holder HHI, LP
    lock, dev reputation, contract authority, proxy, bundle, wash trading,
    honeypot). Slower than /deepscan but runs every forensic module and
    renders via format_sentinel_deepscan_section.
    """
    record_scan("sentinel")
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f52c <b>AEGIS Deep Scan</b>\n\n"
            "Usage: /sentinelscan <code>address</code> [chain]\n\n"
            "Runs the full 9-module forensic pipeline:\n"
            "\u2460 Holder HHI & concentration\n"
            "\u2461 LP lock & liquidity verification\n"
            "\u2462 Dev reputation & rug rate\n"
            "\u2463 Contract authority (proxy, mint, taxes)\n"
            "\u2464 Bundle & wash trading detection\n"
            "\u2465 Honeypot analysis\n"
            "\u2466 Composite risk score + red flags\n\n"
            "\u23f3 Slowest scan (\u2248 1-2 min) \u2014 most thorough.",
            parse_mode=ParseMode.HTML,
        )
        return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    if not is_owner(user.id):
        allowed, _used, _limit = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    started = time.monotonic()
    msg = await update.message.reply_text(
        "\U0001f52c <b>AEGIS scanning...</b>\n\n\U0001f4aa Running all 9 forensic modules \u2014 this takes a bit.",
        parse_mode=ParseMode.HTML,
    )
    typing_task = asyncio.create_task(_keep_typing(msg.chat))
    progress_task: asyncio.Task[Any] | None = None
    try:
        # Run the forensic pipeline in the background and drive the placeholder
        # through staged, elapsed-gated progress edits while it works — a
        # multi-second scan reads like an analyst, not a spinner.
        scan_task = asyncio.create_task(fetch_sentinel_scan(target, chain))
        progress_task = asyncio.create_task(
            _staged_progress(
                msg,
                [
                    ("\U0001f50d <b>Scanning security providers...</b>", 2.0),
                    ("\U0001f468\u200d\U0001f4bb <b>Analyzing deployer + holders...</b>", 6.0),
                    ("\U0001f9e0 <b>Writing verdict...</b>", 12.0),
                ],
                started=started,
            )
        )
        result = await scan_task
        # Stop the stager BEFORE the final edit so it can never clobber it.
        await _stop_task(progress_task)
        if not result:
            await msg.edit_text(
                failure_card("AEGIS Scan Failed", "Could not reach the forensic pipeline.", ACTION_TRY_AGAIN),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        db.increment_usage(user.id, scan=True)
        # AI "Analyst Read" — plain-language bundle/cluster interpretation,
        # rendered BEFORE the deterministic sections. Fail-open: empty block
        # (LLM down / no bundle data) is skipped entirely.
        analyst_read = await render_analyst_read_block(result)
        report = format_sentinel_deepscan_section(result, analyst_read=analyst_read)
        report += f"\n\n{footer_links()}"
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb(target, result.get("chain") or chain or "unknown"),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("sentinel_scan_error", exc_info=True)
        logger.error(f"SENTINEL scan error: {e}")
        await msg.edit_text(
            failure_card("AEGIS Scan Failed", "The forensic pipeline hit an unexpected error.", ACTION_CHECK_ADDRESS),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    finally:
        await _stop_task(typing_task)
        await _stop_task(progress_task)

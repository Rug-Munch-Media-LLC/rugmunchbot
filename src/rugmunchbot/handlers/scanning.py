#!/usr/bin/env python3
"""
RugMunchBot -- Core scanning command handlers.

Commands: start, help, scan, wallet, ta, compare, quick, rugcheck

NOTE (degodding 2026-08-14): the /help reference text + render helpers
(``_HELP_SECTIONS``, ``_HELP_EMOJI``, ``CHANNEL_LABELS``, ``_channel_url``,
``_channels_section``, ``_build_help_text``, ``HELP_TEXT``) live in
``handlers/_help_text.py`` and are re-exported here — no caller changes.
Command handlers remain in this module.

Ported from the private monorepo (rmi-backend rugmunchbot) — import-rewritten
to ``rugmunchbot.*`` per docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md §2.3.
Unresolvable seams (scanner engines, _help_text, _engagement, onboarding) are
marked ``# TODO(port)`` + ``[REVIEW]`` — they land in later extraction stages.
The Advanced private payment-gate endpoint reference is scrubbed per §6.13
(operator-supplied scan provider instead). [REVIEW]
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    backend_headers,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from rugmunchbot.daily_context import get_market_section
from rugmunchbot.formatters import (
    _price_diff_pct,
    _risk_indicator,
    detect_chain,
    faq_text,
    footer_links,
    format_advanced_deepscan_section,
    format_scan_report,
    format_wallet_composition_section,
    format_wallet_report,
    is_evm,
    is_owner,
    is_sol,
    paywall_text,
    sep,
    thin_sep,
    web_scan_button,
    welcome_card_text,
)
from rugmunchbot.formatters._advanced import _token_image_url
from rugmunchbot.formatters._base import dex_button_rows
from rugmunchbot.handlers._engagement import (  # TODO(port): handlers/_engagement.py is NOT yet in the OSS scaffold — needs the rotating-CTA + channel-shill helpers ported (checklist §2.1; live file is pure helpers) [REVIEW]
    channels_menu_text,
    rotating_cta_for,
)
from rugmunchbot.handlers._help_text import (  # noqa: F401  # re-exported for callers/tests  # TODO(port): handlers/_help_text.py is still a scaffold stub — /help reference text + render helpers port in Stage 4 [REVIEW]
    _HELP_EMOJI,
    _HELP_SECTIONS,
    CHANNEL_LABELS,
    HELP_TEXT,
    _build_help_text,
    _channel_url,
    _channels_section,
)
from rugmunchbot.handlers._scanning_helpers import (  # noqa: F401  # re-exported for tests
    _DEPLOYER_BUCKET_EMOJI,
    _bucket_emoji,
    _ds_created_iso,
    _ds_pairs,
    _merge_completed_scan,
    _ta_arrow,
    _ta_rsi_emoji,
    _ta_vol_arrow,
    format_ta_card,
)
from rugmunchbot.handlers.onboarding import (  # TODO(port): handlers/onboarding.py is NOT yet in the OSS scaffold — first-run welcome + daily-hook helpers land in Stage 4 [REVIEW]
    is_first_run,
    send_onboarding_welcome,
)
from rugmunchbot.keyboards import (
    back_kb,
    daily_hook_kb,
    deep_scan_result_kb,
    merge_menu_kb,
    owner_admin_reply_kb,
    scan_result_kb,
    upgrade_nudge_kb,
    welcome_menu_kb,
)
from rugmunchbot.scanner import (  # TODO(port): rugmunchbot/scanner.py is still a scaffold stub — scan engines (gmgn/scan_memory coupling) land in Stage 3 via ScanProvider; scanning.py import hard-fails until then (checklist §5, [REVIEW] #2) [REVIEW]
    advanced_scan as fetch_advanced_scan,
    analyze_wallet,
    analyze_wallet_v2,
    fetch_deployer_fingerprint,
    fetch_hype_score,
    scan_token,
    scan_token_v2,
    v2_token_to_legacy,
    v2_wallet_to_legacy,
)
from rugmunchbot.states import check_spam
from rugmunchbot.support.logging import get_logger
from rugmunchbot.ta_indicators import analyze_candles

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


def _with_trade_buttons(kb: InlineKeyboardMarkup, address: str, chain: str) -> InlineKeyboardMarkup:
    """Return ``kb`` with the referral trade-button rows inserted.

    Inserts ``dex_button_rows(address, chain)`` (chain-filtered DEX_REF_LINKS
    with ref codes + DexScreener/DexTools) right after the first row so the
    trade buttons ride on the SAME message as the scan report. Used for the
    premium-tier keyboards (``deep_scan_result_kb``), which do not otherwise
    include the trade row; ``scan_result_kb`` already embeds it.
    """
    trade_rows = dex_button_rows(address, chain)
    if not trade_rows:
        return kb
    rows = [list(row) for row in kb.inline_keyboard]
    rows[1:1] = trade_rows
    return InlineKeyboardMarkup(rows)


async def _maybe_send_token_image(msg: Any, result: dict[str, Any]) -> None:
    """Send the token image as a photo reply when the scan has one.

    Reads ``image_uri`` / ``image_url`` / ``logo_url`` off the scan result
    (see ``formatters._advanced._token_image_url``). Fail-open: no image URL
    → text-only reply, no crash; a dead image host / Telegram error is
    logged and swallowed so the scan reply is never lost.
    """
    url = _token_image_url(result)
    if not url:
        return
    try:
        await msg.reply_photo(
            photo=url,
            caption=f"\U0001f5bc\ufe0f {result.get('name', '')} ({result.get('symbol', '')})",
        )
    except Exception as e:
        logger.warning("token image send failed, continuing text-only: %s", e)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    u = db.get_or_create_user(user.id, user.username, user.first_name)

    # Referral handling
    if ctx.args and ctx.args[0].startswith("ref_"):
        ref_code = ctx.args[0][4:]
        if ref_code != u.get("referral_code") and not u.get("referred_by"):
            conn = db.get_db()
            referrer = conn.execute(
                "SELECT user_id FROM users WHERE referral_code = ?", (ref_code,)
            ).fetchone()
            if referrer:
                conn.execute(
                    "UPDATE users SET referred_by = ?, bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (ref_code, user.id),
                )
                conn.execute(
                    "UPDATE users SET bonus_scans = bonus_scans + 5 WHERE user_id = ?",
                    (referrer["user_id"],),
                )
                conn.commit()
            conn.close()

    # First-run onboarding: punchy welcome + quick-start buttons. Returning
    # users get the standard welcome card with the daily hook attached.
    if is_first_run(u):
        await send_onboarding_welcome(update, ctx)
    else:
        text = welcome_card_text()

        # Live daily market awareness: compact "Today's Market" section
        # (Redis-cached 5 min, fail-open — /start never blocks on market data).
        market_section = await get_market_section()
        if market_section:
            text += f"\n\n{market_section}"

        # Channel shill: compact "📢 Our Channels" block (public handles only).
        text += f"\n\n{channels_menu_text()}"

        # Returning-user engagement: one rotating, non-spammy footer CTA line
        # (deterministic per user+day — never more than one line).
        text += f"\n{rotating_cta_for(user.id)}"

        # Owner-only admin section — visible only to the configured owner.
        if is_owner(user.id):
            text += (
                "\n\n\U0001f6e1\ufe0f <b>ADMIN</b>\n"
                "\u2514 /admin \u2014 Dashboard & stats\n"
                "\u2514 /admin_broadcast \u2014 Send to all users\n"
                "\u2514 /admin_user \u2014 Look up a user\n"
                "\u2514 /admin_toggle \u2014 Bot ON/OFF\n"
                "\u2514 \U0001f447 Admin buttons are pinned below the input box"
            )
        kb = welcome_menu_kb(user.id)
        # Daily hook: one-tap "what's hot right now" + market pulse for
        # returning users — instant value, zero new callback handlers.
        kb = merge_menu_kb(kb, daily_hook_kb())
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
        )

    # v7: attach the persistent admin reply keyboard for the owner in DMs so
    # admin commands stay one tap away on every message.
    if is_owner(user.id) and update.effective_chat and update.effective_chat.type == "private":
        from rugmunchbot.handlers.message import (
            admin_kb_mark_sent,
            admin_kb_reset,
        )

        admin_kb_reset(user.id)
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f6e1\ufe0f <b>Admin console</b>\n"
            "Tap a button below for quick admin actions \u2014 they stay pinned here.",
            parse_mode=ParseMode.HTML,
            reply_markup=owner_admin_reply_kb(),
        )
        admin_kb_mark_sent(user.id)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Full command reference (v10) — grouped by category:
    Scan & Security / Wallet & Tracing / Market & Analysis / News & Alerts /
    Chat & Learn / Premium, plus Account & Extras."""
    user = update.effective_user
    kb = welcome_menu_kb(user.id if user else None)
    text = HELP_TEXT
    # Channel shill: compact "📢 Our Channels" block (public handles only).
    text += f"\n\n{channels_menu_text()}"
    # Engagement polish: one rotating, non-spammy "what next" CTA line.
    if user is not None:
        text += f"\n\n{rotating_cta_for(user.id)}"
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def cmd_faq(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Lightweight FAQ — plain-language answers to common questions
    (what can you scan / is it free / what is RMI / how accurate)."""
    user = update.effective_user
    kb = welcome_menu_kb(user.id if user else None)
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        faq_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
        disable_web_page_preview=True,
    )


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f50d <b>Scanning token...</b>\n<code>{target}</code>\n\n\u23f3 Running multi-source security checks...",
        parse_mode=ParseMode.HTML,
    )
    try:
        result_v2 = await scan_token_v2(target, chain)
        if result_v2 is not None:
            result = v2_token_to_legacy(result_v2)
            # Supplement with DexScreener data if v2 liquidity is 0
            if not result.get("liquidity"):
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        r = await client.get(f"{DEXSCREENER}/tokens/{target}")
                        if r.status_code == 200:
                            pairs = r.json().get("pairs", [])
                            if pairs:
                                p = pairs[0]
                                liq = p.get("liquidity", {})
                                if liq.get("usd"):
                                    result["liquidity"] = float(liq["usd"])
                                if not result.get("name") or result["name"] == "Unknown":
                                    result["name"] = p.get("baseToken", {}).get("name", "Unknown")
                                    result["symbol"] = p.get("baseToken", {}).get("symbol", "???")
                except Exception:
                    pass
        else:
            # v2 unavailable → fall back to legacy multi-source scanner
            result = await scan_token(target, chain)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                f"\u274c <b>Token Not Found</b>\n\n"
                f"No data found for <code>{target}</code>.\n\n"
                f"\u2022 Check the address is correct\n"
                f"\u2022 Ensure the token has a trading pair\n"
                f"\u2022 Try specifying the chain: /scan <code>addr</code> solana",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        report = format_scan_report(result)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "basic", result["risk_score"])
        # Use richer keyboard for premium users
        user_tier = db.get_user_tier(user.id)
        if user_tier in ("hunter", "pro"):
            # deep_scan_result_kb has no trade row — attach the ref buttons
            # (same message as the report) via _with_trade_buttons.
            result_kb = _with_trade_buttons(
                deep_scan_result_kb(target, result["chain"]), target, result["chain"]
            )
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
            f"\u274c <b>Scan Failed</b>\n\nError: {str(e)[:200]}\n\nPlease check the address and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _poll_v2_scan_completion(
    address: str, chain: str, max_wait_s: float = 24.0
) -> dict[str, Any] | None:
    """Poll the v2 scan status endpoint until background phases complete.

    The v2 scan API returns within ~25s while deep phases (wallet metrics,
    Advanced, news) continue in a background task. /deepscan waits a little
    longer so the wallet-composition section has real data. Returns the
    completed status payload (legacy serializer shape), or None on
    timeout/failure — the caller then renders whatever the fast path had.
    """
    url = f"{BACKEND_URL}/api/v2/scanner/token/scan/{chain}/{address}/status"
    deadline = time.monotonic() + max_wait_s
    await asyncio.sleep(4)  # head start for the background phases
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, headers=backend_headers())
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict) and data.get("background_status") == "complete":
                    return data
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError) as exc:
            logger.debug("v2 scan status poll failed for %s: %s", address[:12], exc)
        await asyncio.sleep(4)
    return None


async def cmd_deepscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Full multi-phase deep scan (v6).

    Runs ALL scanner phases and enriches the result with RugMaps cluster
    data + on-chain intel for a much more detailed report than /scan.
    Slower (~1-2 min) but shows every phase it ran.
    """
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
    msg = await update.message.reply_text(
        f"\U0001f52c <b>Deep scan started...</b>\n"
        f"<code>{target}</code>\n\n"
        f"\u25b6\ufe0f \u2460 Market \u2192 \u2461 Security \u2192 \u2462 Holders \u2192 "
        f"\u2463 Deployer \u2192 \u2464 Funding \u2192 \u2465 AI verdict\n"
        f"\u23f3 This can take up to 2 minutes...",
        parse_mode=ParseMode.HTML,
    )
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
        pass
    try:
        result_v2 = await scan_token_v2(target, chain)
        if (
            result_v2 is not None
            and result_v2.get("background_status") == "running"
            and not result_v2.get("wallet_metrics")
        ):
            # Deep phases still running — wait briefly so the wallet
            # composition (fresh/CEX/brand-new %) lands before we render.
            completed = await _poll_v2_scan_completion(
                target, chain or result_v2.get("chain") or detect_chain(target)
            )
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
            pass
        if result_v2 is not None:
            result = v2_token_to_legacy(result_v2)
        else:
            result = await scan_token(target, chain)
        if result["name"] == "Unknown" and not result["errors"]:
            await msg.edit_text(
                f"\u274c <b>Token Not Found</b>\n\n"
                f"No data found for <code>{target}</code>.\n\n"
                f"\u2022 Check the address is correct\n"
                f"\u2022 Try specifying the chain: /deepscan <code>addr</code> solana",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        # ── Enrichment phase (best-effort, non-blocking) ──
        enrichment_lines: list[str] = []
        chain_norm = chain or result.get("chain") or detect_chain(target)
        token_created_ts: str | None = None
        try:
            async with httpx.AsyncClient(timeout=15) as client:
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
        wallet_section = format_wallet_composition_section(
            result, token_created_ts=token_created_ts
        )
        if wallet_section:
            enrichment_lines.append(wallet_section)

        report = format_scan_report(result)
        if enrichment_lines:
            report += "\n\n" + "\n\n".join(enrichment_lines)
        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, target, result["chain"], "deep", result["risk_score"])
        user_tier = db.get_user_tier(user.id)
        if user_tier in ("hunter", "pro"):
            result_kb = _with_trade_buttons(
                deep_scan_result_kb(target, result["chain"]), target, result["chain"]
            )
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
            f"\u274c <b>Deep Scan Failed</b>\n\nError: {str(e)[:200]}\n\nPlease check the address and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def advanced_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Advanced deep forensic scan — operator-supplied scan provider.

    Standalone command for the 9-module Advanced pipeline (holder HHI, LP
    lock, dev reputation, contract authority, proxy, bundle, wash trading,
    honeypot). Slower than /deepscan but runs every forensic module and
    renders via format_advanced_deepscan_section.

    [REVIEW] The live docstring referenced the private payment-gate endpoint
    (checklist §6.13) — the OSS build talks to
    ``rugmunchbot.scanner.advanced_scan`` (operator-supplied
    ``ADVANCED_SCAN_URL`` deployment) instead.
    """
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if check_spam(user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "\U0001f52c <b>Advanced Deep Scan</b>\n\n"
            "Usage: /advancedscan <code>address</code> [chain]\n\n"
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
    msg = await update.message.reply_text(
        "\U0001f52c <b>Advanced scanning...</b>\n\n\U0001f4aa Running all 9 forensic modules \u2014 this takes a bit.",
        parse_mode=ParseMode.HTML,
    )
    try:
        result = await fetch_advanced_scan(target, chain)
        if not result:
            await msg.edit_text(
                "\u274c <b>Advanced scan failed</b>\n\nCould not reach the forensic pipeline. Please try again shortly.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        db.increment_usage(user.id, scan=True)
        report = format_advanced_deepscan_section(result)
        report += f"\n\n{footer_links()}"
        await msg.edit_text(
            report,
            parse_mode=ParseMode.HTML,
            reply_markup=scan_result_kb(target, result.get("chain") or chain or "unknown"),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("advanced_scan_error", exc_info=True)
        logger.error(f"Advanced scan error: {e}")
        await msg.edit_text(
            f"\u274c <b>Advanced Scan Failed</b>\n\nError: {str(e)[:200]}\n\nPlease check the address and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f9db <b>Wallet Forensics</b>\n\n"
            "Usage: /wallet <code>address</code>\n\n"
            "<b>Example:</b>\n"
            "  /wallet <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "<i>Hunter+ tiers get full P&L, funding source tracing, and connected wallet analysis.</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f9db <b>Analyzing wallet...</b>\n\u23f3 Querying on-chain data...",
        parse_mode=ParseMode.HTML,
    )
    try:
        result_v2 = await analyze_wallet_v2(addr, chain)
        if result_v2 is not None:
            result = v2_wallet_to_legacy(result_v2)
            if chain:
                result["chain"] = chain
        else:
            result = await analyze_wallet(addr, chain)
        report = format_wallet_report(result)
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            report, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except httpx.HTTPError as e:
        logger.debug("cmd_wallet_error", exc_info=True)
        logger.error(f"Wallet error: {e}")
        await msg.edit_text(
            f"\u274c <b>Wallet Analysis Failed</b>\n\n{str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── /ta — real technical analysis from RugCharts OHLCV ──────────────────────


async def _fetch_rugcharts_ohlcv(
    client: httpx.AsyncClient, chain: str, pair_address: str
) -> list[dict[str, Any]]:
    """Fetch OHLCV candles from the backend RugCharts engine (own data).

    Same route the frontend chart uses:
    ``GET /api/v1/rugcharts/ohlcv/{chain}/{pair}?timeframe=1h&limit=100``.
    Returns chronological candles (oldest → newest) or ``[]``.
    """
    if not pair_address or not chain:
        return []
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/rugcharts/ohlcv/{chain}/{pair_address}",
            params={"timeframe": "1h", "limit": 100},
            headers=backend_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            body = r.json()
            data = body.get("data") or body
            candles = data.get("candles") or []
            if isinstance(candles, list):
                return candles
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("rugcharts ohlcv fetch failed: %s", e)
    return []


async def _fetch_rugcharts_intel(
    client: httpx.AsyncClient, chain: str, pair_address: str
) -> dict[str, Any]:
    """Fetch RugCharts intel (rug score + volume authenticity) for risk overlay."""
    if not pair_address or not chain:
        return {}
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/rugcharts/intel/{chain}/{pair_address}",
            headers=backend_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            body = r.json()
            data = body.get("data") or body
            return data if isinstance(data, dict) else {}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("rugcharts intel fetch failed: %s", e)
    return {}


async def cmd_ta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4ca <b>Technical Analysis</b>\n\nUsage: /ta <code>address_or_symbol</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    target = ctx.args[0]
    explicit_chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f4ca <b>Running Technical Analysis...</b>", parse_mode=ParseMode.HTML
    )
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"{DEXSCREENER}/tokens/{target}")
            if r.status_code != 200:
                await msg.edit_text("\u274c Token not found.", parse_mode=ParseMode.HTML)
                return
            pairs = _ds_pairs(r.json())
            if not pairs:
                await msg.edit_text("\u274c No trading pairs found.", parse_mode=ParseMode.HTML)
                return
            p = pairs[0]
            name = p.get("baseToken", {}).get("name", "???")
            symbol = p.get("baseToken", {}).get("symbol", "???")
            price = float(p.get("priceUsd", 0))
            chain = explicit_chain or p.get("chainId", "") or detect_chain(target)
            pair_address = p.get("pairAddress", "")
            pc = p.get("priceChange", {})
            vol = p.get("volume", {})
            txns = p.get("txns", {})

            # ── Real indicators from our own OHLCV data ──
            candles = await _fetch_rugcharts_ohlcv(client, chain, pair_address)
            analysis = analyze_candles(candles) if candles else None

            # ── Risk overlay from RugCharts intel ──
            rug_score = None
            rug_level = None
            intel = await _fetch_rugcharts_intel(client, chain, pair_address)
            if intel:
                rs = intel.get("rug_score") or {}
                if isinstance(rs, dict):
                    rug_score = rs.get("score")
                    rug_level = rs.get("level")

            if analysis is not None:
                # Full card with computed indicators.
                text = format_ta_card(
                    analysis,
                    {
                        "name": name,
                        "symbol": symbol,
                        "address": target,
                        "chain": chain,
                        "price": price,
                        "change_24h": pc.get("h24") if pc.get("h24") is not None else None,
                        "rug_score": rug_score,
                        "rug_level": rug_level,
                    },
                )
            else:
                # Fallback: DexScreener momentum signals when OHLCV is unavailable.
                signals = []
                h1 = pc.get("h1") or 0
                h24 = pc.get("h24") or 0
                d7 = pc.get("d7") or 0
                if h1 > 5 and h24 > 10:
                    signals.append("\U0001f7e2 Strong short-term momentum")
                elif h1 < -5 and h24 < -10:
                    signals.append("\U0001f534 Strong downtrend")
                if h24 > 0 > h1:
                    signals.append("\U0001f7e1 Pullback in uptrend")
                if h24 < 0 < h1:
                    signals.append("\U0001f7e1 Bounce in downtrend")
                v24 = float(vol.get("h24") or 0)
                v6 = float(vol.get("h6") or 0)
                if v6 > 0 and v24 > 0:
                    vol_trend = v6 / (v24 / 4)
                    if vol_trend > 1.5:
                        signals.append("\U0001f4c8 Volume increasing (acceleration)")
                    elif vol_trend < 0.5:
                        signals.append("\U0001f4c9 Volume declining (loss of interest)")
                buys = txns.get("h24", {}).get("buys", 0)
                sells = txns.get("h24", {}).get("sells", 0)
                total_tx = buys + sells
                if total_tx > 0:
                    buy_ratio = buys / total_tx
                    if buy_ratio > 0.7:
                        signals.append(
                            f"\U0001f7e2 Buy pressure dominant ({buy_ratio * 100:.0f}% buys)"
                        )
                    elif buy_ratio < 0.3:
                        signals.append(
                            f"\U0001f534 Sell pressure dominant ({(1 - buy_ratio) * 100:.0f}% sells)"
                        )
                    else:
                        signals.append(f"\u26aa Balanced buy/sell ({buy_ratio * 100:.0f}% buys)")
                text = (
                    f"\U0001f4ca <b>Technical Analysis</b>\n{sep()}\n"
                    f"<b>{name}</b> ({symbol})\n<code>{target}</code>\n\n"
                    f"\U0001f4b0 Price: <b>{fmt_number(price)}</b>\n"
                    f"\U0001f504 1h: {fmt_pct(h1)} | 24h: {fmt_pct(h24)} | 7d: {fmt_pct(d7)}\n\n"
                    f"\U0001f4c8 <b>Volume</b>\n{thin_sep()}\n"
                    f"6h: {fmt_number(v6)} | 24h: {fmt_number(v24)}\n"
                    f"Txns 24h: {total_tx:,} (B:{buys:,} S:{sells:,})\n\n"
                    f"\U0001f3ed <b>Signals</b>\n{thin_sep()}\n"
                )
                if signals:
                    text += "\n".join(signals)
                else:
                    text += "\u26aa No strong signals detected"
                if rug_score is not None:
                    text += f"\n\n\U0001f6e1\ufe0f Rug Risk: {rug_score}/100"
                text += "\n\n<i>TA is not financial advice. Always /scan for security.</i>"
                text += footer_links()
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"TA error: {e}")
        await msg.edit_text(
            f"\u274c TA failed: {str(e)[:200]}", parse_mode=ParseMode.HTML, reply_markup=back_kb()
        )


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None:
        return
    if len(ctx.args or []) < 2:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u2696\ufe0f <b>Compare Tokens</b>\n\nUsage: /compare <code>addr1</code> <code>addr2</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\u2696\ufe0f <b>Comparing tokens...</b>", parse_mode=ParseMode.HTML
    )
    try:
        v2_1, v2_2 = await asyncio.gather(
            scan_token_v2((ctx.args or [])[0]), scan_token_v2((ctx.args or [])[1])
        )
        if v2_1 is not None and v2_2 is not None:
            r1, r2 = v2_token_to_legacy(v2_1), v2_token_to_legacy(v2_2)
        else:
            r1, r2 = await asyncio.gather(
                scan_token((ctx.args or [])[0]), scan_token((ctx.args or [])[1])
            )
        risk1 = r1["risk_score"]
        risk2 = r2["risk_score"]
        better = []
        if risk1 < risk2:
            better.append(f"\u2705 <b>{r1['name']}</b> \u2014 lower risk ({risk1}% vs {risk2}%)")
        elif risk2 < risk1:
            better.append(f"\u2705 <b>{r2['name']}</b> \u2014 lower risk ({risk2}% vs {risk1}%)")
        else:
            better.append(f"\u26aa Both at {risk1}% risk")
        liq1 = r1["liquidity"] or 0
        liq2 = r2["liquidity"] or 0
        if liq1 > liq2 and liq1 > 0:
            better.append(f"\U0001f4a7 <b>{r1['name']}</b> \u2014 higher liquidity")
        elif liq2 > liq1 and liq2 > 0:
            better.append(f"\U0001f4a7 <b>{r2['name']}</b> \u2014 higher liquidity")
        price_diff = _price_diff_pct(r1["price"], r2["price"])

        def mini(r) -> str:
            risk_ind = _risk_indicator(r["risk_score"])
            return (
                f"<b>{r['name']}</b> ({r['symbol']})\n"
                f"  <code>{r['address']}</code>\n"
                f"  \U0001f4b0 Price: <b>{fmt_number(r['price'])}</b>\n"
                f"  \U0001f4c8 MCap: {fmt_number(r['mcap'])} | \U0001f4a7 Liq: {fmt_number(r['liquidity'])}\n"
                f"  \U0001f4ca Vol: {fmt_number(r['volume_24h'])} | 24h: {fmt_pct(r['price_change_24h'])}\n"
                f"  {risk_ind} Risk: <b>{r['risk_score']}%</b> | Threats: {len(r['threats'])}"
            )

        text = (
            f"\u2696\ufe0f <b>Token Comparison</b>\n{sep()}\n\n"
            f"<b>1\ufe0f\u20e3 Token A</b>\n{mini(r1)}\n\n"
            f"<b>2\ufe0f\u20e3 Token B</b>\n{mini(r2)}\n\n"
            f"<b>\U0001f4ca Price Difference:</b> {price_diff}\n\n"
            f"<b>\U0001f3c6 Verdict</b>\n{thin_sep()}\n" + "\n".join(better) + footer_links()
        )
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
        )
    except (httpx.HTTPError, KeyError, TypeError) as e:
        logger.debug("mini_error", exc_info=True)
        logger.error(f"cmd_compare failed: {e}")
        await msg.edit_text(
            "\u274c <b>Comparison Failed</b>\n\nPlease check both addresses and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick price check - same as $TOKEN but as a command."""
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001fa99 Usage: /quick <code>SYMBOL</code>\nExample: /quick PEPE",
            parse_mode=ParseMode.HTML,
        )
        return
    symbol = ctx.args[0].upper().lstrip("$")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{DEXSCREENER}/search/?q={symbol}")
            if r.status_code == 200:
                pairs = _ds_pairs(r.json())
                if pairs:
                    p = pairs[0]
                    addr = p.get("baseToken", {}).get("address", "")
                    chain = p.get("chainId", "")
                    price = float(p.get("priceUsd", 0))
                    h24 = p.get("priceChange", {}).get("h24") or 0
                    vol = float(p.get("volume", {}).get("h24") or 0)
                    mcap = float(p.get("marketCap") or 0)
                    name = p.get("baseToken", {}).get("name", symbol)
                    text = (
                        f"\U0001fa99 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                        f"<code>{addr}</code>\n\n"
                        f"\U0001f4b0 {fmt_number(price)} | \U0001f504 24h: {fmt_pct(h24)}\n"
                        f"\U0001f4c8 MCap: {fmt_number(mcap)} | \U0001f4a7 Vol: {fmt_number(vol)}"
                        f"{footer_links()}"
                    )
                    kb = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}"
                                )
                            ],
                            [web_scan_button(addr, chain)],
                        ]
                    )
                    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                        text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=kb,
                        disable_web_page_preview=True,
                    )
                    return
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"cmd_quick DexScreener lookup failed for symbol {symbol}: {e}")
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\u274c Could not find token: <b>{symbol}</b>", parse_mode=ParseMode.HTML
    )


# ── /deployer — quick deployer fingerprint check ──


async def cmd_deployer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick deployer fingerprint: run a v2 scan and surface dev reputation."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f468\u200d\U0001f4bb <b>Deployer Fingerprint</b>\n\n"
            "Usage: /deployer <code>address</code>\n\n"
            "Runs a v2 security scan and extracts the deployer reputation: "
            "dev risk score, deployment history, rug count.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f468\u200d\U0001f4bb <b>Checking deployer fingerprint...</b>\n<code>{target}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await scan_token_v2(target, chain)
        if data is None:
            await msg.edit_text(
                "\u274c <b>Deployer Check Failed</b>\n\nv2 scanner unavailable. Please try again later.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        evidence = data.get("evidence") or {}
        dev = evidence.get("deployer") or {}
        if not dev:
            await msg.edit_text(
                f"\u26aa <b>No Deployer Data</b>\n\n"
                f"<code>{target}</code>\n"
                f"No deployer reputation data returned by the scanner.\n\n"
                f"Safety: {data.get('safety_score', 'N/A')}/100 | "
                f"Risk level: {data.get('risk_level', 'unknown')}",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        risk_score = dev.get("risk_score")
        risk_label = dev.get("risk_label") or dev.get("reputation_label") or "UNKNOWN"
        dev_addr = dev.get("deployer_address") or dev.get("address") or "unknown"
        chain_norm = chain or detect_chain(target)

        # Enrich with the C7 deployer behavioral fingerprint (risk bucket,
        # success rate, launchpad) when the backend can compute it.
        fingerprint = await fetch_deployer_fingerprint(dev_addr, chain_norm)
        fp = fingerprint or {}

        risk_bucket = fp.get("risk_bucket") or risk_label
        total_deployments = fp.get("total_deployments")
        if total_deployments is None:
            total_deployments = dev.get("total_deployments") or dev.get("deployment_count")
        success_rate = fp.get("success_rate")
        launchpad = fp.get("launchpad")
        lines = [
            "\U0001f468\u200d\U0001f4bb <b>Deployer Fingerprint</b>",
            sep(),
            f"<code>{dev_addr}</code>\n",
            "\U0001f3af <b>Risk Profile</b>",
            thin_sep(),
        ]
        dev_risk = fp.get("risk_score")
        if dev_risk is None:
            dev_risk = risk_score
        if dev_risk is not None:
            emoji = (
                "\U0001f7e2" if dev_risk < 30 else "\U0001f7e1" if dev_risk < 60 else "\U0001f534"
            )
            lines.append(f"{emoji} Dev Risk Score: <b>{dev_risk}/100</b>")
        if risk_bucket:
            lines.append(f"{_bucket_emoji(str(risk_bucket))} Risk Bucket: <b>{risk_bucket}</b>")
        if total_deployments is not None:
            lines.append(f"\U0001f4e6 Total Deployments: <b>{total_deployments}</b>")
        if success_rate is not None:
            lines.append(f"\U0001f4c8 Success Rate: <b>{success_rate * 100:.0f}%</b>")
        if launchpad:
            lines.append(f"\U0001f680 Known Launchpad: <b>{launchpad}</b>")
        if dev.get("rug_count") is not None:
            lines.append(f"\U0001f4a5 Rug Count: <b>{dev['rug_count']}</b>")
        if fp.get("median_lifespan_hours") is not None:
            lines.append(f"\u231b Median Lifespan: <b>{fp['median_lifespan_hours']:.0f}h</b>")
        if evidence.get("holders"):
            h = evidence["holders"]
            lines.append(
                f"\U0001f465 Top-10 Holders: <b>{h.get('top_10_pct', 'N/A')}%</b> "
                f"| Holders: {h.get('holder_count', 'N/A')}"
            )
        if evidence.get("liquidity"):
            liq = evidence["liquidity"]
            locked = liq.get("locked_pct")
            if locked is not None:
                lines.append(f"\U0001f510 LP Locked: <b>{locked:.0f}%</b>")
        if evidence.get("security", {}).get("checks"):
            checks = evidence["security"]["checks"]
            fails = sum(1 for c in checks.values() if c.get("status") == "fail")
            lines.append(f"\U0001f512 Failed Security Checks: <b>{fails}</b>")
        lines.append("")
        lines.append(f"\U0001f50d Full scan: /scan <code>{target}</code>{footer_links()}")
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        logger.debug("cmd_deployer_error", exc_info=True)
        logger.error(f"cmd_deployer failed: {e}")
        await msg.edit_text(
            f"\u274c <b>Deployer Check Failed</b>\n\n{str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── /hype — quick hype score ──


async def cmd_hype(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick hype score: boosts, volume, age, and price momentum."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f525 <b>Hype Score</b>\n\n"
            "Usage: /hype <code>address_or_symbol</code>\n\n"
            "Returns a 0-100 hype score based on market activity, "
            "volume momentum, token age, and price action.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"),
                parse_mode=ParseMode.HTML,
                reply_markup=upgrade_nudge_kb(tier),
            )
            return
    target = ctx.args[0]
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f525 <b>Measuring hype...</b>", parse_mode=ParseMode.HTML
    )
    try:
        # Resolve address: if it looks like a symbol, search DexScreener
        address = target
        chain = ""
        async with httpx.AsyncClient(timeout=15) as client:
            if not (is_evm(target) or is_sol(target)):
                r = await client.get(f"{DEXSCREENER}/search/?q={target}")
                if r.status_code == 200:
                    pairs = _ds_pairs(r.json())
                    if pairs:
                        address = pairs[0].get("baseToken", {}).get("address", "")
                        chain = pairs[0].get("chainId", "")
            if not address:
                await msg.edit_text(
                    f"\u274c Could not resolve token: <b>{target}</b>", parse_mode=ParseMode.HTML
                )
                return

            # Boosts (paid hype signal)
            boosts = 0
            try:
                br = await client.get(f"{DEXSCREENER}/token-boosts/top/v1")
                if br.status_code == 200:
                    boost_list = br.json()
                    if isinstance(boost_list, list):
                        for b in boost_list:
                            if b.get("tokenAddress", "").lower() == address.lower():
                                boosts = int(b.get("boostCount", 0) or 0)
                                break
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
            ):
                pass

            # Pair data
            tr = await client.get(f"{DEXSCREENER}/tokens/{address}")
            if tr.status_code != 200:
                await msg.edit_text("\u274c Token not found.", parse_mode=ParseMode.HTML)
                return
            pairs = _ds_pairs(tr.json())
            if not pairs:
                await msg.edit_text("\u274c No trading pairs found.", parse_mode=ParseMode.HTML)
                return
            p = pairs[0]
            name = p.get("baseToken", {}).get("name", "???")
            symbol = p.get("baseToken", {}).get("symbol", "???")
            chain = chain or p.get("chainId", "")
            price = float(p.get("priceUsd", 0))
            h1 = float(p.get("priceChange", {}).get("h1") or 0)
            h24 = float(p.get("priceChange", {}).get("h24") or 0)
            vol24 = float(p.get("volume", {}).get("h24") or 0)
            liq = float(p.get("liquidity", {}).get("usd") or 0)
            created = p.get("pairCreatedAt")
            from datetime import UTC, datetime as dt

            age_hours = 0.0
            if created:
                with contextlib.suppress(OSError, ValueError, TypeError):
                    age_hours = max(
                        0,
                        (dt.now(UTC) - dt.fromtimestamp(created / 1000, tz=UTC)).total_seconds()
                        / 3600,
                    )

        # ── C5 hype score from backend (preferred) ──
        # Composite 0-100 with per-signal breakdown: social velocity, volume
        # growth, holder growth, price momentum. Falls back to the local
        # DexScreener computation below when unavailable.
        c5 = await fetch_hype_score(address, chain)
        if c5 is not None and c5.get("hype_score") is not None:
            hype = float(c5["hype_score"])
            signals = c5.get("signals") or {}
            social = signals.get("social_velocity") or {}
            volume = signals.get("volume_growth") or {}
            holder = signals.get("holder_growth") or {}
            price_sig = signals.get("price_momentum") or {}
            emoji = "\U0001f525" if hype >= 70 else "\U0001f7e1" if hype >= 40 else "\U0001f7e2"
            lines = [
                f"{emoji} <b>Hype Score: {hype:.0f}/100</b>",
                sep(),
                f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}",
                f"<code>{address}</code>\n",
                "\U0001f4ca <b>Components</b>",
                thin_sep(),
                f"\U0001f4e2 Social Velocity: <b>{float(social.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f4c8 Volume Growth: <b>{float(volume.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f465 Holder Growth: <b>{float(holder.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f680 Price Momentum: <b>{float(price_sig.get('score', 0) or 0):.0f}/100</b>",
            ]
            if c5.get("inactivity_hours") is not None:
                lines.append(
                    f"\u23f3 Inactivity: {c5['inactivity_hours']:.0f}h (decay x{c5.get('decay_factor', 1.0)})"
                )
            details = []
            for sig in (social, volume, holder, price_sig):
                if isinstance(sig, dict) and sig.get("detail"):
                    details.append(str(sig["detail"])[:60])
            if details:
                lines.append("")
                lines.append("\U0001f4cb <i>" + " | ".join(details[:4]) + "</i>")
            lines.append("")
            lines.append(f"\U0001f4b0 Price: {fmt_number(price)}")
            lines.append("")
            lines.append(f"\U0001f50d Full scan: /scan <code>{address}</code>{footer_links()}")
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}"
                        )
                    ],
                    [web_scan_button(address, chain)],
                ]
            )
            db.increment_usage(user.id, scan=True)
            await msg.edit_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return

        # ── Hype scoring (local fallback) ──
        boost_score = min(boosts * 8, 40)  # 0-40 from boost count
        vol_score = 0
        if vol24 > 0:
            if vol24 >= 1_000_000:
                vol_score = 20
            elif vol24 >= 100_000:
                vol_score = 14
            elif vol24 >= 10_000:
                vol_score = 8
            elif vol24 > 0:
                vol_score = 4
        momentum = 0
        if h1 > 20 and h24 > 30:
            momentum = 20
        elif h1 > 10 and h24 > 15:
            momentum = 14
        elif h24 > 5:
            momentum = 8
        elif h24 < -20:
            momentum = 0
        else:
            momentum = 4
        age_score = 0
        if age_hours and age_hours < 24:
            age_score = 10
        elif age_hours and age_hours < 72:
            age_score = 6
        elif age_hours and age_hours < 168:
            age_score = 3
        liquidity_score = 10 if liq >= 100_000 else 5 if liq >= 10_000 else 0
        hype = min(boost_score + vol_score + momentum + age_score + liquidity_score, 100)

        emoji = "\U0001f525" if hype >= 70 else "\U0001f7e1" if hype >= 40 else "\U0001f7e2"
        lines = [
            f"{emoji} <b>Hype Score: {hype}/100</b>",
            sep(),
            f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}",
            f"<code>{address}</code>\n",
            "\U0001f4ca <b>Components</b>",
            thin_sep(),
            f"\U0001f525 Boosts: {boosts} (x8, max 40) -> {boost_score}",
            f"\U0001f4c8 Volume 24h: {fmt_number(vol24)} -> {vol_score}/20",
            f"\U0001f504 Momentum (1h {fmt_pct(h1)} / 24h {fmt_pct(h24)}): -> {momentum}/20",
            f"\u231b Age: {age_hours:.0f}h -> {age_score}/10",
            f"\U0001f4a7 Liquidity: {fmt_number(liq)} -> {liquidity_score}/10",
            "",
            f"\U0001f4b0 Price: {fmt_number(price)}",
        ]
        lines.append("")
        lines.append(f"\U0001f50d Full scan: /scan <code>{address}</code>{footer_links()}")
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}"
                    )
                ],
                [web_scan_button(address, chain)],
            ]
        )
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        logger.debug("cmd_hype_error", exc_info=True)
        logger.error(f"cmd_hype failed: {e}")
        await msg.edit_text(
            f"\u274c <b>Hype Check Failed</b>\n\n{str(e)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

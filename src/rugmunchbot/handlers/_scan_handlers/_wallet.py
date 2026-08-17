#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (cmd_wallet + cmd_compare).

Split out of ``handlers/_scan_handlers.py`` (degodded 2026-08-16). Re-exported
by ``handlers._scan_handlers`` and ``handlers.scanning`` so existing
importers/tests keep working unchanged.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    _price_diff_pct,
    _risk_indicator,
    detect_chain,
    footer_links,
    format_wallet_report,
    is_owner,
    paywall_text,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    upgrade_nudge_kb,
)
from app.domains.telegram.rugmunchbot.scanner import (
    analyze_wallet,
    analyze_wallet_v2,
    scan_token,
    scan_token_v2,
    v2_token_to_legacy,
    v2_wallet_to_legacy,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")

#: Hard budget for the wallet-trust enrichment. Deterministic own-data
#: (ClickHouse), but 3-4 sequential bounded queries can stack to tens of
#: seconds during a ClickHouse outage — a slow trust probe must never stall
#: the wallet report. Timeout → fail-open (no trust line), report ships.
_WALLET_TRUST_TIMEOUT: float = 8.0


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run wallet forensics on an address (usage, then wallet analysis)."""
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
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f9db <b>Analyzing wallet...</b>\n\u23f3 Querying on-chain data...", parse_mode=ParseMode.HTML
    )
    try:
        result_v2 = await analyze_wallet_v2(addr, chain)
        if result_v2 is not None:
            result = v2_wallet_to_legacy(result_v2)
            if chain:
                result["chain"] = chain
        else:
            result = await analyze_wallet(addr, chain)

        # ── Intelligent wallet trust: 0-100 score + one-line verdict ──
        # Computed from own ClickHouse signals (age / activity / funding /
        # prior rugs / labels) with the scan result as supplementary context.
        # Fail-open: renders "—" when no signal resolves — never blocks the
        # wallet report.
        try:
            from app.domains.scanner.engines.own_wallet_trust import get_wallet_trust
            from app.rag.scan_memory import schedule_wallet_memory_index

            trust_chain = chain or str(result.get("chain") or "") or detect_chain(addr)
            trust = await asyncio.wait_for(
                get_wallet_trust(addr, trust_chain, context=result),
                timeout=_WALLET_TRUST_TIMEOUT,
            )
            result["trust"] = trust
            # RAG feed: accumulate the wallet's label/trust so the labeling
            # system learns "we see them a lot". Fire-and-forget; never delays
            # the report.
            schedule_wallet_memory_index(result)
        except Exception as e:  # trust is additive, never fatal
            logger.debug("wallet_trust_failed", exc_info=True)
            logger.warning(f"Wallet trust computation failed for {addr}: {e}")

        report = format_wallet_report(result)
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(report, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True)
    except httpx.HTTPError as e:
        logger.debug("cmd_wallet_error", exc_info=True)
        logger.error(f"Wallet error: {e}")
        await msg.edit_text(
            failure_card("Wallet Analysis Failed", "Wallet forensics hit an unexpected error.", ACTION_TRY_AGAIN),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── /ta — real technical analysis from RugCharts OHLCV ──────────────────────
# (cmd_ta now lives in handlers/_ta_handlers.py; this section header is kept
# for context. The RugCharts fetchers live in handlers/_charts.py.)


async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Compare two token contracts and render a verdict card."""
    user = update.effective_user
    if user is None:
        return
    if len(ctx.args or []) < 2:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u2696\ufe0f <b>Compare Tokens</b>\n\n"
            "Usage: /compare <code>addr1</code> <code>addr2</code>\n\n"
            "Example: /compare <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code> "
            "<code>0x2170Ed0880ac9A755fd29B2688956BD959F933F8</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    msg = await update.message.reply_text("\u2696\ufe0f <b>Comparing tokens...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    try:
        v2_1, v2_2 = await asyncio.gather(scan_token_v2((ctx.args or [])[0]), scan_token_v2((ctx.args or [])[1]))
        if v2_1 is not None and v2_2 is not None:
            r1, r2 = v2_token_to_legacy(v2_1), v2_token_to_legacy(v2_2)
        else:
            r1, r2 = await asyncio.gather(scan_token((ctx.args or [])[0]), scan_token((ctx.args or [])[1]))
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

        def mini(r: dict[str, Any]) -> str:
            """Render one compact per-token comparison block."""
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
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True)
    except (httpx.HTTPError, KeyError, TypeError) as e:
        logger.debug("mini_error", exc_info=True)
        logger.error(f"cmd_compare failed: {e}")
        await msg.edit_text(
            "\u274c <b>Comparison Failed</b>\n\nPlease check both addresses and try again.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

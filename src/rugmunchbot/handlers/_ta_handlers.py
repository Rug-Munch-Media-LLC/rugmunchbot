#!/usr/bin/env python3
"""
RugMunchBot -- /ta technical-analysis handler (degodded 2026-08-14).

Extracted from ``handlers/scanning.py``: ``cmd_ta`` runs real technical
analysis from RugCharts OHLCV (``analyze_candles``) with a RugCharts risk
overlay, falling back to DexScreener momentum signals when OHLCV is
unavailable. Re-exported by ``handlers.scanning`` so existing importers
keep working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import DEXSCREENER, fmt_number, fmt_pct
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    is_owner,
    paywall_text,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.handlers._charts import (
    _fetch_rugcharts_intel,
    _fetch_rugcharts_ohlcv,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    token_not_found_card,
    usage_hint,
)
from app.domains.telegram.rugmunchbot.handlers._scanning_helpers import _ds_pairs, format_ta_card
from app.domains.telegram.rugmunchbot.keyboards import back_kb, upgrade_nudge_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.ta_indicators import analyze_candles

logger = get_logger("rmi_bot")


async def cmd_ta(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render a technical-analysis card for an address/symbol (RugCharts OHLCV)."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4ca <b>Technical Analysis</b>\n\n"
            + usage_hint("ta", "address_or_symbol", "0x6982508145454Ce325dDbE47a25d4ec3d2311933"),
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
    target = ctx.args[0]
    explicit_chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text("\U0001f4ca <b>Running Technical Analysis...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    try:
        client = get_shared_client(20)
        r = await client.get(f"{DEXSCREENER}/tokens/{target}")
        if r.status_code != 200:
            await msg.edit_text(token_not_found_card(target, cmd="ta"), parse_mode=ParseMode.HTML)
            return
        pairs = _ds_pairs(r.json())
        if not pairs:
            await msg.edit_text(
                failure_card(
                    "No Trading Pairs Found",
                    f"No DEX pairs found for <code>{target}</code>.",
                    "Ensure the token has an active pair, then try again.",
                ),
                parse_mode=ParseMode.HTML,
            )
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
                    signals.append(f"\U0001f7e2 Buy pressure dominant ({buy_ratio * 100:.0f}% buys)")
                elif buy_ratio < 0.3:
                    signals.append(f"\U0001f534 Sell pressure dominant ({(1 - buy_ratio) * 100:.0f}% sells)")
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
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"TA error: {e}")
        await msg.edit_text(
            failure_card(
                "TA Failed",
                "The analysis hit an unexpected error.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )

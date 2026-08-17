#!/usr/bin/env python3
"""
RugMunchBot -- /sentiment command: Market sentiment from own data + external.

Shows market sentiment trend, fear/greed index, social volume,
and crypto market health indicators aggregated from our data pipeline.
Scout+ tier feature.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    COINGECKO,
    backend_headers,
    fmt_number,
)
from app.domains.telegram.rugmunchbot.data_resolver import backend_get_json, resolve_with_fallback
from app.domains.telegram.rugmunchbot.formatters import (
    footer_links,
    is_owner,
    paywall_text,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.keyboards import paywall_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.sentiment")


async def _fetch_market_overview() -> dict[str, Any]:
    """Fetch market overview — own data first, external APIs as fallback.

    Hierarchy enforced via data_resolver:
      1. Backend market intel (/api/v1/market/overview — our cached pipeline)
      2. Backend sentiment (/api/v1/market/sentiment)
      3. External fallback: CoinGecko global + alternative.me F&G + Etherscan gas

    DATA_SOURCE is logged by the resolver so reliance on external providers
    (CoinGecko/alternative.me/Etherscan) can be tracked.
    """
    result: dict[str, Any] = {
        "btc_dominance": None,
        "total_mcap": None,
        "total_volume_24h": None,
        "fear_greed": None,
        "sentiment": "neutral",
        "trending_coins": [],
        "market_health": "unknown",
        "eth_gas": None,
        "btc_dominance_change": None,
    }

    async def _external_overview() -> dict[str, Any]:
        """Fallback: direct CoinGecko + alternative.me + Etherscan calls.

        The four external calls run concurrently (previously sequential —
        up to ~40s worst case); each is best-effort and contributes
        whichever fields it returns (first-success merge).
        """
        out = dict(result)
        try:
            client = get_shared_client(10)

            async def _fetch_coingecko() -> dict[str, Any]:
                # CoinGecko global data
                r = await client.get(f"{COINGECKO}/global")
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    return {
                        "btc_dominance": data.get("market_cap_percentage", {}).get("btc"),
                        "total_mcap": data.get("total_market_cap", {}).get("usd"),
                        "total_volume_24h": data.get("total_volume", {}).get("usd"),
                    }
                return {}

            async def _fetch_fear_greed() -> dict[str, Any]:
                # Fear & Greed Index
                fg = await client.get("https://api.alternative.me/fng/?limit=1")
                if fg.status_code == 200:
                    fg_data = fg.json().get("data", [{}])[0]
                    return {
                        "fear_greed": {
                            "value": int(fg_data.get("value", 50)),
                            "classification": fg_data.get("value_classification", "Neutral"),
                        }
                    }
                return {}

            async def _fetch_gas() -> dict[str, Any]:
                # ETH Gas (proxy for network activity)
                gas = await client.get("https://api.etherscan.io/v2/api?chainid=1&module=gastracker&action=gasoracle")
                if gas.status_code == 200:
                    gas_data = gas.json().get("result", {})
                    return {"eth_gas": gas_data.get("ProposeGasPrice")}
                return {}

            async def _fetch_own_sentiment() -> dict[str, Any]:
                # Try own backend sentiment data (best-effort enrichment)
                own = await client.get(
                    f"{BACKEND_URL}/api/v1/market/sentiment",
                    headers=backend_headers(),
                    timeout=5,
                )
                if own.status_code == 200:
                    own_data = own.json()
                    if isinstance(own_data, dict):
                        return {
                            "sentiment": own_data.get("sentiment", out["sentiment"]),
                            "btc_dominance_change": own_data.get("btc_dominance_change"),
                        }
                return {}

            # Parallel first-success merge: failures contribute nothing.
            for partial in await asyncio.gather(
                _fetch_coingecko(),
                _fetch_fear_greed(),
                _fetch_gas(),
                _fetch_own_sentiment(),
                return_exceptions=True,
            ):
                if isinstance(partial, dict):
                    out.update(partial)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.warning("Market overview external fetch failed: %s", e)
        return out

    # ── Own data first: backend market intel (cached, TTL 120s) ──────────
    async def _own_overview() -> dict[str, Any]:
        body = await backend_get_json("/api/v1/market/overview", timeout_secs=5)
        if not body or not body.get("total_market_cap"):
            return {}
        out = dict(result)
        out["total_mcap"] = body.get("total_market_cap")
        out["total_volume_24h"] = body.get("total_volume_24h")
        out["btc_dominance"] = body.get("btc_dominance")
        fg = body.get("fear_greed")
        if isinstance(fg, dict) and fg.get("value"):
            out["fear_greed"] = {
                "value": int(fg.get("value", 50)),
                "classification": fg.get("classification", "Neutral"),
            }
        # Backend sentiment enrichment (own pipeline)
        sent = await backend_get_json("/api/v1/market/sentiment", timeout_secs=4)
        if sent:
            out["sentiment"] = sent.get("sentiment", out["sentiment"])
            out["btc_dominance_change"] = sent.get("btc_dominance_change")
        return out

    overview = await resolve_with_fallback(
        "market_overview",
        _own_overview,
        _external_overview,
        timeout_secs=8,
        is_valid=lambda d: bool(d and d.get("total_mcap")),
    )
    result.update(overview or {})

    # Derive market health
    fg_val = result.get("fear_greed", {}).get("value", 50) if result.get("fear_greed") else 50
    if fg_val >= 75:
        result["market_health"] = "extreme_greed"
    elif fg_val >= 60:
        result["market_health"] = "greed"
    elif fg_val >= 40:
        result["market_health"] = "neutral"
    elif fg_val >= 25:
        result["market_health"] = "fear"
    else:
        result["market_health"] = "extreme_fear"

    return result


def _health_emoji(health: str) -> str:
    emojis = {
        "extreme_greed": "\U0001f525",
        "greed": "\U0001f4c8",
        "neutral": "\u26aa",
        "fear": "\U0001f4c9",
        "extreme_fear": "\U0001f4a9",
    }
    return emojis.get(health, "\u26aa")


def _sentiment_arrow(sentiment: str) -> str:
    arrows = {
        "bullish": "\U0001f7e2",
        "bearish": "\U0001f534",
        "neutral": "\u26aa",
    }
    return arrows.get(sentiment, "\u26aa")


async def cmd_sentiment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show market sentiment from own data + external indicators.

    Usage: /sentiment
    Scout+ tier feature.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    tier = db.get_user_tier(user.id)
    if tier == "free" and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4c8 <b>Market Sentiment</b>\n\n"
            "This feature requires <b>Scout tier</b> or higher.\n\n"
            "\U0001f6f0\ufe0f <b>Monitor (250\u2605/30d)</b> — live LP/whale + "
            "tax/ownership/mint alerts · 🔬 <b>Deep Scan (250\u2605)</b> — full forensics\n"
            "💵 Core /scan + chat stay free.",
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

    msg = await update.message.reply_text("\U0001f4c8 <b>Analyzing market sentiment...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields

    try:
        overview = await _fetch_market_overview()
        s = sep()
        d = thin_sep()
        health_emoji = _health_emoji(overview["market_health"])
        sent_arrow = _sentiment_arrow(overview["sentiment"])

        lines = [
            "\U0001f4c8 <b>Market Sentiment</b>",
            s,
            f"{health_emoji} <b>Market Health:</b> {overview['market_health'].replace('_', ' ').title()}",
            f"{sent_arrow} <b>Sentiment:</b> {overview['sentiment'].title()}",
            "",
            "<b>\U0001f4ca Key Indicators</b>",
            d,
        ]

        fg = overview.get("fear_greed")
        if fg:
            fg_emoji = (
                "\U0001f525"
                if fg["value"] >= 75
                else "\U0001f4c8"
                if fg["value"] >= 60
                else "\u26aa"
                if fg["value"] >= 40
                else "\U0001f4c9"
                if fg["value"] >= 25
                else "\U0001f4a9"
            )
            lines.append(f"{fg_emoji} Fear & Greed Index: <b>{fg['value']}</b> — {fg['classification']}")

        btc_dom = overview.get("btc_dominance")
        if btc_dom is not None:
            change = overview.get("btc_dominance_change")
            change_str = f" ({change:+.1f}%)" if change else ""
            lines.append(f"\U0000243b BTC Dominance: <b>{btc_dom:.1f}%</b>{change_str}")

        total_mcap = overview.get("total_mcap")
        if total_mcap:
            lines.append(f"\U0001f4b0 Total Market Cap: <b>{fmt_number(total_mcap)}</b>")

        total_vol = overview.get("total_volume_24h")
        if total_vol:
            lines.append(f"\U0001f4e6 24h Volume: <b>{fmt_number(total_vol)}</b>")

        gas = overview.get("eth_gas")
        if gas:
            try:
                gas_val = int(gas)
                gas_emoji = "\U0001f534" if gas_val > 100 else "\U0001f7e1" if gas_val > 50 else "\U0001f7e2"
                lines.append(f"{gas_emoji} ETH Gas: <b>{gas_val} Gwei</b>")
            except (ValueError, TypeError) as e:
                logger.debug("sentiment score parse failed: %s", e)

        lines.extend(
            [
                "",
                "<b>\U0001f4a1 Interpretation</b>",
                d,
            ]
        )

        fg_val = fg["value"] if fg else 50
        if fg_val >= 75:
            lines.append("\U0001f525 <b>Extreme Greed</b> — Market may be overbought. Consider taking profits.")
        elif fg_val >= 60:
            lines.append("\U0001f4c8 <b>Greed</b> — Positive sentiment but stay cautious.")
        elif fg_val >= 40:
            lines.append("\u26aa <b>Neutral</b> — Balanced market. Good for DCA.")
        elif fg_val >= 25:
            lines.append("\U0001f4c9 <b>Fear</b> — Oversold conditions. Look for buying opportunities.")
        else:
            lines.append("\U0001f4a9 <b>Extreme Fear</b> — Maximum pessimism. Historical buying opportunity.")

        lines.append(footer_links())

        db.increment_usage(user.id, scan=True)

        sentiment_kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("\U0001f504 Refresh", callback_data="sentiment_refresh")],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=sentiment_kb,
            disable_web_page_preview=True,
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_sentiment_error", exc_info=True)
        logger.error(f"Sentiment error: {e}")
        await msg.edit_text(
            "\u274c <b>Sentiment Unavailable</b>\n\n"
            "Could not fetch market sentiment right now.\n"
            "Please try again in a moment.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]
            ),
        )

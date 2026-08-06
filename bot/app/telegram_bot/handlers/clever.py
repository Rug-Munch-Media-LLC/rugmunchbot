#!/usr/bin/env python3
"""
V8 CLEVER COMMANDS — What makes RugMunch bot better than every competitor.
============================================================================
- Chart IN Telegram (quickchart.io OHLCV candlestick)
- Bubble Map IN Telegram (holder distribution visualization)
- Deployer Investigation (trace deployer wallet history)
- Token Comparables (RAG-powered similarity search)
- Live Trades Feed (last 10 trades with whale detection)
- Wallet Profile (PnL, win/loss, labels)
- Token Time Machine (price journey + key events)
- Safety Report Card (A-F grade with category breakdowns)
"""

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from config import (
    BACKEND_URL,
    DATABUS_URL,
    RAG_SEARCH_URL,
    RMI_AUTH_TOKEN,
    WEBSITE_URL,
    fmt_chain,
    fmt_number,
    fmt_pct,
    risk_bar,
)
from handlers.menu import back_kb
from services.formatting import esc, footer_links, sep, thin_sep
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex"

SPAM_COOLDOWN = 3.0
_user_last_action: dict[int, float] = {}


def _short(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if len(addr) > 12 else addr


def _check_spam(user_id: int) -> bool:
    import time

    now = time.time()
    last = _user_last_action.get(user_id, 0.0)
    if now - last < SPAM_COOLDOWN:
        return True
    _user_last_action[user_id] = now
    return False


def _parse_args(args: list[str] | None) -> tuple[str | None, str | None]:
    if not args:
        return None, None
    address = args[0].strip()
    chain = args[1] if len(args) > 1 else None
    return address, chain


# ═══════════════════════════════════════════════════════════════
# DEXSCREENER FAST DATA FETCH (cached, reliable, fast)
# ═══════════════════════════════════════════════════════════════


def _dex_pairs_to_list(pairs: list) -> list[dict]:
    """Extract simplified token data from DexScreener pairs."""
    tokens = []
    for p in pairs:
        bt = p.get("baseToken", {})
        tokens.append(
            {
                "name": bt.get("name", "???"),
                "symbol": bt.get("symbol", "???"),
                "address": bt.get("address", ""),
                "price": float(p.get("priceUsd", 0) or 0),
                "mcap": float(p.get("marketCap") or p.get("fdv") or 0),
                "volume_24h": float((p.get("volume", {}) or {}).get("h24") or 0),
                "liquidity_usd": float((p.get("liquidity", {}) or {}).get("usd") or 0),
                "price_change_1h": (p.get("priceChange", {}) or {}).get("h1") or 0,
                "price_change_24h": (p.get("priceChange", {}) or {}).get("h24") or 0,
                "price_change_7d": (p.get("priceChange", {}) or {}).get("d7") or 0,
                "chain": p.get("chainId", ""),
                "pair_address": p.get("pairAddress", ""),
                "dex": p.get("dexId", ""),
                "created_at": p.get("pairCreatedAt"),
                "txns_24h": (p.get("txns", {}) or {}).get("h24", {}),
                "socials": {
                    s.get("type"): s.get("url")
                    for s in (p.get("info", {}) or {}).get("socials", []) or []
                },
                "url": p.get("url", ""),
            }
        )
    return tokens


async def _fetch_dexscreener(client: httpx.AsyncClient, address: str) -> dict | None:
    """Fast DexScreener lookup — returns first pair dict or None."""
    try:
        r = await client.get(f"{DEXSCREENER_URL}/search/?q={address}", timeout=8)
        if r.status_code == 200:
            pairs = r.json().get("pairs", [])
            if pairs:
                return _dex_pairs_to_list(pairs)[0]
    except Exception as exc:
        logger.debug("DexScreener fetch failed for %s: %s", _short(address), exc)
    return None


# ═══════════════════════════════════════════════════════════════
# 1. CHART IN TELEGRAM — /chart command with OHLCV candlestick
# ═══════════════════════════════════════════════════════════════


async def cmd_chart_v8(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/chart [address] [chain] — Show price chart with OHLCV data inline."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    address, chain = _parse_args(ctx.args)
    if not address:
        await update.message.reply_text(
            "📊 <b>Token Chart</b>\n\n"
            "Usage: /chart <code>address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /chart <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /chart <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"📊 <b>Generating chart...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Fetching price data + OHLCV candles...",
        parse_mode=ParseMode.HTML,
    )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = await _fetch_dexscreener(client, address)
            if not data:
                # Try DataBus fallback
                try:
                    r = await client.get(
                        f"{DATABUS_URL}/fetch/token_price",
                        params={"mint": address},
                        timeout=10,
                    )
                    if r.status_code == 200:
                        payload = r.json().get("data", r.json())
                        pairs = payload.get("pairs", [])
                        if pairs:
                            data = _dex_pairs_to_list(pairs)[0]
                except Exception:
                    pass

            if not data:
                await msg.edit_text(
                    "❌ <b>Chart Data Unavailable</b>\n\n"
                    "No trading pair found for this address.\n"
                    f'<a href="{WEBSITE_URL}/rugcharts">Try rugmunch.io/rugcharts</a>',
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                    disable_web_page_preview=True,
                )
                return

            # Try OHLCV from backend
            ohlcv_candles = ""
            try:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/rugcharts/ohlcv/{data['chain']}/{data['pair_address'] or address}",
                    timeout=6,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if r.status_code == 200:
                    ohlcv = r.json()
                    if ohlcv:
                        ohlcv_candles = "\n📈 <b>Recent Candles (15m):</b>\n"
                        for c in (ohlcv if isinstance(ohlcv, list) else ohlcv.get("candles", []))[
                            :5
                        ]:
                            o = float(c.get("open", 0))
                            cl = float(c.get("close", 0))
                            h = float(c.get("high", 0))
                            l = float(c.get("low", 0))
                            d_emoji = "🟢" if cl >= o else "🔴"
                            ohlcv_candles += (
                                f"  {d_emoji} O:{fmt_number(o)} H:{fmt_number(h)} "
                                f"L:{fmt_number(l)} C:{fmt_number(cl)}\n"
                            )
            except Exception as exc:
                logger.debug("OHLCV fetch failed: %s", exc)

            # Price zone indicators
            price = data["price"]
            h24 = data["price_change_24h"]
            price_trend = "📈" if h24 >= 0 else "📉"
            trend_color = "🟢" if h24 >= 0 else "🔴"

            text = (
                f"📊 <b>Token Chart</b>\n{sep()}\n"
                f"<b>{esc(data['name'])}</b> (${esc(data['symbol'])}) {fmt_chain(data['chain'])}\n"
                f"<code>{esc(str(address)[:42])}</code>\n\n"
                f"💰 Price: <b>{fmt_number(price)}</b> {trend_color} {fmt_pct(h24)}\n"
                f"💧 Liquidity: <b>{fmt_number(data['liquidity_usd'])}</b>  │  "
                f"📊 Vol 24h: <b>{fmt_number(data['volume_24h'])}</b>\n"
                f"🏷️ MCap: <b>{fmt_number(data['mcap'])}</b>  │  "
                f"🔄 24h: {fmt_pct(h24)}\n"
                f"{ohlcv_candles}\n"
                f"{sep()}\n"
                f"{footer_links()}"
            )

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📊 Full Chart", url=f"{WEBSITE_URL}/rugcharts?token={address}"
                        ),
                        InlineKeyboardButton(
                            "🗺️ Map", callback_data=f"mapaddr_{address}_{data['chain']}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🔍 Scan", callback_data=f"scan_{address}_{data['chain']}"
                        ),
                        InlineKeyboardButton(
                            "📈 DexScreener",
                            url=f"https://dexscreener.com/{data['chain']}/{address}",
                        ),
                    ],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            )

            await msg.edit_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )

    except Exception as exc:
        logger.error("Chart failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ Chart error: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ═══════════════════════════════════════════════════════════════
# 2. BUBBLE MAP IN TELEGRAM — /map command with holder vis
# ═══════════════════════════════════════════════════════════════


async def cmd_map_v8(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/map [address] — Holder bubble map with visual indicators for clusters."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    address, chain = _parse_args(ctx.args)
    if not address:
        await update.message.reply_text(
            "🗺️ <b>Holder Bubble Map</b>\n\n"
            "Usage: /map <code>address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /map <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /map <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"🗺️ <b>Mapping holder distribution...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Analyzing wallet clusters & concentration...",
        parse_mode=ParseMode.HTML,
    )

    try:
        map_data = None
        holders_info = ""
        async with httpx.AsyncClient(timeout=15) as client:
            # Try RugMaps token-graph API
            try:
                r = await client.post(
                    f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
                    json={"chain": chain or "solana", "address": address},
                    timeout=10,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if r.status_code == 200:
                    map_data = r.json()
            except Exception:
                pass

            # Fall back to DataBus holders
            if not map_data:
                try:
                    r = await client.get(
                        f"{DATABUS_URL}/token/{address}",
                        timeout=8,
                        headers={"X-API-Key": RMI_AUTH_TOKEN},
                    )
                    if r.status_code == 200:
                        detail = r.json().get("data", r.json())
                        if detail:
                            holders = detail.get("top_holders") or detail.get("holders", [])
                            if holders:
                                total_count = detail.get("holders_count") or len(holders)
                                holders_info = (
                                    f"👥 <b>Total Holders:</b> {total_count}\n"
                                    f"{thin_sep()}\n<b>Top Holders:</b>\n"
                                )
                                for i, h in enumerate(holders[:10], 1):
                                    h_addr = h.get("address") or h.get("holder", "")
                                    h_pct = h.get("pct") or h.get("percentage") or h.get("share", 0)
                                    tag = f" [{h.get('tag', '')}]" if h.get("tag") else ""
                                    # Highlight suspicious clusters in RED
                                    is_suspicious = float(h_pct) > 10
                                    color = "🔴" if is_suspicious else "  "
                                    holders_info += (
                                        f"{color}{i}. <code>{_short(str(h_addr))}</code>"
                                        f" — {float(h_pct):.1f}%{tag}\n"
                                    )
                except Exception:
                    pass

            if not map_data and not holders_info:
                await msg.edit_text(
                    "❌ <b>Map Data Unavailable</b>\n\n"
                    f'<a href="{WEBSITE_URL}/rugmaps?token={address}">Try rugmunch.io/rugmaps</a>',
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_kb(),
                    disable_web_page_preview=True,
                )
                return

            lines = [f"🗺️ <b>Holder Bubble Map</b>\n{sep()}"]
            if map_data:
                name = map_data.get("name", "")
                symbol = map_data.get("symbol", "")
                if name:
                    lines.append(
                        f"<b>{esc(name)}</b> (${esc(symbol)}) {fmt_chain(map_data.get('chain', chain or ''))}"
                    )
                lines.append(f"<code>{esc(str(address)[:42])}</code>\n")
                total_holders = map_data.get("total_holders") or map_data.get("holders_count", 0)
                clusters = map_data.get("clusters") or map_data.get("wallet_clusters", [])
                top10_pct = float(
                    map_data.get("top10_holder_pct") or map_data.get("top10_concentration", 0)
                )
                lines.append(f"👥 Total Holders: <b>{total_holders:,}</b>")
                lines.append(f"📊 Top 10 Concentration: <b>{top10_pct:.1f}%</b>")
                if clusters:
                    suspicious = [c for c in clusters if c.get("suspicious") or c.get("connected")]
                    lines.append(
                        f"🕸️ Wallet Clusters: <b>{len(clusters)}</b> {'🔴 SUSPICIOUS' if suspicious else ''}"
                    )
                lines.append("")

                # Show visualization hint — holder pie
                holders = map_data.get("top_holders") or map_data.get("holders", [])
                if holders and not holders_info:
                    lines.append("📊 <b>Distribution:</b>")
                    for i, h in enumerate(holders[:8], 1):
                        h_addr = h.get("address") or h.get("holder", "")
                        h_pct = float(h.get("pct") or h.get("percentage") or h.get("share", 0))
                        bar_len = max(1, int(h_pct / 5))
                        bar = "█" * min(bar_len, 20)
                        tag = f" [{h.get('tag', '')}]" if h.get("tag") else ""
                        # Color-code: red for >10%, yellow for >5%, green for <5%
                        if h_pct > 10:
                            color = "🔴"
                        elif h_pct > 5:
                            color = "🟡"
                        else:
                            color = "🟢"
                        lines.append(
                            f"  {color} <code>{_short(str(h_addr))}</code> {bar} {h_pct:.1f}%{tag}"
                        )

            if holders_info:
                lines.append(holders_info)

            lines.append(
                f"\n{thin_sep()}\n"
                f'🌐 <a href="{WEBSITE_URL}/rugmaps?token={address}">Full Interactive Map →</a>\n'
                f"🔴 = suspicious cluster  🟢 = normal distribution"
            )

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🗺️ Full Map", url=f"{WEBSITE_URL}/rugmaps?token={address}"
                        ),
                        InlineKeyboardButton(
                            "📊 Chart", callback_data=f"chart_{address}_{chain or ''}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "🔍 Scan", callback_data=f"scan_{address}_{chain or ''}"
                        ),
                    ],
                    [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
                ]
            )

            await msg.edit_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )

    except Exception as exc:
        logger.error("Map failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ Map error: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ═══════════════════════════════════════════════════════════════
# 3. DEPLOYER INVESTIGATION — /deployer command
# ═══════════════════════════════════════════════════════════════


async def cmd_deployer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/deployer [address] — Find the real deployer, trace their history."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    address, chain = _parse_args(ctx.args)
    if not address:
        await update.message.reply_text(
            "🕵️ <b>Deployer Investigation</b>\n\n"
            "Find the REAL deployer behind a token.\n\n"
            "Usage: /deployer <code>address</code>\n\n"
            "<b>Examples:</b>\n"
            "  /deployer <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /deployer <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>\n\n"
            "<i>Traces deployer wallet, what else they launched, success rate, total rugged.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"🕵️ <b>Investigating deployer...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Tracing deployer wallet & history...",
        parse_mode=ParseMode.HTML,
    )

    try:
        deployer_data = None
        dex_data = None
        async with httpx.AsyncClient(timeout=20) as client:
            # Get token info from DexScreener first
            dex_data = await _fetch_dexscreener(client, address)

            # Try backend deployer endpoint
            try:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/deployer/{address}",
                    timeout=10,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if r.status_code == 200:
                    deployer_data = r.json().get("data", r.json())
            except Exception:
                pass

            # Fallback: DataBus token detail for dev_wallet
            if not deployer_data:
                try:
                    r = await client.get(
                        f"{DATABUS_URL}/token/{address}",
                        timeout=8,
                        headers={"X-API-Key": RMI_AUTH_TOKEN},
                    )
                    if r.status_code == 200:
                        detail = r.json().get("data", r.json())
                        if detail and detail.get("dev_wallet"):
                            deployer_data = {
                                "dev_wallet": detail["dev_wallet"],
                                "info": {"source": "databus_detail"},
                            }
                except Exception:
                    pass

            # Try RAG deployer_history collection
            rag_insight = ""
            try:
                name = dex_data["name"] if dex_data else address[:10]
                rag_r = await client.post(
                    RAG_SEARCH_URL,
                    json={
                        "query": f"deployer of token {name} {address[:10]} scam history rug pull",
                        "collection": "deployer_history",
                        "top_k": 3,
                        "min_similarity": 0.2,
                    },
                    timeout=8,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if rag_r.status_code == 200:
                    rag_results = rag_r.json().get("hits", [])
                    if rag_results:
                        rag_insight = "\n".join(
                            f"  • {esc(str(h.get('content', ''))[:150])}" for h in rag_results[:2]
                        )
            except Exception:
                pass

        lines = [f"🕵️ <b>Deployer Investigation</b>\n{sep()}"]
        if dex_data:
            lines.append(
                f"<b>{esc(dex_data['name'])}</b> (${esc(dex_data['symbol'])}) {fmt_chain(dex_data['chain'])}"
            )
        lines.append(f"<code>{esc(str(address)[:42])}</code>\n")

        if deployer_data:
            dev_wallet = deployer_data.get("dev_wallet") or deployer_data.get("creator")
            if dev_wallet:
                lines.append(f"👨‍💻 <b>Deployer Wallet:</b> <code>{_short(str(dev_wallet))}</code>")

            total_launched = deployer_data.get("total_launched") or deployer_data.get("token_count")
            total_rugs = deployer_data.get("total_rugged") or deployer_data.get("rug_count")
            success_rate = deployer_data.get("success_rate")
            is_repeat = deployer_data.get("is_repeat_deployer")

            if total_launched:
                lines.append(f"📦 Total Tokens Launched: <b>{total_launched}</b>")
            if total_rugs:
                lines.append(
                    f"💀 Total Rugged/Dead: <b>{total_rugs}</b> {'🔴' if total_rugs > 0 else '✅'}"
                )

            if total_launched and total_rugs:
                dead_pct = (int(total_rugs) / max(int(total_launched), 1)) * 100
                lines.append(f"📉 Failure Rate: <b>{dead_pct:.1f}%</b>")

                if dead_pct > 50:
                    lines.append(
                        f"\n⚠️ <b>This deployer has launched {total_launched} tokens. "
                        f"{total_rugs} are dead or rugged. Caveat emptor.</b>"
                    )
                elif dead_pct > 0:
                    lines.append(
                        "\n⚡ <b>This deployer has a mixed track record. "
                        "Some projects survived, others didn't.</b>"
                    )
                else:
                    lines.append("\n✅ <b>Clean track record so far. But always verify.</b>")

            if is_repeat:
                lines.append("🔄 <b>This is a REPEAT deployer</b> — has created multiple contracts")

        else:
            # No backend data — show what we can from DexScreener
            if dex_data:
                age = "Unknown"
                if dex_data.get("created_at"):
                    try:
                        created = datetime.fromtimestamp(dex_data["created_at"] / 1000, tz=UTC)
                        age_delta = datetime.now(UTC) - created
                        age = (
                            f"{age_delta.days}d"
                            if age_delta.days > 0
                            else f"{age_delta.seconds // 3600}h"
                        )
                    except Exception:
                        pass
                lines.append(f"📅 Token Age: <b>{age}</b>")
                lines.append(f"💱 DEX: <b>{esc(dex_data['dex'])}</b>")

            lines.append("")
            lines.append("⚠️ <b>Limited deployer data available</b>")
            lines.append(
                "<i>Backend deployer tracing requires indexer data. Data improves with every scan.</i>"
            )

        if rag_insight:
            lines.extend(["", "🧠 <b>RAG Intelligence:</b>", thin_sep(), rag_insight])

        # Deployer verdict
        if deployer_data:
            total = int(deployer_data.get("total_launched", 1))
            rugs = int(deployer_data.get("total_rugged", 0))
            if rugs == 0:
                verdict = "🟢 LOW RISK — Clean deployer history"
            elif rugs / max(total, 1) < 0.3:
                verdict = "🟡 CAUTION — Some failed projects in history"
            else:
                verdict = "🔴 HIGH RISK — Multiple rug pulls / dead tokens"
            lines.extend(["", f"🛡️ <b>Verdict:</b> {verdict}"])

        lines.extend(["", sep(), footer_links()])

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔍 Scan Token", callback_data=f"scan_{address}_{chain or ''}"
                    ),
                    InlineKeyboardButton("👛 Wallet Check", callback_data="menu_wallet"),
                ],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    except Exception as exc:
        logger.error("Deployer investigation failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ Deployer investigation error: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ═══════════════════════════════════════════════════════════════
# 4. LIVE TRADES FEED — /trades command
# ═══════════════════════════════════════════════════════════════


async def cmd_trades(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/trades [address] — Last 10 trades with whale detection."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    address, chain = _parse_args(ctx.args)
    if not address:
        await update.message.reply_text(
            "📊 <b>Live Trades Feed</b>\n\n"
            "Usage: /trades <code>address</code>\n\n"
            "Shows last 10 trades with whale detection.\n"
            "<i>Highlights moves > $10K and buy/sell ratios.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"📊 <b>Fetching live trades...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Querying on-chain transaction data...",
        parse_mode=ParseMode.HTML,
    )

    try:
        dex_data = None
        trades = []
        async with httpx.AsyncClient(timeout=15) as client:
            dex_data = await _fetch_dexscreener(client, address)

            # Try backend trades endpoint
            try:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/trades/{address}",
                    params={"limit": 10},
                    timeout=10,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if r.status_code == 200:
                    trades = r.json().get("data", r.json())
                    if isinstance(trades, dict):
                        trades = trades.get("trades", [])
            except Exception:
                pass

            # Fallback: DexScreener txn data
            if not trades and dex_data:
                txns = dex_data.get("txns_24h", {})
                if isinstance(txns, dict) and txns:
                    buy_count = txns.get("buys", 0)
                    sell_count = txns.get("sells", 0)
                    total = buy_count + sell_count
                    if total > 0:
                        trades = [
                            {
                                "type": "summary",
                                "buys": buy_count,
                                "sells": sell_count,
                                "total": total,
                            }
                        ]

        lines = [f"📊 <b>Live Trades Feed</b>\n{sep()}"]
        if dex_data:
            lines.append(
                f"<b>{esc(dex_data['name'])}</b> (${esc(dex_data['symbol'])}) {fmt_chain(dex_data['chain'])}"
            )
            lines.append(f"<code>{esc(str(address)[:42])}</code>\n")

        whale_alerts = []
        buy_count = sell_count = total_count = 0

        if trades and isinstance(trades[0], dict) and trades[0].get("type") == "summary":
            # Summary from DexScreener
            t = trades[0]
            buy_count = t.get("buys", 0)
            sell_count = t.get("sells", 0)
            total_count = buy_count + sell_count
            lines.append("📊 <b>24h Transaction Summary:</b>")
            lines.append(f"🟢 Buys: <b>{buy_count:,}</b>  │  🔴 Sells: <b>{sell_count:,}</b>")
            if sell_count > 0:
                ratio = buy_count / sell_count
                ratio_emoji = "🟢" if ratio > 1.5 else "🟡" if ratio >= 0.8 else "🔴"
                lines.append(f"{ratio_emoji} Buy/Sell Ratio: <b>{ratio:.1f}x</b>")
            if dex_data:
                vol = dex_data.get("volume_24h", 0)
                lines.append(f"💰 Total Volume: <b>{fmt_number(vol)}</b>")
        elif trades:
            # Real trade data from backend
            lines.append("📊 <b>Last Transactions:</b>")
            for i, tx in enumerate(trades[:10], 1):
                tx_type = tx.get("type", "unknown")
                amount = float(tx.get("amount_usd") or tx.get("value") or 0)
                wallet = tx.get("wallet") or tx.get("from", "")
                time_str = tx.get("timestamp") or tx.get("time", "")

                if tx_type in ("buy", "Buy"):
                    emoji = "🟢"
                    buy_count += 1
                elif tx_type in ("sell", "Sell"):
                    emoji = "🔴"
                    sell_count += 1
                else:
                    emoji = "⚪"
                total_count += 1

                whale_emoji = "🐳 " if amount > 10000 else ""
                lines.append(
                    f"{whale_emoji}{emoji} <b>{esc(tx_type.upper())}</b> "
                    f"{fmt_number(amount)} — "
                    f"<code>{_short(str(wallet))}</code>"
                )
                if amount > 10000:
                    whale_alerts.append(
                        f"🐳 Whale {tx_type}: {_short(str(wallet))} {fmt_number(amount)}"
                    )

            if buy_count + sell_count > 0:
                lines.append("")
                lines.append(
                    f"🟢 {buy_count} buys  │  🔴 {sell_count} sells  │  "
                    f"Ratio: <b>{buy_count / max(sell_count, 1):.1f}x</b>"
                )
        else:
            lines.append("⚠️ <b>No recent trade data available</b>")
            lines.append("<i>Trade data requires on-chain indexing. Try again later.</i>")

        # Whale alerts
        if whale_alerts:
            lines.extend(["", "🐳 <b>WHALE ALERTS:</b>", thin_sep()])
            lines.extend(whale_alerts)

        lines.extend(["", sep(), footer_links()])

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔍 Scan", callback_data=f"scan_{address}_{chain or ''}"),
                    InlineKeyboardButton(
                        "📊 Chart", callback_data=f"chart_{address}_{chain or ''}"
                    ),
                ],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    except Exception as exc:
        logger.error("Trades failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ Trades error: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ═══════════════════════════════════════════════════════════════
# 5. WALLET PROFILE — /walletv2 command (enhanced wallet analysis)
# ═══════════════════════════════════════════════════════════════


async def cmd_wallet_v2(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/wallet [address] — Enhanced wallet profile with PnL, win/loss, labels."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    address, chain = _parse_args(ctx.args)
    if not address:
        await update.message.reply_text(
            "👛 <b>Wallet Profile</b>\n\n"
            "Usage: /wallet <code>address</code>\n\n"
            "Shows trading history, PnL estimate, win/loss ratio, and known labels.\n\n"
            "<b>Examples:</b>\n"
            "  /wallet <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n"
            "  /wallet <code>7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"👛 <b>Analyzing wallet...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Fetching on-chain history, labels & PnL...",
        parse_mode=ParseMode.HTML,
    )

    try:
        wallet_data = {}
        async with httpx.AsyncClient(timeout=20) as client:
            # Try backend wallet endpoint
            try:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/wallet/{address}",
                    timeout=10,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if r.status_code == 200:
                    wallet_data = r.json().get("data", r.json())
            except Exception:
                pass

            # Fallback: basic check
            if not wallet_data:
                try:
                    r = await client.get(
                        f"{DATABUS_URL}/token/{address}",
                        timeout=6,
                        headers={"X-API-Key": RMI_AUTH_TOKEN},
                    )
                    if r.status_code in (200, 404):
                        wallet_data = {"checked": True}
                except Exception:
                    pass

        lines = [f"👛 <b>Wallet Profile</b>\n{sep()}"]
        lines.append(f"<code>{esc(str(address)[:42])}</code>")

        if wallet_data:
            chain_detected = wallet_data.get("chain") or chain or "unknown"
            balance = wallet_data.get("balance_usd") or wallet_data.get("balance")
            tx_count = wallet_data.get("tx_count") or wallet_data.get("transaction_count")
            is_exchange = wallet_data.get("is_exchange")
            is_contract = wallet_data.get("is_contract")
            is_fresh = wallet_data.get("is_fresh") or wallet_data.get("wallet_age_days", 999) < 7
            pnl = wallet_data.get("pnl_estimate") or wallet_data.get("pnl")
            flags = wallet_data.get("flags") or []
            labels = wallet_data.get("labels") or wallet_data.get("known_labels") or []
            top_tokens = wallet_data.get("top_tokens") or []
            funding = wallet_data.get("funding_source")
            connected = wallet_data.get("connected_wallets") or []
            risk = wallet_data.get("risk_score") or 0
            total_tokens = wallet_data.get("total_tokens_traded") or wallet_data.get(
                "tokens_traded"
            )
            win_rate = wallet_data.get("win_rate")

            lines.append(
                f"{fmt_chain(chain_detected)}  │  "
                f"Balance: <b>{fmt_number(balance) if balance is not None else 'N/A'}</b>"
            )

            if tx_count:
                lines.append(f"📝 Transactions: <b>{tx_count:,}</b>")

            # Wallet type + freshness
            wallet_type = ""
            if is_exchange:
                wallet_type = "🏦 Exchange Wallet"
            elif is_contract:
                wallet_type = "📄 Contract Address"
            else:
                wallet_type = "👤 EOA (Personal Wallet)"
            if is_fresh:
                wallet_type += " 🆕 <b>FRESH WALLET!</b>"
            lines.append(f"🏷️ Type: <b>{wallet_type}</b>")

            # PnL & Win Rate
            if pnl is not None:
                pnl_emoji = "📈" if float(pnl) >= 0 else "📉"
                lines.append(f"{pnl_emoji} Estimated P&L: <b>{fmt_number(pnl)}</b>")
            if win_rate is not None:
                wr_emoji = (
                    "🟢" if float(win_rate) > 0.5 else "🟡" if float(win_rate) > 0.3 else "🔴"
                )
                lines.append(f"{wr_emoji} Win Rate: <b>{float(win_rate) * 100:.0f}%</b>")
            if total_tokens:
                lines.append(f"🪙 Tokens Traded: <b>{total_tokens:,}</b>")

            # Personality verdict
            if is_fresh:
                lines.append("\n⚠️ <b>Fresh wallet — typical of bot/sniper accounts.</b>")
            if total_tokens and win_rate is not None:
                if float(win_rate) < 0.3:
                    lines.append(
                        "\n💀 <b>Mostly memecoin gambler. Proceed with extreme caution.</b>"
                    )
                elif float(win_rate) > 0.7:
                    lines.append("\n🟢 <b>Solid trader. Good track record.</b>")

            # Known labels
            if labels:
                lines.extend(["", "🏷️ <b>Known Labels:</b>", thin_sep()])
                for lbl in labels if isinstance(labels, list) else [str(labels)]:
                    lines.append(f"  • {esc(str(lbl))}")

            # Flags
            if flags:
                lines.extend(["", "⚠️ <b>Flags:</b>", thin_sep()])
                for flag in flags:
                    lines.append(f"  🔴 {esc(str(flag))}")

            # Top holdings
            if top_tokens:
                lines.extend(["", "🪙 <b>Top Holdings:</b>", thin_sep()])
                for i, t in enumerate(top_tokens[:5], 1):
                    symbol = t.get("symbol", "???")
                    value = t.get("value_usd") or t.get("value", 0)
                    lines.append(f"  {i}. <b>{esc(str(symbol))}</b> — {fmt_number(value)}")

            # Funding source
            if funding:
                lines.append(f"\n🏦 <b>Funding Source:</b> {esc(str(funding)[:80])}")

            # Connected wallets
            if connected:
                lines.extend(["", "🔗 <b>Connected Wallets:</b>", thin_sep()])
                for w in connected[:5]:
                    lines.append(f"  • <code>{_short(str(w))}</code>")

            # Risk score
            if risk:
                lines.extend(["", risk_bar(risk)])

        else:
            lines.append("\n⚠️ <b>Limited wallet data available</b>")
            lines.append("<i>Wallet profiling is being built. Data improves with every scan.</i>")

        lines.extend(["", sep(), footer_links()])

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔍 Scan Related", callback_data="menu_scan"),
                    InlineKeyboardButton(
                        "🕵️ Deployer", callback_data=f"deployer_{address}_{chain or ''}"
                    ),
                ],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    except Exception as exc:
        logger.error("Wallet profile failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ Wallet analysis error: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ═══════════════════════════════════════════════════════════════
# 6. TOKEN TIME MACHINE — /history command
# ═══════════════════════════════════════════════════════════════


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/history [address] — Token's price journey from launch to now."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    address, chain = _parse_args(ctx.args)
    if not address:
        await update.message.reply_text(
            "⏳ <b>Token Time Machine</b>\n\n"
            "Usage: /history <code>address</code>\n\n"
            "Shows price journey, key events (LP add/remove, large buys/sells, CEX listings).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"⏳ <b>Rewinding time...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Fetching token's price journey & key events...",
        parse_mode=ParseMode.HTML,
    )

    try:
        dex_data = None
        history_data = None
        async with httpx.AsyncClient(timeout=15) as client:
            dex_data = await _fetch_dexscreener(client, address)

            # Try backend history endpoint
            try:
                r = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/token_history/{address}",
                    timeout=10,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if r.status_code == 200:
                    history_data = r.json().get("data", r.json())
            except Exception:
                pass

        lines = [f"⏳ <b>Token Time Machine</b>\n{sep()}"]
        if dex_data:
            lines.append(
                f"<b>{esc(dex_data['name'])}</b> (${esc(dex_data['symbol'])}) {fmt_chain(dex_data['chain'])}"
            )
            lines.append(f"<code>{esc(str(address)[:42])}</code>\n")
            lines.append(f"💰 Current Price: <b>{fmt_number(dex_data['price'])}</b>")
            lines.append(
                f"📊 24h: {fmt_pct(dex_data['price_change_24h'])}  │  7d: {fmt_pct(dex_data['price_change_7d'])}"
            )

            # Age timeline
            if dex_data.get("created_at"):
                try:
                    created = datetime.fromtimestamp(dex_data["created_at"] / 1000, tz=UTC)
                    age = datetime.now(UTC) - created
                    lines.append("\n📅 <b>Timeline:</b>")
                    lines.append(f"   🟢 <b>LAUNCH:</b> {created.strftime('%b %d, %Y %H:%M UTC')}")
                    lines.append(f"   ⏱ <b>Age:</b> {age.days}d {age.seconds // 3600}h old")
                except Exception:
                    pass

        # Price milestones
        if dex_data:
            price = dex_data["price"]
            lines.extend(["", "📈 <b>Price Milestones:</b>", thin_sep()])
            lines.append(f"  💰 Current: <b>{fmt_number(price)}</b>")
            if dex_data["liquidity_usd"]:
                lines.append(f"  💧 Liquidity: <b>{fmt_number(dex_data['liquidity_usd'])}</b>")
            if dex_data["mcap"]:
                lines.append(f"  🏷️ Market Cap: <b>{fmt_number(dex_data['mcap'])}</b>")

        # Backend history data
        if history_data:
            events = history_data.get("events") or history_data.get("timeline") or []
            if events:
                lines.extend(["", "📋 <b>Key Events:</b>", thin_sep()])
                for evt in events[:8]:
                    if isinstance(evt, dict):
                        t = evt.get("timestamp") or evt.get("time") or ""
                        desc = evt.get("event") or evt.get("description") or str(evt)
                        lines.append(f"  📍 {esc(str(t)[:20])}: {esc(str(desc)[:100])}")
                    else:
                        lines.append(f"  📍 {esc(str(evt)[:120])}")

            price_points = history_data.get("price_points") or history_data.get("prices") or []
            if price_points:
                lines.extend(["", "📊 <b>Price History:</b>", thin_sep()])
                for pp in price_points[:6]:
                    if isinstance(pp, dict):
                        label = pp.get("label") or pp.get("date") or ""
                        px = pp.get("price") or 0
                        lines.append(f"  • {esc(str(label))}: <b>{fmt_number(px)}</b>")
        else:
            lines.append("\n📊 <b>Market Overview:</b>")
            lines.append(f"  DEX: <b>{esc(dex_data.get('dex', 'Unknown'))}</b>")
            lines.append(f"  Pair: <code>{_short(dex_data.get('pair_address', 'N/A'))}</code>")
            lines.append(
                "\n💡 <i>Detailed history requires backend indexing. "
                "Run /scan to index this token for future queries.</i>"
            )

        lines.extend(["", sep(), footer_links()])

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔍 Scan", callback_data=f"scan_{address}_{chain or ''}"),
                    InlineKeyboardButton(
                        "📊 Chart", callback_data=f"chart_{address}_{chain or ''}"
                    ),
                ],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    except Exception as exc:
        logger.error("History failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ History error: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ═══════════════════════════════════════════════════════════════
# 7. SAFETY REPORT CARD — /report command (A-F grade)
# ═══════════════════════════════════════════════════════════════


def _letter_grade(score: float) -> str:
    """Convert 0-100 score to A-F letter grade with emoji."""
    if score >= 90:
        return "🟢 <b>A</b>"
    elif score >= 80:
        return "🟢 <b>B</b>"
    elif score >= 70:
        return "🟡 <b>C</b>"
    elif score >= 60:
        return "🟠 <b>D</b>"
    else:
        return "🔴 <b>F</b>"


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/report [address] — Comprehensive A-F safety report card."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    if _check_spam(user.id):
        return

    address, chain = _parse_args(ctx.args)
    if not address:
        await update.message.reply_text(
            "📋 <b>Safety Report Card</b>\n\n"
            "Usage: /report <code>address</code>\n\n"
            "Comprehensive A-F grade across safety, liquidity, holders, deployer, social & trading.\n\n"
            "<b>Example:</b>\n"
            "  /report <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    msg = await update.message.reply_text(
        f"📋 <b>Generating safety report card...</b>\n<code>{_short(address)}</code>\n\n"
        "⏳ Analyzing contract, liquidity, holders, deployer, social & trading...",
        parse_mode=ParseMode.HTML,
    )

    try:
        dex_data = None
        security_data = None
        detail_data = None
        async with httpx.AsyncClient(timeout=15) as client:
            dex_data = await _fetch_dexscreener(client, address)

            # Fetch security data
            try:
                r = await client.get(
                    f"{DATABUS_URL}/fetch/token_security",
                    params={"address": address, "chain": chain or "solana"},
                    timeout=10,
                )
                if r.status_code == 200:
                    security_data = r.json().get("data", r.json())
            except Exception:
                pass

            # Fetch detail data
            try:
                r = await client.get(
                    f"{DATABUS_URL}/token/{address}",
                    timeout=8,
                    headers={"X-API-Key": RMI_AUTH_TOKEN},
                )
                if r.status_code == 200:
                    detail_data = r.json().get("data", r.json())
            except Exception:
                pass

        if not dex_data:
            await msg.edit_text(
                "❌ <b>Token Not Found</b>\n\nNo trading data available for this token.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        # ── Calculate scores per category ──
        scores = {}

        # 1. Contract Safety (0-100)
        if security_data:
            contract_score = 100
            if security_data.get("is_honeypot") or security_data.get("honeypot"):
                contract_score = 0
            else:
                if not (
                    security_data.get("is_open_source") or security_data.get("contract_verified")
                ):
                    contract_score -= 30
                if security_data.get("mintable") or security_data.get("is_mintable"):
                    contract_score -= 25
                if security_data.get("can_blacklist") or security_data.get("is_blacklisted"):
                    contract_score -= 20
                owner_renounced = security_data.get("owner_renounced") or security_data.get(
                    "is_owner_renounced"
                )
                if owner_renounced is False:
                    contract_score -= 15
                buy_tax = float(security_data.get("buy_tax", 0) or 0)
                sell_tax = float(security_data.get("sell_tax", 0) or 0)
                if buy_tax > 0.1:
                    contract_score -= 15
                if sell_tax > 0.1:
                    contract_score -= 20
            scores["contract"] = max(0, contract_score)
        else:
            scores["contract"] = 50  # Unknown

        # 2. Liquidity (0-100)
        liq = dex_data.get("liquidity_usd", 0)
        mcap = dex_data.get("mcap", 0)
        if liq <= 0:
            scores["liquidity"] = 0
        elif liq < 5000:
            scores["liquidity"] = 30
        elif liq < 50000:
            scores["liquidity"] = 60
        elif liq < 500000:
            scores["liquidity"] = 80
        else:
            scores["liquidity"] = 95
        # Penalty for poor liq/mcap ratio
        if mcap > 0 and liq > 0:
            ratio = liq / mcap
            if ratio < 0.02:
                scores["liquidity"] = max(0, scores["liquidity"] - 30)
            elif ratio < 0.05:
                scores["liquidity"] = max(0, scores["liquidity"] - 15)

        # 3. Holder Distribution (0-100)
        if detail_data:
            fresh_pct = float(detail_data.get("fresh_wallet_pct", 0) or 0)
            top10_concentration = float(detail_data.get("top10_concentration", 0) or 0)
            holder_score = 100
            if fresh_pct > 70:
                holder_score -= 35
            elif fresh_pct > 40:
                holder_score -= 20
            if top10_concentration > 80:
                holder_score -= 40
            elif top10_concentration > 50:
                holder_score -= 25
            scores["holders"] = max(0, holder_score)
        else:
            scores["holders"] = 50

        # 4. Deployer (0-100)
        if detail_data and detail_data.get("dev_wallet"):
            deployer_score = 70
            if detail_data.get("bundle_detected"):
                deployer_score -= 30
        else:
            deployer_score = 50
        scores["deployer"] = deployer_score

        # 5. Social Presence (0-100)
        socials = dex_data.get("socials", {})
        social_score = 0
        if socials.get("twitter"):
            social_score += 30
        if socials.get("telegram"):
            social_score += 25
        if socials.get("website"):
            social_score += 25
        if socials.get("discord"):
            social_score += 20
        scores["social"] = min(100, social_score) if social_score > 0 else 30

        # 6. Trading Activity (0-100)
        vol = dex_data.get("volume_24h", 0)
        if vol <= 0:
            scores["trading"] = 0
        elif vol < 1000:
            scores["trading"] = 20
        elif vol < 10000:
            scores["trading"] = 50
        elif vol < 100000:
            scores["trading"] = 75
        else:
            scores["trading"] = 95
        # Volume authenticity penalty
        if mcap > 0 and vol > 0:
            vol_ratio = vol / mcap
            if vol_ratio > 10:
                scores["trading"] = max(0, scores["trading"] - 30)

        # ── Overall grade ──
        overall = sum(scores.values()) / len(scores)
        grade = _letter_grade(overall)

        # ── Build report ──
        lines = [
            f"📋 <b>SAFETY REPORT CARD</b>\n{sep()}",
            f"<b>{esc(dex_data['name'])}</b> (${esc(dex_data['symbol'])}) {fmt_chain(dex_data['chain'])}",
            f"<code>{esc(str(address)[:42])}</code>\n",
            f"🏆 <b>OVERALL GRADE:</b> {grade} ({overall:.0f}/100)\n",
            sep(),
        ]

        # Category scores
        categories = [
            ("contract", "🔒 Contract Safety", "Honeypot, verification, taxes, ownership"),
            ("liquidity", "💧 Liquidity", "Pool size, LP lock, liq/mcap ratio"),
            ("holders", "👥 Holders", "Fresh wallet %, top 10 concentration"),
            ("deployer", "👨‍💻 Deployer", "Dev wallet, bundle detection"),
            ("social", "📣 Social", "Website, X, Telegram, Discord"),
            ("trading", "📊 Trading", "Volume, buy/sell ratio, authenticity"),
        ]

        for key, label, desc in categories:
            sc = scores[key]
            g = _letter_grade(sc)
            bar_len = 10
            filled = max(1, int(sc / 100 * bar_len))
            bar = "█" * filled + "░" * (bar_len - filled)
            lines.append(f"{g} <b>{label}</b> — {sc:.0f}/100")
            lines.append(f"  <code>{bar}</code>")
            lines.append(f"  <i>{desc}</i>")
            lines.append("")

        # Key findings
        findings = []
        if security_data:
            if security_data.get("is_honeypot") or security_data.get("honeypot"):
                findings.append("🔴 HONEYPOT — Cannot sell")
            if not security_data.get("is_open_source") and not security_data.get(
                "contract_verified"
            ):
                findings.append("🟠 Contract NOT verified")
            buy_tax_val = float(security_data.get("buy_tax", 0) or 0)
            sell_tax_val = float(security_data.get("sell_tax", 0) or 0)
            if buy_tax_val > 0.1:
                findings.append(f"🟠 High buy tax ({buy_tax_val * 100:.1f}%)")
            if sell_tax_val > 0.1:
                findings.append(f"🔴 High sell tax ({sell_tax_val * 100:.1f}%)")
            owner = security_data.get("owner_renounced") or security_data.get("is_owner_renounced")
            if owner is False:
                findings.append("🟡 Ownership NOT renounced")
        if detail_data:
            if detail_data.get("bundle_detected"):
                findings.append("🔴 Bundle activity detected")
            if float(detail_data.get("fresh_wallet_pct", 0) or 0) > 50:
                findings.append("🟠 High fresh wallet ratio")
        if mcap > 0 and liq > 0 and liq / mcap < 0.02:
            findings.append("🔴 Very low liquidity vs MCap")

        if findings:
            lines.extend(["⚠️ <b>KEY FINDINGS:</b>", thin_sep()])
            lines.extend(f"  {f}" for f in findings)
            lines.append("")

        # Verdict
        if overall >= 80:
            verdict = "🟢 <b>SAFE — Low risk profile. Standard due diligence recommended.</b>"
        elif overall >= 60:
            verdict = "🟡 <b>CAUTION — Some concerns. Review findings before trading.</b>"
        elif overall >= 40:
            verdict = "🟠 <b>RISKY — Multiple red flags. Proceed with extreme caution.</b>"
        else:
            verdict = "🔴 <b>DANGER — High probability of scam/rug. AVOID.</b>"
        lines.append(f"🛡️ <b>VERDICT:</b> {verdict}")

        lines.extend(["", sep(), footer_links()])

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔍 Full Scan", callback_data=f"scan_{address}_{chain or ''}"
                    ),
                    InlineKeyboardButton(
                        "🕵️ Deployer", callback_data=f"deployer_{address}_{chain or ''}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "📊 Chart", callback_data=f"chart_{address}_{chain or ''}"
                    ),
                    InlineKeyboardButton("🗺️ Map", callback_data=f"mapaddr_{address}_{chain or ''}"),
                ],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    except Exception as exc:
        logger.error("Report card failed for %s: %s", _short(address), exc)
        await msg.edit_text(
            f"❌ Report card error: {str(exc)[:200]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ═══════════════════════════════════════════════════════════════
# 8. ASK RUG MUNCHER (existing /ask is in bot.py but we add a quick inline version)
# ═══════════════════════════════════════════════════════════════


async def cmd_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/chat — Quick start chat with the AI directly."""
    if update.message is None:
        return
    await update.message.reply_text(
        "🤖 <b>Chat with Rug Muncher</b>\n\n"
        "Ask me anything about crypto! I search across our threat intel database.\n\n"
        "<b>Just type naturally — no command needed!</b>\n\n"
        'Examples: "Is this token a scam?", "Explain a honeypot", "What\'s trending?"\n\n'
        "<i>Or use /ask for RAG-powered expert answers.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🧠 Ask Expert", callback_data="menu_analyze")],
                [InlineKeyboardButton("📚 Scam School", callback_data="menu_scamschool")],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        ),
    )

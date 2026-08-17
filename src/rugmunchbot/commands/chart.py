"""
📊 Price Chart - Powered by RugCharts OHLCV + DexScreener
=============================================================
Generates a chart IMAGE for any token from the RugCharts engine
(/api/v1/rugcharts/ohlcv/...) and sends it as a Telegram photo.
Falls back to the text sparkline + web link when image generation
is unavailable.

Usage: /chart <address> [chain]
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import httpx

from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    COINGECKO,
    DEXSCREENER,
    WEBSITE_URL,
    backend_headers,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi.chart")

# Unicode sparkline blocks (8 levels)
_SPARKLINE_CHARS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]

# DexScreener chart URL template
_DEXSCREENER_CHART_URL = "https://dexscreener.com/{chain}/{pair_address}"


def _sparkline(prices: list[float], width: int = 8) -> str:
    """Map price points to unicode sparkline blocks.

    Args:
        prices: Chronological price points (oldest → newest).
        width: Number of sparkline characters to output.

    Returns:
        Sparkline string of unicode block characters.
    """
    if not prices:
        return ""
    mn, mx = min(prices), max(prices)
    if mx == mn:
        return _SPARKLINE_CHARS[3] * min(width, len(prices))
    # Sample `width` points evenly
    step = max(len(prices) // width, 1)
    sampled = [prices[min(i * step, len(prices) - 1)] for i in range(width)]
    return "".join(_SPARKLINE_CHARS[min(int((p - mn) / (mx - mn) * 7), 7)] for p in sampled)


def _pseudo_sparkline(
    current_price: float,
    change_1h: float,
    change_6h: float | None,
    change_24h: float,
    change_7d: float,
) -> str:
    """Generate a sparkline from DexScreener price-change percentages.

    When CoinGecko has no historical data (common for shitcoins), we
    reconstruct approximate price points from the available change data.
    """
    # Build relative price history: 7d → 24h → 6h → 1h → now
    points: list[float] = []
    if change_7d != 0:
        points.append(current_price / (1 + change_7d / 100))
    if change_24h != 0:
        if change_7d == 0:
            points.append(current_price / (1 + change_24h / 100))
        else:
            # Interpolate midpoint between 7d and 24h
            p_7d = current_price / (1 + change_7d / 100)
            p_24h = current_price / (1 + change_24h / 100)
            points.append((p_7d + p_24h) / 2)
    if change_6h is not None and change_6h != 0:
        points.append(current_price / (1 + change_6h / 100))
    if change_1h != 0:
        points.append(current_price / (1 + change_1h / 100))
    else:
        # Duplicate previous point if no 1h change
        points.append(points[-1] if points else current_price)
    points.append(current_price)

    return _sparkline(points, width=8)


async def _fetch_cg_price_history(
    client: httpx.AsyncClient,
    token_name: str,
    token_symbol: str,
) -> list[float] | None:
    """Try CoinGecko for historical price data.

    Searches by name/symbol, fetches 7-day price history,
    returns list of prices (oldest → newest).
    """
    search_query = token_name or token_symbol
    if not search_query or search_query == "Unknown":
        return None
    try:
        # Step 1: Search for coin ID
        sr = await client.get(
            f"{COINGECKO}/search?query={search_query[:50]}",
            timeout=8,
        )
        if sr.status_code != 200:
            return None
        coins = sr.json().get("coins", [])
        if not coins:
            return None
        coin_id = coins[0].get("id")
        if not coin_id:
            return None

        # Step 2: Fetch 7-day market chart
        chart_r = await client.get(
            f"{COINGECKO}/coins/{coin_id}/market_chart?vs_currency=usd&days=7",
            timeout=10,
        )
        if chart_r.status_code != 200:
            return None
        raw = chart_r.json().get("prices", [])
        if not raw or len(raw) < 2:
            return None
        return [p[1] for p in raw]  # {timestamp, price}[]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("CoinGecko price history failed: %s", exc)
        return None


# ══════════════════════════════════════════════
# v6 — IMAGE RENDERING (Pillow, already in bot venv)
# ══════════════════════════════════════════════


def render_chart_png(
    candles: list[dict[str, Any]],
    name: str,
    symbol: str,
    chain: str = "",
) -> bytes | None:
    """Render an OHLCV candlestick chart to PNG bytes (v6).

    Uses Pillow (already in the bot venv) — no matplotlib dependency.
    Returns ``None`` when Pillow is unavailable so callers fall back to the
    text sparkline + web link. Pure CPU work — safe to run in the async
    handler (small datasets, <100 candles).
    """
    if not candles:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except (ImportError, OSError, RuntimeError) as exc:
        logger.debug("Pillow unavailable, skipping chart image: %s", exc)
        return None

    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    vols: list[float] = []
    for c in candles:
        try:
            closes.append(float(c["close"]))
            highs.append(float(c["high"]))
            lows.append(float(c["low"]))
            vols.append(float(c.get("volume") or 0))
        except (KeyError, ValueError, TypeError):
            continue
    if not closes:
        return None

    w, h = 1000, 640
    pad_l, pad_r, pad_t, pad_b = 70, 20, 50, 60
    bg = (15, 17, 21)
    grid_c = (35, 38, 46)
    text_c = (154, 160, 166)
    title_c = (232, 234, 237)
    up_c = (38, 166, 154)
    down_c = (239, 83, 80)

    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=20)
        ImageFont.load_default(size=16)
        tiny = ImageFont.load_default(size=14)
    except TypeError:
        font = ImageFont.load_default()
        tiny = font

    # Plot area
    plot_w = w - pad_l - pad_r
    plot_h = h - pad_t - pad_b
    vol_h = int(plot_h * 0.22)
    price_h = plot_h - vol_h - 30
    price_top = pad_t
    price_bot = pad_t + price_h
    vol_top = price_bot + 30
    vol_bot = vol_top + vol_h

    mn, mx = min(lows), max(highs)
    if mx == mn:
        mx = mn + 1.0

    n = len(closes)

    def x_of(i: int) -> int:
        return pad_l + int(i * plot_w / max(n - 1, 1))

    def y_of(v: float) -> int:
        return price_bot - int((v - mn) / (mx - mn) * price_h)

    # Grid + labels
    for g in range(5):
        gy = price_top + int(price_h * g / 4)
        draw.line([(pad_l, gy), (w - pad_r, gy)], fill=grid_c, width=1)
        val = mx - (mx - mn) * g / 4
        label = f"${val:,.4f}" if val < 1000 else f"${val:,.0f}"
        draw.text((8, gy - 10), label, font=tiny, fill=text_c)
    for g in range(6):
        gx = pad_l + int(plot_w * g / 5)
        draw.line([(gx, price_top), (gx, vol_bot)], fill=grid_c, width=1)

    color = up_c if closes[-1] >= closes[0] else down_c

    # Moving averages (MA7, MA25)
    def ma(data, period):
        result = []
        for i in range(len(data)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(data[i - period + 1 : i + 1]) / period)
        return result

    ma7 = ma(closes, min(7, n))
    ma25 = ma(closes, min(25, n))
    for i in range(1, n):
        for ma_data, ma_color in [(ma7, (255, 183, 77)), (ma25, (100, 181, 246))]:
            if ma_data[i] is not None and ma_data[i - 1] is not None:
                draw.line(
                    [(x_of(i - 1), y_of(ma_data[i - 1])), (x_of(i), y_of(ma_data[i]))],
                    fill=ma_color,
                    width=2,
                )

    # Candlesticks (wick = high→low, body = close line)
    body_w = max(2, min(12, int(plot_w / n) - 2))
    for i in range(n):
        cx = x_of(i)
        draw.line([(cx, y_of(highs[i])), (cx, y_of(lows[i]))], fill=color, width=2)
        draw.line(
            [(cx - body_w // 2, y_of(closes[i])), (cx + body_w // 2, y_of(closes[i]))],
            fill=color,
            width=3,
        )

    # Volume bars
    vmax = max(vols) if vols and max(vols) > 0 else 1
    for i in range(n):
        vh = int(vol_h * vols[i] / vmax)
        if vh <= 0:
            continue
        bx = x_of(i) - body_w // 2
        draw.rectangle((bx, vol_bot - vh, bx + body_w, vol_bot), fill=color)

    # Border + titles
    draw.rectangle((pad_l, price_top, w - pad_r, vol_bot), outline=grid_c, width=1)
    title = f"{name} ({symbol})"
    if chain:
        title += f"  ·  {chain.capitalize()}"
    draw.text((pad_l, 12), title, font=font, fill=title_c)
    # MA legend
    if ma7[-1] is not None:
        draw.text((pad_l + 200, 12), f"MA7: ${ma7[-1]:.4f}", font=tiny, fill=(255, 183, 77))
    if ma25[-1] is not None:
        draw.text((pad_l + 350, 12), f"MA25: ${ma25[-1]:.4f}", font=tiny, fill=(100, 181, 246))
    draw.text((pad_l, h - 30), "Price (USD)", font=tiny, fill=text_c)
    draw.text((w - pad_r - 90, vol_top - 22), "Volume", font=tiny, fill=text_c)

    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()
    except (OSError, ValueError, RuntimeError):
        logger.debug("chart png render failed", exc_info=True)
        return None


async def _fetch_rugcharts_ohlcv(
    client: httpx.AsyncClient, chain: str, pair_address: str, timeframe: str = "1h"
) -> list[dict[str, Any]] | None:
    """Fetch OHLCV candles from the backend RugCharts engine.

    Args:
        timeframe: "15m", "1h", "4h", "1d", "1w"

    Returns the candle list (oldest → newest) or None on any failure.
    """
    if not pair_address:
        return None
    # Map timeframe to API params
    tf_map: dict[str, dict[str, str | int]] = {
        "15m": {"timeframe": "15m", "limit": 200},
        "1h": {"timeframe": "1h", "limit": 150},
        "4h": {"timeframe": "4h", "limit": 120},
        "1d": {"timeframe": "1d", "limit": 100},
        "1w": {"timeframe": "1w", "limit": 80},
    }
    params: dict[str, str | int] = tf_map.get(timeframe, {"timeframe": "1h", "limit": 100})
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/rugcharts/ohlcv/{chain}/{pair_address}",
            params=params,
            headers=backend_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            body = r.json()
            data = body.get("data") or body
            candles = data.get("candles") or []
            if isinstance(candles, list) and candles:
                return candles
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("rugcharts ohlcv fetch failed: %s", e)
    return None


def _web_chart_url(chain: str, pair_address: str) -> str:
    if pair_address:
        return _DEXSCREENER_CHART_URL.format(chain=chain, pair_address=pair_address)
    return f"{WEBSITE_URL}/scanner"


async def cmd_chart(update, ctx) -> None:
    """Handle /chart command — chart image from RugCharts, text fallback."""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg is None:
        return
    args = ctx.args

    if not args:
        await msg.reply_text(
            "📊 <b>Usage:</b> <code>/chart &lt;token_address&gt; [chain]</code>\n\n"
            "Generates a chart image (OHLCV).\n\n"
            "Example: <code>/chart 0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "         <code>/chart So11111111111111111111111111111111111111112 solana</code>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    explicit_chain = args[1] if len(args) > 1 else ""
    timeframe = args[2] if len(args) > 2 else "1d"
    await msg.chat.send_action("upload_photo")

    client = get_shared_client(25)
    # ── Fetch DexScreener data ──
    try:
        r = await client.get(f"{DEXSCREENER}/tokens/{address}")
        if r.status_code != 200:
            await msg.reply_text(
                "❌ Could not fetch token data.",
                parse_mode="HTML",
            )
            return

        data = r.json()
        pairs = data.get("pairs", [])
        if not pairs:
            await msg.reply_text(
                "❌ No trading pairs found for this token.",
                parse_mode="HTML",
            )
            return
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error("DexScreener fetch failed: %s", e)
        await msg.reply_text(
            "❌ Could not fetch token data.\n\nPlease try again in a moment.",
            parse_mode="HTML",
        )
        return

    p = pairs[0]
    symbol = p.get("baseToken", {}).get("symbol", "???")
    name = p.get("baseToken", {}).get("name", "Unknown")
    price = float(p.get("priceUsd", 0))
    change_1h = float(p.get("priceChange", {}).get("h1", 0))
    change_6h = p.get("priceChange", {}).get("h6")  # may be None
    change_6h = float(change_6h) if change_6h is not None else None
    change_24h = float(p.get("priceChange", {}).get("h24", 0))
    change_7d = float(p.get("priceChange", {}).get("d7", 0))
    vol_24h = float(p.get("volume", {}).get("h24", 0))
    mcap = float(p.get("marketCap") or p.get("fdv") or 0)
    chain = explicit_chain or p.get("chainId", "")
    pair_address = p.get("pairAddress", "")

    # ── Fetch OHLCV for requested timeframe ──
    candles = await _fetch_rugcharts_ohlcv(client, chain, pair_address, timeframe)
    # Fallback to 1h if requested timeframe unavailable
    if not candles and timeframe != "1h":
        candles = await _fetch_rugcharts_ohlcv(client, chain, pair_address, "1h")
    # Use branded visual engine. Pillow rendering is CPU-bound — run it in
    # a worker thread so the bot event loop never stalls on image draw.
    from app.domains.telegram.rugmunchbot.services.visuals import render_candlestick_chart

    png = None
    if candles:
        png = await asyncio.to_thread(render_candlestick_chart, candles, name, symbol, chain, timeframe)
    if png:
        caption = (
            f"📊 <b>{name}</b> ({symbol})\n<code>{address}</code>\n\n💰 Price: <b>${price:.10f}</b>".rstrip("0").rstrip(
                "."
            )
            + (
                f"\n📈 MCap: {fmt_number(mcap)} | 📊 24h Vol: {fmt_number(vol_24h)}\n"
                f"🟢 1h: {fmt_pct(change_1h)} | 24h: {fmt_pct(change_24h)} | 7d: {fmt_pct(change_7d)}"
            )
            + f'\n📉 <a href="{_web_chart_url(chain, pair_address)}">Open chart</a>'
        )
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("1H", callback_data=f"chart_{address}_{chain}_1h"),
                    InlineKeyboardButton("4H", callback_data=f"chart_{address}_{chain}_4h"),
                    InlineKeyboardButton("1D", callback_data=f"chart_{address}_{chain}_1d"),
                    InlineKeyboardButton("1W", callback_data=f"chart_{address}_{chain}_1w"),
                ],
                [
                    InlineKeyboardButton("🗺️ RugMap", callback_data=f"rugmap_{address}_{chain}"),
                    InlineKeyboardButton("\U0001f52c Deep Scan (250\u2605)", callback_data=f"deep_{address}_{chain}"),
                ],
                [InlineKeyboardButton("🏠 Menu", callback_data="menu_main")],
            ]
        )
        try:
            await msg.reply_photo(
                photo=io.BytesIO(png),
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return
        except Exception as e:
            logger.warning("chart photo send failed, falling back to text: %s", e)

    # ── Fallback: sparkline text + web link ──
    sparkline = ""
    cg_prices = await _fetch_cg_price_history(client, name, symbol)
    if cg_prices:
        sparkline = _sparkline(cg_prices, width=8)
    else:
        sparkline = _pseudo_sparkline(price, change_1h, change_6h, change_24h, change_7d)

    def _arrow(val: float) -> str:
        if val > 0:
            return "🟢"
        if val < 0:
            return "🔴"
        return "⚪"

    dex_url = _web_chart_url(chain, pair_address)

    lines = [
        f"📊 <b>{name}</b> ({symbol})",
        f"<code>{address}</code>",
        "",
        sparkline,
        "",
        f"💰 <b>Price:</b> ${price:.10f}".rstrip("0").rstrip("."),
        f"📈 <b>Market Cap:</b> {fmt_number(mcap)}",
        f"📊 <b>24h Volume:</b> {fmt_number(vol_24h)}",
        "",
        "<b>Price Change</b>",
        f"  {_arrow(change_1h)} 1h:  <b>{fmt_pct(change_1h)}</b>",
        f"  {_arrow(change_24h)} 24h: <b>{fmt_pct(change_24h)}</b>",
        f"  {_arrow(change_7d)} 7d:  <b>{fmt_pct(change_7d)}</b>",
        "",
        f'📉 <a href="{dex_url}">View chart</a>',
        "",
        "<i>Chart rendering unavailable — showing text sparkline.</i>",
    ]

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>...truncated</i>"

    await msg.reply_text(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


__all__ = ["cmd_chart", "render_chart_png"]

"""
RMI Visual Engine — branded chart & map generation.
All outputs carry the Rug Munch Intelligence watermark.
"""

from __future__ import annotations

import io
import math

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi.visuals")

# ── Brand constants ──
BRAND_NAME = "Rug Munch Intelligence"
BRAND_URL = "rugmunch.io"
BRAND_PRIMARY = (88, 166, 255)
BRAND_ACCENT = (255, 193, 7)


def _add_branding(draw, w, h, font, small_font, symbol=""):
    """Add RMI watermark + branding footer."""
    brand_text = BRAND_NAME
    try:
        bbox = draw.textbbox((0, 0), brand_text, font=small_font)
        bw = bbox[2] - bbox[0]
    except (TypeError, OSError):
        bw = len(brand_text) * 8
    draw.text((w - bw - 16, 8), brand_text, font=small_font, fill=BRAND_PRIMARY)

    footer_y = h - 28
    draw.rectangle([0, footer_y, w, h], fill=(20, 22, 28))
    footer_text = f"@{BRAND_URL}  ·  {symbol}" if symbol else f"@{BRAND_URL}"
    draw.text((16, footer_y + 7), footer_text, font=small_font, fill=(120, 125, 140))


def render_candlestick_chart(
    candles,
    name,
    symbol,
    chain="",
    timeframe="1d",
    show_volume=True,
    show_ma=True,
    show_rsi=False,
):
    """Render a professional branded candlestick chart to PNG bytes."""
    if not candles or len(candles) < 3:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except (ImportError, OSError, RuntimeError):
        return None

    closes, highs, lows, vols = [], [], [], []
    for c in candles:
        try:
            closes.append(float(c.get("close", c.get("c", 0))))
            highs.append(float(c.get("high", c.get("h", 0))))
            lows.append(float(c.get("low", c.get("l", 0))))
            vols.append(float(c.get("volume", c.get("v", 0))))
        except (TypeError, ValueError):
            continue
    if not closes:
        return None

    w, h = 1200, 720
    bg = (13, 17, 23)
    grid_c = (28, 33, 42)
    text_c = (140, 148, 156)
    title_c = (232, 234, 237)
    up_c = (38, 198, 156)
    down_c = (239, 83, 80)

    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.load_default(size=22)
        small = ImageFont.load_default(size=14)
        tiny = ImageFont.load_default(size=12)
    except TypeError:
        font = ImageFont.load_default()
        small = font
        tiny = font

    pad_l, pad_r, pad_t, pad_b = 80, 24, 60, 40
    plot_w = w - pad_l - pad_r
    rsi_h = 80 if show_rsi else 0
    vol_h = int((h - pad_t - pad_b - rsi_h) * 0.2) if show_volume else 0
    price_h = h - pad_t - pad_b - vol_h - rsi_h - 20
    price_top = pad_t + 10
    price_bot = price_top + price_h
    vol_top = price_bot + 15
    vol_bot = vol_top + vol_h
    rsi_top = vol_bot + 10
    rsi_bot = rsi_top + rsi_h

    mn, mx = min(lows), max(highs)
    if mx == mn:
        mx = mn + 1.0
    n = len(closes)

    def x_of(i):
        return pad_l + int(i * plot_w / max(n - 1, 1))

    def y_of(v):
        return price_bot - int((v - mn) / (mx - mn) * price_h)

    # Grid
    for g in range(6):
        gy = price_top + int(price_h * g / 5)
        draw.line([(pad_l, gy), (w - pad_r, gy)], fill=grid_c, width=1)
        val = mx - (mx - mn) * g / 5
        label = f"${val:,.6f}" if val < 1 else f"${val:,.4f}" if val < 1000 else f"${val:,.0f}"
        draw.text((6, gy - 8), label, font=tiny, fill=text_c)

    # Moving averages
    if show_ma:

        def ma(data, period):
            result = []
            for i in range(len(data)):
                if i < period - 1:
                    result.append(None)
                else:
                    result.append(sum(data[i - period + 1 : i + 1]) / period)
            return result

        for period, color, label in [(7, (255, 183, 77), "MA7"), (25, (100, 181, 246), "MA25")]:
            ma_data = ma(closes, min(period, n))
            for i in range(1, n):
                if ma_data[i] is not None and ma_data[i - 1] is not None:
                    draw.line(
                        [(x_of(i - 1), y_of(ma_data[i - 1])), (x_of(i), y_of(ma_data[i]))],
                        fill=color,
                        width=2,
                    )

    # Candlesticks
    body_w = max(2, min(14, int(plot_w / n) - 2))
    color = up_c if closes[-1] >= closes[0] else down_c
    for i in range(n):
        cx = x_of(i)
        draw.line([(cx, y_of(highs[i])), (cx, y_of(lows[i]))], fill=color, width=2)
        draw.line(
            [(cx - body_w // 2, y_of(closes[i])), (cx + body_w // 2, y_of(closes[i]))],
            fill=color,
            width=4,
        )

    # Volume
    if show_volume and vol_h > 0:
        vmax = max(vols) if vols and max(vols) > 0 else 1
        for i in range(n):
            vh = int(vol_h * vols[i] / vmax)
            if vh <= 0:
                continue
            bx = x_of(i) - body_w // 2
            draw.rectangle((bx, vol_bot - vh, bx + body_w, vol_bot), fill=(*color[:3],))
        draw.text((w - pad_r - 60, vol_top - 18), "Volume", font=tiny, fill=text_c)

    # Title
    tf_label = timeframe.upper() if timeframe else ""
    title = f"{name} ({symbol})"
    if chain:
        title += f"  ·  {chain.capitalize()}"
    if tf_label:
        title += f"  ·  {tf_label}"
    draw.text((pad_l, 10), title, font=font, fill=title_c)

    # MA legend
    if show_ma:
        legend_x = pad_l + len(title) * 14 + 20
        for period, color, label in [(7, (255, 183, 77), "MA7"), (25, (100, 181, 246), "MA25")]:
            ma_data = ma(closes, min(period, n))
            if ma_data[-1] is not None:
                val_label = f"{label}: ${ma_data[-1]:.6f}" if ma_data[-1] < 1 else f"{label}: ${ma_data[-1]:.4f}"
                draw.text((legend_x, 14), val_label, font=tiny, fill=color)
                legend_x += len(val_label) * 9 + 15

    draw.text((pad_l, h - 35), "Price (USD)", font=tiny, fill=text_c)
    draw.rectangle((pad_l, price_top, w - pad_r, price_bot), outline=grid_c, width=1)
    _add_branding(draw, w, h, font, small, symbol)

    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf.getvalue()
    except (OSError, ValueError, RuntimeError):
        return None


def render_cluster_map(nodes, links, name, symbol, chain="", total_holders=0, top10_pct=0.0):
    """Render a branded wallet cluster map (RugMaps) to PNG bytes."""
    if not nodes:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except (ImportError, OSError, RuntimeError):
        return None

    w, h = 1000, 760
    bg = (13, 17, 23)
    grid_c = (28, 33, 42)
    text_c = (140, 148, 156)
    title_c = (232, 234, 237)

    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.load_default(size=22)
        small = ImageFont.load_default(size=14)
        tiny = ImageFont.load_default(size=12)
    except TypeError:
        font = ImageFont.load_default()
        small = font
        tiny = font

    def node_id(node):
        return node.get("address") or node.get("id") or ""

    def risk_color(node):
        risk = node.get("risk") or node.get("risk_score") or 0
        risk_level = str(node.get("risk_level") or "").lower()
        label = str(node.get("label") or "").lower()
        if any(k in label for k in ["binance", "coinbase", "kraken", "okx", "bybit"]):
            return (0, 150, 255)
        if any(k in label for k in ["raydium", "uniswap", "meteora", "orca", "jupiter", "pool"]):
            return (180, 100, 255)
        if risk_level == "high" or (isinstance(risk, (int, float)) and risk >= 60):
            return (239, 83, 80)
        if risk_level == "medium" or (isinstance(risk, (int, float)) and risk >= 30):
            return (255, 183, 77)
        if risk_level == "low":
            return (102, 187, 106)
        return (66, 165, 245)

    # Layout
    positions = {}
    for node in nodes:
        try:
            x = float(node.get("x") or node.get("fx") or 0)
            y = float(node.get("y") or node.get("fy") or 0)
            if x or y:
                positions[node_id(node)] = (x, y)
        except (TypeError, ValueError):
            continue

    if not positions:
        n_count = len(nodes)
        for i, node in enumerate(nodes):
            angle = 2 * math.pi * i / max(n_count, 1)
            positions[node_id(node)] = (math.cos(angle), math.sin(angle))

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    xmin, xmax = (min(xs), max(xs)) if xs else (0, 1)
    ymin, ymax = (min(ys), max(ys)) if ys else (0, 1)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    margin = 100

    def px(p):
        sx = margin + (p[0] - xmin) / (xmax - xmin) * (w - 2 * margin)
        sy = h - margin - 40 - (p[1] - ymin) / (ymax - ymin) * (h - 2 * margin - 60)
        return int(sx), int(sy)

    # Links with arrows
    drawn = set()
    for lnk in links:
        src = lnk.get("source")
        tgt = lnk.get("target")
        if isinstance(src, dict):
            src = node_id(src)
        if isinstance(tgt, dict):
            tgt = node_id(tgt)
        if src not in positions or tgt not in positions:
            continue
        key = tuple(sorted((str(src), str(tgt))))
        if key in drawn:
            continue
        drawn.add(key)
        src_pos, tgt_pos = px(positions[src]), px(positions[tgt])
        draw.line([src_pos, tgt_pos], fill=(60, 65, 80), width=2)
        dx = tgt_pos[0] - src_pos[0]
        dy = tgt_pos[1] - src_pos[1]
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 25:
            ux, uy = dx / dist, dy / dist
            arr_len, arr_w = 12, 6
            tip = tgt_pos
            left = (tip[0] - arr_len * ux + arr_w * uy, tip[1] - arr_len * uy - arr_w * ux)
            right = (tip[0] - arr_len * ux - arr_w * uy, tip[1] - arr_len * uy + arr_w * ux)
            draw.polygon([tip, left, right], fill=(100, 105, 120))

    # Bubbles
    center = nodes[0]
    for node in nodes:
        pos = positions.get(node_id(node))
        if pos is None:
            continue
        try:
            weight = float(node.get("weight") or node.get("value") or 0)
        except (TypeError, ValueError):
            weight = 0
        is_center = node is center
        radius: float = 32.0 if is_center else max(10, min(26, 12 + weight * 0.4))
        cx, cy = px(pos)
        col = risk_color(node)
        if is_center:
            for r in range(int(radius) + 8, int(radius) + 2, -3):
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*BRAND_ACCENT, 20))
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=col, outline=(220, 225, 230), width=2)
        label = str(node.get("label") or "")[:12] or node_id(node)[:10]
        try:
            bbox = draw.textbbox((0, 0), label, font=small)
            lw = bbox[2] - bbox[0]
        except (TypeError, OSError):
            lw = len(label) * 8
        draw.text((cx - lw / 2, cy - 8), label, font=small, fill=title_c)

    # Title
    title = f"RugMaps: {name} ({symbol})"
    if chain:
        title += f"  ·  {chain.capitalize()}"
    draw.text((16, 10), title, font=font, fill=title_c)

    # Stats
    stats_y = 42
    if total_holders or top10_pct:
        stats_text = f"Holders: {total_holders:,}" if total_holders else ""
        if top10_pct:
            stats_text += f"  ·  Top 10: {top10_pct:.1f}%"
        draw.text((16, stats_y), stats_text, font=small, fill=text_c)

    # Legend
    legend_y = h - 68
    draw.rectangle((0, legend_y - 4, w, h), fill=(20, 22, 28))
    legend_items = [
        ((239, 83, 80), "High Risk"),
        ((255, 183, 77), "Medium"),
        ((102, 187, 106), "Low Risk"),
        ((0, 150, 255), "Exchange"),
        ((180, 100, 255), "DEX/LP"),
        ((66, 165, 245), "Unknown"),
    ]
    lx = 16
    for col, label in legend_items:
        draw.ellipse([lx, legend_y, lx + 14, legend_y + 14], fill=col)
        draw.text((lx + 18, legend_y), label, font=small, fill=text_c)
        lx += len(label) * 9 + 40
    draw.text((16, legend_y + 20), "Bubble size = wallet weight  ·  Arrow = fund flow", font=tiny, fill=text_c)
    _add_branding(draw, w, h, font, small, symbol)

    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf.getvalue()
    except (OSError, ValueError, RuntimeError):
        return None

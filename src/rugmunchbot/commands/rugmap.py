"""
🕸️ RugMap - Bubble Map Visualization
==========================================
Visualize wallet connections and fund flow clusters for a token.
Fetches the graph from the backend RugMaps engine (/api/v1/rugmaps/
token-graph) and renders a bubble-map IMAGE. Falls back to the ASCII text
map + web link when image generation is unavailable.

Usage: /rugmap <address> [chain]
"""

from __future__ import annotations

import asyncio
import io
from typing import Any, cast

import httpx

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, DEXSCREENER, WEBSITE_URL, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi.rugmap")


# ══════════════════════════════════════════════
# v6 — BUBBLE MAP IMAGE RENDERING (Pillow, in bot venv)
# ══════════════════════════════════════════════


def render_bubble_map_png(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    name: str,
    symbol: str,
    token_address: str,
) -> bytes | None:
    """Render a bubble-map PNG of wallet clusters (v6).

    Nodes become bubbles (radius from node weight/value; color by risk),
    links become connecting lines. Uses Pillow (already in the bot venv).
    Returns ``None`` when Pillow is unavailable so callers fall back to the
    text map + web link.
    """
    if not nodes:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except (ImportError, OSError, RuntimeError) as exc:
        logger.debug("Pillow unavailable, skipping rugmap image: %s", exc)
        return None

    w, h = 900, 700
    bg = (15, 17, 21)
    grid_c = (35, 38, 46)
    text_c = (154, 160, 166)
    title_c = (232, 234, 237)

    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=20)
        small = ImageFont.load_default(size=12)
    except TypeError:
        font = ImageFont.load_default()
        small = font

    def node_id(node: dict[str, Any]) -> str:
        return node.get("address") or node.get("id") or ""

    def risk_color(node: dict[str, Any]) -> tuple[int, int, int]:
        risk = node.get("risk") or node.get("risk_score") or 0
        risk_level = str(node.get("risk_level") or "").lower()
        if risk_level == "high" or (isinstance(risk, (int, float)) and risk >= 60):
            return (239, 83, 80)
        if risk_level == "medium" or (isinstance(risk, (int, float)) and risk >= 30):
            return (255, 183, 77)
        if risk_level == "low":
            return (102, 187, 106)
        return (66, 165, 245)

    # Layout: prefer graph positions, else circle layout.
    positions: dict[str, tuple[float, float]] = {}
    for node in nodes:
        try:
            x = float(node.get("x") or node.get("fx") or 0)
            y = float(node.get("y") or node.get("fy") or 0)
            if x or y:
                positions[node_id(node)] = (x, y)
        except (TypeError, ValueError):
            continue

    if not positions:
        import math

        n_count = len(nodes)
        for i, node in enumerate(nodes):
            angle = 2 * math.pi * i / max(n_count, 1)
            positions[node_id(node)] = (math.cos(angle), math.sin(angle))

    # Normalize to plot area with margins.
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    xmin, xmax = (min(xs), max(xs)) if xs else (0, 1)
    ymin, ymax = (min(ys), max(ys)) if ys else (0, 1)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    margin = 90

    def px(p: tuple[float, float]) -> tuple[int, int]:
        sx = margin + (p[0] - xmin) / (xmax - xmin) * (w - 2 * margin)
        sy = h - margin - (p[1] - ymin) / (ymax - ymin) * (h - 2 * margin)
        return int(sx), int(sy)

    # Links first (under bubbles) — with fund flow direction arrows.
    import math

    drawn: set[tuple[str, str]] = set()
    for lnk in links:
        src = lnk.get("source")
        tgt = lnk.get("target")
        if isinstance(src, dict):
            src = node_id(src)
        if isinstance(tgt, dict):
            tgt = node_id(tgt)
        if src not in positions or tgt not in positions:
            continue
        key = cast("tuple[str, str]", tuple(sorted((str(src), str(tgt)))))
        if key in drawn:
            continue
        drawn.add(key)
        src_pos, tgt_pos = px(positions[src]), px(positions[tgt])
        draw.line([src_pos, tgt_pos], fill=(80, 85, 100), width=2)
        # Arrow head for fund flow direction
        dx = tgt_pos[0] - src_pos[0]
        dy = tgt_pos[1] - src_pos[1]
        dist = math.sqrt(dx * dx + dy * dy)
        if dist > 20:
            ux, uy = dx / dist, dy / dist
            arr_len, arr_w = 10, 5
            tip = tgt_pos
            left = (tip[0] - arr_len * ux + arr_w * uy, tip[1] - arr_len * uy - arr_w * ux)
            right = (tip[0] - arr_len * ux - arr_w * uy, tip[1] - arr_len * uy + arr_w * ux)
            draw.polygon([tip, left, right], fill=(120, 125, 140))

    # Bubbles.
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
        radius = 28 if is_center else max(8, min(22, 10 + weight * 0.5))
        cx, cy = px(pos)
        col = risk_color(node)
        # Glow for center token
        if is_center:
            for r in range(int(radius) + 6, int(radius) + 2, -2):
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 200, 0, 30))
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=col, outline=(232, 234, 237), width=2)
        label = str(node.get("label") or "")[:10] or node_id(node)[:8]
        bbox = draw.textbbox((0, 0), label, font=small)
        lw = bbox[2] - bbox[0]
        draw.text((cx - lw / 2, cy - 7), label, font=small, fill=title_c)

    # Legend
    draw.text((16, 12), f"RugMap: {name} ({symbol})", font=font, fill=title_c)
    legend_y = h - 50
    draw.ellipse([16, legend_y, 30, legend_y + 12], fill=(239, 83, 80))
    draw.text((34, legend_y), "High Risk", font=small, fill=text_c)
    draw.ellipse([120, legend_y, 134, legend_y + 12], fill=(255, 183, 77))
    draw.text((138, legend_y), "Medium", font=small, fill=text_c)
    draw.ellipse([210, legend_y, 224, legend_y + 12], fill=(102, 187, 106))
    draw.text((228, legend_y), "Low Risk", font=small, fill=text_c)
    draw.ellipse([310, legend_y, 324, legend_y + 12], fill=(66, 165, 245))
    draw.text((328, legend_y), "Unknown", font=small, fill=text_c)
    draw.text(
        (16, legend_y + 18), "Bubble size = wallet weight  ·  Arrow = fund flow direction", font=small, fill=text_c
    )

    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()
    except (OSError, ValueError, RuntimeError):
        logger.debug("rugmap png render failed", exc_info=True)
        return None


async def _fetch_token_graph(client: httpx.AsyncClient, address: str, chain: str) -> dict[str, Any] | None:
    """Fetch the token holder graph from the backend RugMaps engine."""
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
            params={"token_address": address, "chain": chain, "limit": 40},
            headers=backend_headers(),
            timeout=20,
        )
        if r.status_code == 200:
            body = r.json()
            if isinstance(body, dict) and (body.get("nodes") or body.get("links")):
                return body
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("RugMaps token-graph failed: %s", exc)
    return None


def _web_rugmap_url(address: str, chain: str) -> str:
    return f"{WEBSITE_URL}/rugmaps?token={address}&chain={chain}"


async def cmd_rugmap(update, ctx) -> None:
    """Handle /rugmap command - bubble map image of wallet clusters."""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg is None:
        return
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🕸️ <b>Usage:</b> <code>/rugmap &lt;token_address&gt; [chain]</code>\n\n"
            "Generates a bubble-map image of wallet clusters and fund flows.\n\n"
            "Example: <code>/rugmap 0x...</code>\n\n"
            "<i>Shows dev wallet, bundler wallets, exchange deposits, and more.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    explicit_chain = args[1] if len(args) > 1 else ""
    await msg.chat.send_action("upload_photo")

    try:
        smart_money = []
        top_holders = []
        bundle_detected = False
        fresh_wallet_pct = 0
        liquidity = 0.0
        symbol = "???"
        name = "Unknown"
        chain = explicit_chain
        graph: dict[str, Any] | None = None

        client = get_shared_client(25)
        # v6: fetch the token graph from the RugMaps engine first.
        graph_chain = chain or _guess_chain(address)
        graph = await _fetch_token_graph(client, address, graph_chain)
        if graph:
            nodes = graph.get("nodes") or []
            links = graph.get("links") or []
            chain = graph.get("chain") or graph_chain
        else:
            nodes, links = [], []

        # Try DataBus
        try:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/databus/token/{address}",
                headers=backend_headers(),
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                bundle_detected = data.get("bundle_detected", False)
                fresh_wallet_pct = data.get("fresh_wallet_pct", 0)
                smart_money = data.get("smart_money", [])
                top_holders = data.get("top_holders", [])
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.debug("RMI backend call failed: %s", exc)

        # DexScreener for basic info
        try:
            r = await client.get(f"{DEXSCREENER}/tokens/{address}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    symbol = p.get("baseToken", {}).get("symbol", "???")
                    name = p.get("baseToken", {}).get("name", "Unknown")
                    liquidity = float(p.get("liquidity", {}).get("usd", 0))
                    chain = chain or p.get("chainId", "")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.debug("DexScreener fallback failed: %s", exc)

        # ── Use branded visual engine. Pillow rendering is CPU-bound — run it
        # in a worker thread so the bot event loop never stalls on image draw.
        from app.domains.telegram.rugmunchbot.services.visuals import render_cluster_map

        # Holder stats for the map footer (top_holders entries carry pct).
        total_holders = len(top_holders)
        top10_pct = sum(float(h.get("pct") or 0) for h in top_holders[:10])

        png = None
        if nodes:
            png = await asyncio.to_thread(
                render_cluster_map,
                nodes or [],
                links or [],
                name,
                symbol,
                chain,
                total_holders,
                top10_pct,
            )
        if png:
            risk_line = ""
            if graph and graph.get("risk_level"):
                risk_line = f"\n🛡️ Graph Risk: <b>{graph['risk_level'].upper()}</b> ({graph.get('risk_score', 0)})"
            caption = (
                f"🕸️ <b>RugMap: {name} ({symbol})</b>\n"
                f"<code>{address}</code>{risk_line}\n\n"
                f"👥 Nodes: <b>{len(nodes)}</b> | 🔗 Links: <b>{len(links)}</b>"
                f" | 💧 Liq: ${liquidity:,.0f}"
                + ("\n🚨 <b>BUNDLE DETECTED</b>" if bundle_detected else "")
                + f'\n🌐 <a href="{_web_rugmap_url(address, chain)}">Open interactive map</a>'
            )
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("📊 View Chart", callback_data=f"chart_{address}_{chain}"),
                        InlineKeyboardButton("🔍 Deep Scan", callback_data=f"deep_{address}_{chain}"),
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
                logger.warning("rugmap photo send failed, falling back to text: %s", e)

        # ── Fallback: ASCII cluster map + web link ──
        lines = [
            f"🕸️ <b>RugMap: {name} ({symbol})</b>",
            "",
            "<b>Cluster Analysis</b>",
        ]

        if bundle_detected:
            lines.append("  🚨 <b>BUNDLE DETECTED</b> - Multiple funded wallets")
        else:
            lines.append("  ✅ No bundle detected")

        if fresh_wallet_pct:
            if fresh_wallet_pct > 50:
                lines.append(f"  🆕 <b>Fresh Wallets:</b> {fresh_wallet_pct:.0f}% ⚠️")
            else:
                lines.append(f"  🆕 Fresh Wallets: {fresh_wallet_pct:.0f}%")

        lines.append(f"  💧 Liquidity: ${liquidity:,.0f}")

        if top_holders:
            lines.extend(["", "👥 <b>Top Holders Cluster</b>"])
            for i, h in enumerate(top_holders[:5], 1):
                addr = h.get("address", "")
                pct = h.get("pct", 0)
                is_contract = h.get("is_contract", False)
                tag = "📄" if is_contract else "👛"
                lines.append(f"  {i}. {tag} <code>{addr}</code> - {pct:.2f}%")

        if smart_money:
            lines.extend(["", "💡 <b>Smart Money Cluster</b>"])
            for i, w in enumerate(smart_money[:3], 1):
                addr = w.get("address", "")
                pnl = w.get("pnl_usd", 0)
                lines.append(f"  {i}. <code>{addr}</code> - ${pnl:,.0f}")

        # Build simple ASCII cluster map
        lines.extend(["", "<b>Connection Map</b>"])
        if top_holders:
            lines.append("  💰 Funding → 🏦 Exchange")
            lines.append("  👛 Dev Wallet → 🗂️ Distribution Wallets")
            lines.append(f"  📊 Top {min(len(top_holders), 5)} Holders → 🔗 {len(top_holders)} connections")
        else:
            lines.append("  No cluster data available")

        lines.extend(
            [
                "",
                f'🌐 <a href="{_web_rugmap_url(address, chain)}">Open interactive map on rugmunch.io</a>',
                "",
                "<i>Image map unavailable — showing text map.</i>",
            ]
        )

        text = "\n".join(lines)
        await msg.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"RugMap failed: {e}")
        await msg.reply_text("❌ Could not generate rug map.\n\nPlease try again in a moment.", parse_mode="HTML")


def _guess_chain(address: str) -> str:
    from app.domains.telegram.rugmunchbot.formatters import detect_chain

    return detect_chain(address) or "solana"


__all__ = ["cmd_rugmap", "render_bubble_map_png"]

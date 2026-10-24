"""🕸️ RugMap — token cluster map as a PNG plus a cluster summary.

Renders wallet clusters for one token from the RugMaps engine
(``/api/v1/rugmaps/token-graph``) using the SINGLE map engine
(``services.visuals.render_cluster_map``) and captions it with the numbers
that matter: top clusters, bundle confidence, fresh wallets and the AI /
data-engine opinion — each with a stated reason when the backend has none.

Per-chat limits: every registered command runs behind the bot-wide flood gate
(``bot/_commands.py``: 3/s + 30/min per user, owners exempt), so no extra
limiter is added here.

Usage: /rugmap <token_address> [chain]
"""

from __future__ import annotations

import asyncio
import io
import re
from typing import Any
from urllib.parse import quote

import httpx

from rugmunchbot.commands._rugmap_caption import build_caption
from rugmunchbot.commands._rugmap_summary import build_cluster_summary
from rugmunchbot.config import BACKEND_URL, DEXSCREENER, WEBSITE_URL, backend_headers
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

logger = get_logger("rmi.rugmap")

#: EVM hex (0x + 40) and Solana base58 both match; anything else is rejected
#: before we spend a backend call on it.
_ADDRESS_RE = re.compile(r"^(?:0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$")

_USAGE = (
    "🕸️ <b>Usage:</b> <code>/rugmap &lt;token_address&gt; [chain]</code>\n\n"
    "Generates a bubble-map image of wallet clusters and fund flows.\n\n"
    "Example: <code>/rugmap EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>"
)


async def _fetch_token_graph(
    client: httpx.AsyncClient, address: str, chain: str
) -> dict[str, Any] | None:
    """Fetch the token holder graph from the backend RugMaps engine."""
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
            params={"token_address": address, "chain": chain, "limit": 40},
            headers=backend_headers(),
            timeout=25,
        )
        if r.status_code == 200:
            body = r.json()
            if isinstance(body, dict) and (body.get("nodes") or body.get("links")):
                return body
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
    ) as exc:
        logger.debug("RugMaps token-graph failed: %s", exc)
    return None


async def _fetch_context(client: httpx.AsyncClient, address: str) -> dict[str, Any]:
    """Fetch DataBus token metrics + DexScreener identity; never raises."""
    ctx: dict[str, Any] = {
        "databus": {},
        "name": "Unknown",
        "symbol": "???",
        "liquidity": 0.0,
        "dex_chain": "",
        "holders": [],
    }
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/databus/token/{address}", headers=backend_headers(), timeout=12
        )
        if r.status_code == 200:
            body = r.json()
            if isinstance(body, dict):
                ctx["databus"] = body
                ctx["holders"] = body.get("top_holders") or []
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
    ) as exc:
        logger.debug("RMI backend call failed: %s", exc)

    try:
        r = await client.get(f"{DEXSCREENER}/tokens/{address}", timeout=10)
        pairs = r.json().get("pairs") or [] if r.status_code == 200 else []
        if pairs:
            pair = pairs[0]
            ctx["symbol"] = pair.get("baseToken", {}).get("symbol") or "???"
            ctx["name"] = pair.get("baseToken", {}).get("name") or "Unknown"
            ctx["liquidity"] = float(pair.get("liquidity", {}).get("usd") or 0)
            ctx["dex_chain"] = pair.get("chainId") or ""
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        ValueError,
        httpx.HTTPError,
        httpx.TimeoutException,
    ) as exc:
        logger.debug("DexScreener fallback failed: %s", exc)
    return ctx


def _web_rugmap_url(address: str, chain: str) -> str:
    return f"{WEBSITE_URL}/rugmaps?token={address}&chain={chain}"


def _share_url(url: str) -> str:
    """t.me/share/url deep link — the share button target."""
    return (
        "https://t.me/share/url?url="
        + quote(url)
        + "&text="
        + quote("RugMap by Rug Munch Intelligence")
    )


def _keyboard(address: str, chain: str):
    """Inline keyboard: chart, deep scan, share, menu."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Chart", callback_data=f"chart_{address}_{chain}"),
                InlineKeyboardButton("🔍 Deep Scan", callback_data=f"deep_{address}_{chain}"),
            ],
            [
                InlineKeyboardButton("📣 Share", url=_share_url(_web_rugmap_url(address, chain))),
                InlineKeyboardButton("🏠 Menu", callback_data="menu_main"),
            ],
        ]
    )


async def _render(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    ctx: dict[str, Any],
    chain: str,
    summary: dict[str, Any],
) -> bytes | None:
    """Render the map and stamp token/chain/time/risk/AI onto the PNG."""
    from rugmunchbot.services.visuals import render_cluster_map
    from rugmunchbot.services.visuals_map_meta import annotate_map_png

    holders = ctx["holders"]
    top10 = sum(float(h.get("pct") or 0) for h in holders[:10])
    png = await asyncio.to_thread(
        render_cluster_map,
        nodes,
        links,
        ctx["name"],
        ctx["symbol"],
        chain,
        len(holders),
        top10,
    )
    if not png:
        return None
    ai = summary.get("ai") or {}
    meta = {
        "token": summary.get("address", ""),
        "chain": chain,
        "timestamp": summary.get("timestamp", ""),
        "risk": summary.get("risk_label", ""),
        "ai": (ai.get("verdict") or "") + (f" — {ai['summary']}" if ai.get("summary") else ""),
    }
    return await asyncio.to_thread(annotate_map_png, png, meta) or png


async def cmd_rugmap(update, ctx) -> None:
    """Handle /rugmap — cluster map PNG + cluster summary caption."""
    msg = update.message or (update.callback_query.message if update.callback_query else None)
    if msg is None:
        return
    args = ctx.args or []
    if not args:
        await msg.reply_text(_USAGE, parse_mode="HTML", disable_web_page_preview=True)
        return

    address = args[0].strip()
    if not _ADDRESS_RE.match(address):
        await msg.reply_text(
            "❌ <b>Invalid token address.</b>\n\n" + _USAGE,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return

    explicit_chain = args[1].lower() if len(args) > 1 else ""
    await msg.chat.send_action("upload_photo")

    try:
        client = get_shared_client(25)
        graph_chain = explicit_chain or _guess_chain(address)
        graph = await _fetch_token_graph(client, address, graph_chain)
        ctx_data = await _fetch_context(client, address)

        nodes = (graph or {}).get("nodes") or []
        links = (graph or {}).get("links") or []
        chain = (graph or {}).get("chain") or ctx_data["dex_chain"] or graph_chain
        analysis = (graph or {}).get("analysis") or {}
        risk_level = str((graph or {}).get("risk_level") or analysis.get("verdict") or "").lower()
        risk_score = (graph or {}).get("risk_score") or analysis.get("risk_score") or 0

        summary = build_cluster_summary(
            nodes,
            links,
            analysis,
            ctx_data["databus"],
            graph_source=(graph or {}).get("source") or ("rugmaps/token-graph" if graph else None),
        )
        summary["address"] = address
        summary["timestamp"] = _utc_stamp()
        summary["risk_label"] = f"{risk_level.upper()} {risk_score}".strip() if risk_level else ""
        if not graph:
            summary["source"] = "n/a — RugMaps token-graph returned no graph"

        caption = build_caption(
            address,
            chain,
            ctx_data["name"],
            ctx_data["symbol"],
            ctx_data["liquidity"],
            summary,
            _web_rugmap_url(address, chain),
        )
        kb = _keyboard(address, chain)

        png = await _render(nodes, links, ctx_data, chain, summary) if nodes else None
        if png:
            try:
                await msg.reply_photo(
                    photo=io.BytesIO(png), caption=caption, parse_mode="HTML", reply_markup=kb
                )
                return
            except Exception as exc:  # Telegram can reject a caption/photo we cannot predict
                logger.warning("rugmap photo send failed, sending text: %s", exc)

        await msg.reply_text(
            caption, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True
        )

    except (
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
    ) as exc:
        logger.error("RugMap failed: %s", exc)
        await msg.reply_text(
            "❌ Could not generate rug map.\n\nPlease try again in a moment.", parse_mode="HTML"
        )


def _utc_stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _guess_chain(address: str) -> str:
    from rugmunchbot.formatters import detect_chain

    return detect_chain(address) or "solana"


__all__ = ["cmd_rugmap"]

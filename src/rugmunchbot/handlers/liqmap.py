"""/liqmap — cross-exchange liquidation pressure map (Hyperliquid, Bybit,
Binance, OKX, Bitget, Gate.io, MEXC, KuCoin where reachable).

Renders the aggregated venue-OI heatmap: top assets by cascade pressure,
per-venue breakdown for the queried asset, 10x liquidation levels.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

if TYPE_CHECKING:
    from telegram import Update

from rugmunchbot.config._endpoints import backend_headers
from rugmunchbot.tools._client import http_get_json

_ENDPOINT = "/api/v1/market/liquidations-multi-venue"


def _e(s: Any) -> str:
    return html.escape(str(s), quote=False)


def _usd(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.2f}M"
    return f"${n:,.0f}"


async def cmd_liqmap(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/liqmap [asset] — top liquidation-pressure assets or one asset deep-dive."""
    if not update.message:
        return
    arg = (ctx.args or [""])[0].strip().upper() if ctx.args else ""
    wait = await update.message.reply_text("🗺 Building cross-exchange liquidation map...")
    try:
        body = await http_get_json(
            _ENDPOINT, params={"limit": 40}, headers=backend_headers(), timeout=20
        )
    except Exception as exc:
        await wait.edit_text(f"❌ Liquidation map unavailable: {_e(str(exc)[:120])}")
        return

    rows = (body or {}).get("heatmap") or []
    if not rows:
        await wait.edit_text("❌ No liquidation data returned.")
        return

    total_oi = body.get("total_open_interest_usd") or 0
    venues = ", ".join(body.get("venues_online") or [])
    lines = [
        "<b>━━━ LIQUIDATION MAP ━━━</b>",
        f"<i>{len(rows)} assets · {_usd(total_oi)} aggregate OI · venues: {_e(venues)}</i>",
        "",
    ]

    if arg:
        row = next((r for r in rows if r.get("asset") == arg), None)
        if row is None:
            await wait.edit_text(f"❌ {_e(arg)} not in the top-pressure set. Try /liqmap for the leaderboard.")
            return
        lines.append(f"<b>{_e(row['asset'])}</b> — OI {_usd(row.get('total_oi_usd'))} ({row.get('oi_share_pct')}% of tracked)")
        lines.append(f"Weighted mark: <b>{row.get('weighted_mark')}</b>")
        lines.append(f"10x liq levels: longs <b>{row.get('long_liq_10x')}</b> · shorts <b>{row.get('short_liq_10x')}</b>")
        spread = row.get("funding_spread")
        if spread is not None:
            lines.append(f"Funding spread across venues: {spread}")
        lines.append("")
        lines.append("<b>Per-venue OI:</b>")
        for venue, d in sorted((row.get("venues") or {}).items(), key=lambda kv: -(kv[1].get("oi_usd") or 0)):
            lines.append(f"  · {_e(venue)}: {_usd(d.get('oi_usd'))} (funding {d.get('funding')})")
    else:
        lines.append("<b>Top cascade pressure:</b>")
        for r in rows[:8]:
            lines.append(
                f"  · <b>{_e(r['asset'])}</b> — OI {_usd(r.get('total_oi_usd'))} "
                f"({r.get('oi_share_pct')}%) · {r.get('venue_count')} venues"
            )
        lines.append("")
        lines.append("Deep-dive one asset: <code>/liqmap BTC</code>")

    lines.append("")
    lines.append("via @rugmunchbot · rugmunch.io")
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_liqmap(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(CommandHandler("liqmap", cmd_liqmap))

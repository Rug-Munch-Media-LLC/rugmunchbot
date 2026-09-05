"""/hlwhales — Hyperliquid whale action: biggest movers, funding squeezes,
and where forced unwinds are loading up. Real exchange data via the
backend market-intel endpoint.
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

_ENDPOINT = "/api/v1/market/hyperliquid-action"


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


async def cmd_hlwhales(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/hlwhales — where the big money is pushing on Hyperliquid."""
    if not update.message:
        return
    wait = await update.message.reply_text("🐋 Scanning Hyperliquid whale flow...")
    try:
        body = await http_get_json(
            _ENDPOINT, params={"limit": 5}, headers=backend_headers(), timeout=20
        )
    except Exception as exc:
        await wait.edit_text(f"❌ Hyperliquid feed unavailable: {_e(str(exc)[:120])}")
        return

    gainers = (body or {}).get("top_gainers") or []
    losers = (body or {}).get("top_losers") or []
    long_sq = (body or {}).get("long_squeeze_highest_funding") or []
    short_sq = (body or {}).get("short_squeeze_lowest_funding") or []

    def _row(m: dict[str, Any]) -> str:
        sym = _e(m.get("asset") or m.get("symbol") or "?")
        chg = float(m.get("change_24h") or 0)
        vol = _usd(m.get("volume_24h"))
        oi = _usd(m.get("open_interest_usd"))
        arrow = "🟢" if chg >= 0 else "🔴"
        return f"  {arrow} <b>{sym}</b> {chg:+.1f}% · vol {vol} · OI {oi}"

    lines = ["<b>━━━ HYPERLIQUID WHALES ━━━</b>", ""]

    if gainers:
        lines.append("<b>🐋 Whales pumping:</b>")
        lines.extend(_row(m) for m in gainers[:4])
        lines.append("")
    if losers:
        lines.append("<b>🩸 Whales dumping:</b>")
        lines.extend(_row(m) for m in losers[:4])
        lines.append("")
    if long_sq:
        top = long_sq[0]
        fr = float(top.get("funding_rate") or 0) * 100
        lines.append(
            f"<b>⚡ Long squeeze watch:</b> {_e(top.get('asset'))} paying "
            f"{fr:.3f}%/h funding — crowded longs are the fuel."
        )
    if short_sq:
        top = short_sq[0]
        fr = float(top.get("funding_rate") or 0) * 100
        lines.append(
            f"<b>⚡ Short squeeze watch:</b> {_e(top.get('asset'))} at "
            f"{fr:.3f}%/h — shorts are paying to stay in; squeeze fuel loaded."
        )

    lines.append("")
    lines.append("via @rugmunchbot · rugmunch.io")
    await wait.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def register_hlwhales(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(CommandHandler("hlwhales", cmd_hlwhales))

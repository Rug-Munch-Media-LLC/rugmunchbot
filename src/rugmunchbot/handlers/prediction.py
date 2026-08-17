#!/usr/bin/env python3
"""
RugMunchBot -- Prediction market command handlers.

Commands: /polymarket, /election, /odds — backed by the backend
prediction-market router (app/routers/prediction_market_router.py,
/api/v1/prediction-markets/*).

Sources: Polymarket, Kalshi, Limitless, Manifold (real-money + play-money
prediction markets). The backend caches every endpoint (30s-1h TTL) — the
bot does NOT add its own cache layer.

Routes used:
  GET /api/v1/prediction-markets/trending  - top markets by volume (category filter)
  GET /api/v1/prediction-markets/search    - search all 4 sources (q=<topic>)
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers, fmt_number
from app.domains.telegram.rugmunchbot.formatters import footer_links, sep
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot")

# Source badges (in order of reliability: real-money first).
SOURCE_EMOJI = {
    "polymarket": "\U0001f52e",  # 🔮
    "kalshi": "\U0001f3db\ufe0f",  # 🏛️
    "limitless": "\u26a1",  # ⚡
    "manifold": "\U0001f3af",  # 🎯
}

PREDICTION_UNAVAILABLE = (
    "\u26a0\ufe0f <b>Prediction Markets Unavailable</b>\n\n"
    "Could not fetch prediction market data right now.\n"
    "Please try again in a moment."
)

# Category filter accepted by the backend /trending endpoint.
POLYMARKET_CATEGORIES = ("all", "crypto", "security", "regulation")

MARKET_LIMIT = 8


def _end_countdown(ends_at: str | None) -> str:
    """Human countdown to market end (e.g. "3d 5h left", "2mo 12d left").

    Returns "" when the date is missing or unparseable. ISO strings with a
    trailing 'Z' (Polymarket style) are normalized to +00:00 first.
    """
    if not ends_at:
        return ""
    try:
        clean = ends_at.replace("Z", "+00:00")
        end = datetime.fromisoformat(clean)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        diff = end - datetime.now(UTC)
    except (TypeError, ValueError):
        return ""
    if diff.total_seconds() <= 0:
        return "ended"
    days = diff.days
    hours = int(diff.seconds // 3600)
    minutes = int(diff.seconds % 3600 // 60)
    if days >= 365:
        return f"{days // 365}y {days % 365 // 30}mo left"
    if days >= 30:
        return f"{days // 30}mo {days % 30}d left"
    if days >= 1:
        return f"{days}d {hours}h left"
    if hours >= 1:
        return f"{hours}h {minutes}m left"
    return f"{minutes}m left"


def _prob_bar(yes_pct: float, length: int = 10) -> str:
    """Colored YES-probability bar: 🟢/🟡/🔴 status + filled blocks."""
    pct = max(0.0, min(100.0, float(yes_pct)))
    filled = max(0, min(length, round((pct / 100) * length)))
    status = "\U0001f7e2" if pct >= 60 else "\U0001f7e1" if pct >= 40 else "\U0001f534"
    bar = "\u2588" * filled + "\u2591" * (length - filled)
    return f"{status}{bar} {pct:.0f}%"


def _market_line(i: int, m: dict[str, Any]) -> str:
    """Render one prediction market as an HTML block."""
    raw_q = (m.get("question") or "Untitled market").strip()
    question = escape(raw_q[:90] + ("..." if len(raw_q) > 90 else ""))
    prob = m.get("probability") or {}
    try:
        yes_pct = float(prob.get("yes") or 0) * 100
    except (TypeError, ValueError):
        yes_pct = 0.0
    vol = m.get("volume_usd")
    vol_txt = fmt_number(vol) if vol is not None else "N/A"
    source_emoji = SOURCE_EMOJI.get((m.get("source") or "").lower(), "\U0001f52e")
    end_txt = _end_countdown(m.get("ends_at"))
    end_str = f" | \U0001f552 {end_txt}" if end_txt else ""
    badge = ""
    if m.get("is_crypto_relevant"):
        badge += " \U0001f4b0"
    if m.get("is_security_relevant"):
        badge += " \U0001f6a8"
    return f"{i}. {source_emoji} <b>{question}</b>{badge}\n   {_prob_bar(yes_pct)} YES | Vol: <b>{vol_txt}</b>{end_str}"


def _render_markets(results: list[dict[str, Any]], title: str, note: str = "") -> str:
    """HTML list of markets with header/footer + optional note."""
    lines = [title, sep()]
    for i, m in enumerate(results[:MARKET_LIMIT], 1):
        lines.append(_market_line(i, m))
    if note:
        lines.append("")
        lines.append(f"<i>{note}</i>")
    lines.append(footer_links())
    return "\n".join(lines)


async def _fetch_prediction(
    path: str, params: dict[str, Any] | None = None, timeout_secs: float = 10
) -> dict[str, Any] | None:
    """GET a backend prediction-markets endpoint with the shared-secret header.

    Returns the parsed JSON body or None on any failure (network, non-200,
    malformed body). Callers render a graceful unavailable state on None.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            r = await client.get(
                f"{BACKEND_URL}{path}",
                params=params,
                headers=backend_headers(),
            )
            if r.status_code != 200:
                logger.debug("prediction %s status %s", path, r.status_code)
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("prediction %s fetch failed: %s", path, exc)
        return None


async def cmd_polymarket(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /polymarket [category] — trending prediction markets by volume.

    Optional category: all (default), crypto, security, regulation.
    """
    category = ctx.args[0].lower() if ctx.args else "all"
    if category not in POLYMARKET_CATEGORIES:
        category = "all"
    msg = await update.message.reply_text("\U0001f52e <b>Fetching prediction markets...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    data = await _fetch_prediction(
        "/api/v1/prediction-markets/trending", params={"limit": MARKET_LIMIT, "category": category}
    )
    results = (data or {}).get("results") or []
    if not results:
        await msg.edit_text(PREDICTION_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    cat_label = f" \u00b7 {category.title()}" if category != "all" else ""
    title = f"\U0001f52e <b>Prediction Markets{cat_label}</b>"
    note = "Trending by volume \u2014 Polymarket \u00b7 Kalshi \u00b7 Limitless"
    await msg.edit_text(
        _render_markets(results, title, note),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def cmd_election(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /election [query] — election-specific prediction markets.

    Searches the backend for "election" (plus any user query words) and
    shows the top markets by volume.
    """
    extra = " ".join(ctx.args) if ctx.args else ""
    q = f"election {extra}".strip() or "election"
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        "\U0001f5f3\ufe0f <b>Fetching election odds...</b>", parse_mode=ParseMode.HTML
    )
    data = await _fetch_prediction("/api/v1/prediction-markets/search", params={"q": q, "limit": 40})
    results = (data or {}).get("results") or []
    if not results:
        await msg.edit_text(PREDICTION_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    # Search returns relevance-ordered results; rank by volume so the most
    # traded markets surface first.
    results = sorted(results, key=lambda m: float(m.get("volume_usd") or 0), reverse=True)
    title = "\U0001f5f3\ufe0f <b>Election Odds</b>"
    note = "Election markets by volume \u2014 real money: Polymarket \u00b7 Kalshi"
    await msg.edit_text(
        _render_markets(results, title, note),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def cmd_odds(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /odds <topic> — search odds for any topic (token, event, policy)."""
    topic = " ".join(ctx.args).strip() if ctx.args else ""
    if not topic:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f4b0 <b>Odds Search</b>\n\n"
            "Search prediction market odds for any topic.\n\n"
            "Usage: <code>/odds bitcoin</code>\n"
            "       <code>/odds fed rate cut</code>\n"
            "       <code>/odds ethereum</code>\n\n"
            "\U0001f4a1 Tip: try /polymarket for trending markets or "
            "/election for election odds.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
        return
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f4b0 <b>Fetching odds for \u201c{escape(topic[:60])}\u201d...</b>",
        parse_mode=ParseMode.HTML,
    )
    data = await _fetch_prediction("/api/v1/prediction-markets/search", params={"q": topic, "limit": 40})
    results = (data or {}).get("results") or []
    if not results:
        await msg.edit_text(
            f"\u274c <b>No markets found</b> for \u201c{escape(topic[:60])}\u201d.\n\n"
            "Try a broader term, e.g. <code>/odds crypto</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    results = sorted(results, key=lambda m: float(m.get("volume_usd") or 0), reverse=True)
    title = f"\U0001f4b0 <b>Odds: {escape(topic[:60])}</b>"
    note = "Across Polymarket \u00b7 Kalshi \u00b7 Limitless \u00b7 Manifold"
    await msg.edit_text(
        _render_markets(results, title, note),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


def register_prediction(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register prediction-market commands on the bot application.

    Wires /polymarket, /election and /odds in one call. The Lead wires
    this from bot.py (do not touch bot.py directly).
    """
    app.add_handler(CommandHandler("polymarket", cmd_polymarket))
    app.add_handler(CommandHandler("election", cmd_election))
    app.add_handler(CommandHandler("odds", cmd_odds))

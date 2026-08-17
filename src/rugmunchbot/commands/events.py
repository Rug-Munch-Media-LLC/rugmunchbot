"""
📰 News Events & Price Impact
===============================
Show recent news events for a token and how the price moved around each one.

Backed by the news-event-to-price correlation engine
(``app.domains.scanner.engines.event_correlation``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot.formatters import footer_links, sep
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.events")

_CORRELATION_EMOJI: dict[str, str] = {
    "STRONG_CORRELATION": "🟢",
    "MODERATE_CORRELATION": "🟡",
    "NO_CORRELATION": "⚪",
}
_SENTIMENT_EMOJI: dict[str, str] = {
    "BULLISH": "🐂",
    "BEARISH": "🐻",
    "NEUTRAL": "⚖️",
}


def _fmt_price(value: float) -> str:
    """Format a USD price compactly."""
    if value >= 1000:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:,.4f}"
    return f"${value:.8f}"


def _fmt_change(pct: float) -> str:
    """Format a percent change with sign + emoji."""
    sign = "+" if pct > 0 else ""
    arrow = "📈" if pct > 0 else "📉" if pct < 0 else "-"
    return f"{arrow} {sign}{pct:.2f}%"


def _format_event(event: dict[str, Any]) -> str:
    """Render a single correlated event as a compact Telegram block."""
    title = str(event.get("title") or "Untitled")[:120]
    sent = str(event.get("sentiment") or "NEUTRAL")
    corr = str(event.get("correlation") or "NO_CORRELATION")
    url = str(event.get("url") or "")

    sent_e = _SENTIMENT_EMOJI.get(sent, "⚖️")
    corr_e = _CORRELATION_EMOJI.get(corr, "⚪")

    title_linked = f'<a href="{url}">{title}</a>' if url else title
    lines = [
        f"{corr_e} <b>{title_linked}</b>",
        f"{sent_e} {sent} · {_fmt_change(float(event.get('price_change_pct', 0) or 0))}",
        f"   <code>{_fmt_price(float(event.get('price_before', 0) or 0))}</code> → "
        f"<code>{_fmt_price(float(event.get('price_after', 0) or 0))}</code> "
        f"(<i>{corr}</i>)",
    ]
    return "\n".join(lines)


async def cmd_events(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /events command - show news events and their price impact."""
    if update.message is None:
        return
    msg = update.message
    args = ctx.args if ctx.args is not None else []

    if not args:
        await msg.reply_text(
            "📰 <b>Usage:</b> <code>/events &lt;token_address&gt; [chain]</code>\n\n"
            "Shows recent news events for a token and whether the price moved "
            "with each one.\n\n"
            "Example: <code>/events 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 ethereum</code>\n"
            "You can also use a ticker: <code>/events BTC</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    token = args[0]
    chain = args[1] if len(args) > 1 else "ethereum"

    await msg.chat.send_action("typing")
    status = await msg.reply_text("📰 <b>Correlating news with price...</b>", parse_mode=ParseMode.HTML)

    try:
        from app.domains.scanner.engines.event_correlation import correlate_news_with_price

        result = await correlate_news_with_price(token, chain, lookback_hours=24)
        events = result.get("events") or []
    except Exception as exc:
        logger.warning("cmd_events correlation failed: %s", exc)
        events = []

    if not events:
        await status.edit_text(
            "📰 <b>No correlatable news events</b>\n\n"
            f"Token: <code>{token}</code>\nChain: <code>{chain}</code>\n\n"
            "No news events with usable price data were found in the last 24h. "
            "Try a more liquid token (e.g. BTC/ETH) or check back later.",
            parse_mode=ParseMode.HTML,
        )
        return

    summary = str(result.get("summary") or "")
    lines = [
        "📰 <b>News → Price Correlation</b>",
        f"Token: <code>{token}</code> · Chain: <code>{chain}</code>",
        f"<i>{summary}</i>",
        sep(),
    ]
    for event in events[:8]:
        lines.append(_format_event(event))
        lines.append("")
    lines.append(footer_links())

    text = "\n".join(lines)
    try:
        await status.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as exc:
        logger.debug("cmd_events edit failed: %s", exc)
        await status.edit_text(
            "📰 <b>News → Price Correlation</b>\n\n" + summary,
            parse_mode=ParseMode.HTML,
        )

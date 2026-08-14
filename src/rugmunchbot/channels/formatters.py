"""Per-channel HTML content formatters for the bot-owned Telegram channels.

One formatter per channel:
* ``format_news_post``   — news channel (curated headlines, source-cited).
* ``format_alerts_post`` — alerts channel (scanner risk digest, /scan links).
* ``format_alpha_post``  — alpha channel (whale moves + smart money + launches).

All backend/user-supplied text is HTML-escaped (``quote=False`` so Telegram
``<a>``/``<code>`` tags we build ourselves stay intact). Messages stay under
Telegram's 4096-char soft limit and always carry the bot footer.
"""

from __future__ import annotations

import html
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.formatters import footer_links, sep, short_addr

_MAX_BODY_CHARS = 4090


def _esc(value: object) -> str:
    """HTML-escape text for Telegram parse_mode=HTML (quotes left intact)."""
    return html.escape(str(value), quote=False)


def _clip(text: str, limit: int = _MAX_BODY_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def _risk_emoji(risk_level: str) -> str:
    return {"critical": "🔴", "high": "🔴", "medium": "🟡", "low": "🟢", "safe": "✅"}.get(
        str(risk_level).lower(), "⚪"
    )


def _now_stamp() -> str:
    return datetime.now(UTC).strftime("%b %d, %H:%M UTC")


# ── News channel ──────────────────────────────────────────────────────────


def format_news_post(items: list[dict[str, Any]]) -> str:
    """News digest: numbered, source-cited headlines with links."""
    lines = ["📰 <b>RugMunch News</b>", sep(), ""]
    for i, item in enumerate(items[:6], 1):
        title = _esc(item.get("title") or "Untitled")
        source = _esc(item.get("source") or "unknown")
        url = (item.get("url") or "").strip()
        if url:
            title = f'<a href="{html.escape(url, quote=True)}">{title}</a>'
        lines.append(f"{i}. {title}")
        rating_bits = []
        if item.get("sentiment_label"):
            rating_bits.append(f"sentiment {_esc(item['sentiment_label'])}")
        if item.get("factual_rating"):
            rating_bits.append(f"factual {_esc(item['factual_rating'])}")
        suffix = f" — {source}" + (f" ({', '.join(rating_bits)})" if rating_bits else "")
        lines.append(f"   <i>{suffix}</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    return _clip("\n".join(lines))


# ── Alerts channel ────────────────────────────────────────────────────────


def format_alerts_post(alerts: list[dict[str, Any]]) -> str:
    """Scanner risk digest: high-risk tokens with honeypot/rug flags + /scan."""
    lines = ["🚨 <b>Risk Alerts</b>", sep(), ""]
    if not alerts:
        lines.append("No new high-risk tokens detected in the last window.")
        lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
        return _clip("\n".join(lines))
    for item in alerts[:6]:
        symbol = _esc(item.get("symbol") or "???")
        name = _esc(item.get("name") or symbol)
        addr = str(item.get("address") or "")
        chain = _esc(item.get("chain") or "")
        risk = _esc(item.get("risk_level") or "unknown")
        score = item.get("risk_score")
        score_txt = f"{_risk_emoji(risk)} <b>{name}</b> ({symbol})"
        if score is not None:
            with suppress(TypeError, ValueError):
                score_txt += f" — risk {float(score):.0f}/100"
        lines.append(score_txt)
        if chain:
            lines.append(f"   ⛓️ {chain}")
        if addr:
            lines.append(f"   🔍 /scan <code>{_esc(addr)}</code>")
        flags = [f for f in item.get("risk_flags") or [] if f]
        if flags:
            lines.append("   ⚠️ " + " · ".join(_esc(f) for f in flags[:5]))
        lines.append("")
    lines.append("<i>Auto-flagged by the RugMunch scanner — always /scan before buying.</i>")
    lines.extend(["", f"🕐 {_now_stamp()}", footer_links()])
    return _clip("\n".join(lines))


# ── Alpha channel ─────────────────────────────────────────────────────────


def _fmt_usd(value: Any) -> str:
    with suppress(TypeError, ValueError):
        return f"${float(value):,.0f}"
    return ""


def _fmt_wallet_short(addr: str) -> str:
    return short_addr(addr) if addr else "?"


def format_alpha_post(
    whale_moves: list[dict[str, Any]],
    smart_money: list[dict[str, Any]],
    launches: list[dict[str, Any]],
) -> str:
    """Alpha digest: whale moves + smart money wallets + new launches.

    Every section only renders when its live source returned data — an empty
    section is omitted, never invented. Source labels are honest about where
    each item came from.
    """
    lines = ["🐋 <b>Alpha Feed</b>", sep(), ""]

    if whale_moves:
        lines.append("⚡ <b>Whale Moves</b>")
        for move in whale_moves[:4]:
            title = _esc(move.get("title") or "Whale move")
            chain = _esc(move.get("chain") or "solana")
            addr = str(move.get("token_address") or "")
            lines.append(f"• {title} ({chain})")
            if addr:
                lines.append(f"  🔍 /scan <code>{_esc(addr)}</code>")
        lines.append("")

    if smart_money:
        lines.append("🧠 <b>Smart Money Wallets</b>")
        for w in smart_money[:5]:
            label = _esc(w.get("label") or _fmt_wallet_short(str(w.get("address") or "")))
            addr = str(w.get("address") or "")
            pnl = _fmt_usd(w.get("pnl_usd") or w.get("realized_pnl"))
            bits = [f"<code>{_esc(addr)}</code>"]
            if pnl:
                bits.append(f"P&L {pnl}")
            lines.append(f"• {label} — " + " · ".join(bits))
        lines.append("")

    if launches:
        lines.append("🆕 <b>New Launches</b>")
        for launch in launches[:5]:
            symbol = _esc(launch.get("symbol") or "???")
            name = _esc(launch.get("name") or symbol)
            chain = _esc(launch.get("chain") or "")
            risk = _esc(launch.get("risk_level") or "unknown")
            addr = str(launch.get("address") or "")
            lines.append(
                f"• {_risk_emoji(risk)} <b>{name}</b> ({symbol}){f' — {chain}' if chain else ''}"
            )
            if addr:
                lines.append(f"  🔍 /scan <code>{_esc(addr)}</code>")
        lines.append("")

    if not whale_moves and not smart_money and not launches:
        lines.append("No fresh alpha signals in this window — checking again soon.")
        lines.append("")

    lines.append(f"🕐 {_now_stamp()}")
    lines.append(footer_links())
    return _clip("\n".join(lines))


__all__ = ["format_alerts_post", "format_alpha_post", "format_news_post"]

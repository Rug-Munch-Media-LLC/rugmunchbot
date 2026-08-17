"""Pure formatting helpers for the free-text AI chat pipeline.

Extracted from ``handlers/chat_ai/__init__.py`` during the de-godding pass:
functions that render REAL tool data / bridge metadata into plain or HTML
text. They carry no ``Update``/``Context`` state and no handler registration —
the handler re-imports them, so behavior is byte-for-byte identical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.domains.telegram.rugmunchbot.config import fmt_chain, fmt_number, fmt_pct
from app.domains.telegram.rugmunchbot.formatters import detect_chain

if TYPE_CHECKING:
    from app.domains.telegram.rugmunchbot.tools.registry import ToolResult


def _render_bridge_actions(
    actions: list[dict[str, str]] | None,
) -> InlineKeyboardMarkup | None:
    """Map bridge suggested actions to Telegram inline buttons.

    Only existing, safe bot surfaces are wired: ``analyze`` targets become
    full-scan buttons (callback ``scan_<addr>_<chain>`` — the same convention
    as the rest of the bot). Unknown action types are skipped rather than
    rendering dead buttons. Returns ``None`` when there is nothing to show.
    """
    if not actions:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        action = str(a.get("action") or "")
        label = str(a.get("label") or "")
        target = str(a.get("target") or "")
        if action == "analyze" and target:
            chain = detect_chain(target)
            rows.append([InlineKeyboardButton(label, callback_data=f"scan_{target}_{chain}")])
        # exploit / other action types have no dedicated bot surface yet — skip.
    return InlineKeyboardMarkup(rows) if rows else None


def _format_tool_answer(results: dict[str, ToolResult]) -> str | None:
    """Render REAL tool data as a markdown answer (no LLM involved).

    Used when the whole LLM chain is down but the tools returned live data —
    the bot still answers with REAL numbers instead of guessing. Returns
    ``None`` when there is nothing usable to render.

    Emits MARKDOWN (``**bold**``, `` `code` ``, ``*italic*``), never raw HTML:
    the answer flows through ``_send_answer``'s untrusted-LLM pipeline
    (normalize → escape → :func:`_markdown_to_html`), which converts the
    markers to real Telegram HTML but would escape any literal ``<b>`` tag
    into visible ``&lt;b&gt;`` garbage.

    ``web_search`` results are included too: its synthesized fallbacks
    (knowledge answer / honest no-live-data message) are usable, labeled
    answers — never fabricated — so they render instead of the generic
    fallback message. Live tool data keeps the "Live data" footer; a
    web-only fallback gets a knowledge footer instead.
    """
    lines: list[str] = []
    has_live = False
    price = results.get("price_lookup")
    if price is not None and price.ok and isinstance(price.data, dict):
        pairs = price.data.get("pairs") or []
        if pairs:
            has_live = True
            p = pairs[0]
            addr = p.get("address", "")
            lines.append(
                f"\U0001fa99 **{p.get('name', p.get('symbol', ''))}** ({p.get('symbol', '')}) "
                f"{fmt_chain(p.get('chain', ''))}\n`{addr}`"
            )
            if p.get("price_usd") is not None:
                lines.append(f"\U0001f4b0 {fmt_number(p['price_usd'])} | 24h: {fmt_pct(p.get('change_24h'))}")
            if p.get("market_cap"):
                lines.append(f"\U0001f4c8 MCap: {fmt_number(p.get('market_cap'))}")
            extra = []
            if p.get("liquidity_usd") is not None:
                extra.append(f"Liq {fmt_number(p['liquidity_usd'])}")
            if p.get("volume_24h") is not None:
                extra.append(f"Vol {fmt_number(p['volume_24h'])}")
            if p.get("fdv"):
                extra.append(f"FDV {fmt_number(p['fdv'])}")
            if extra:
                lines.append("\U0001f4a7 " + " | ".join(extra))
            lines.append("")
    trending = results.get("trending")
    if trending is not None and trending.ok and isinstance(trending.data, dict):
        tokens = trending.data.get("tokens") or []
        if tokens:
            has_live = True
            # Whole-line **bold** so _beautify_answer's header pass unwraps it
            # cleanly into a bold section header + thin separator.
            lines.append("**\U0001f525 Trending**")
            for t in tokens[:8]:
                addr = t.get("address", "")
                sym = t.get("symbol", "?")
                chg = f" | {fmt_pct(t.get('change_24h'))}" if t.get("change_24h") is not None else ""
                lines.append(f"\u2022 ${sym} {fmt_chain(t.get('chain', ''))}{chg} — `{addr}`")
            lines.append("")
    trend_ranked = results.get("trend_by_chain")
    if trend_ranked is not None and trend_ranked.ok and isinstance(trend_ranked.data, dict):
        tokens = trend_ranked.data.get("tokens") or []
        if tokens:
            has_live = True
            rank_mode = trend_ranked.data.get("rank_mode") or "trend"
            if rank_mode == "risk":
                lines.append("**\U0001f6a8 Riskiest / Sketchiest**")
                for t in tokens[:8]:
                    addr = t.get("address", "")
                    sym = t.get("symbol", "?")
                    flags = t.get("risk_flags") or []
                    flag_str = f" — {', '.join(flags)}" if flags else ""
                    liq = t.get("liquidity_usd")
                    liq_str = f" | Liq {fmt_number(liq)}" if liq is not None else ""
                    lines.append(f"\u2022 ${sym} {fmt_chain(t.get('chain', ''))}{liq_str}{flag_str} — `{addr}`")
            else:
                lines.append("**\U0001f525 Trending (scored)**")
                for t in tokens[:8]:
                    addr = t.get("address", "")
                    sym = t.get("symbol", "?")
                    score = t.get("score")
                    score_str = f" | score {fmt_number(score)}" if score is not None else ""
                    lines.append(f"\u2022 ${sym} {fmt_chain(t.get('chain', ''))}{score_str} — `{addr}`")
            lines.append("")
    security = results.get("security_scan")
    if security is not None and security.ok and isinstance(security.data, dict):
        score = security.data.get("risk_score")
        if score is not None:
            has_live = True
            emoji = "\U0001f7e2" if score < 40 else ("\U0001f7e1" if score < 70 else "\U0001f534")
            lines.append(f"**\U0001f50d Risk: {emoji} {score}/100**")
            if security.data.get("is_honeypot") is not None:
                lines.append(f"honeypot: {security.data['is_honeypot']}")
            lines.append("")
    # web_search synthesized fallback (knowledge / honest no-live-data):
    # already labeled in the context — render as-is, never as live data.
    web = results.get("web_search")
    if web is not None and web.ok and isinstance(web.data, dict):
        context = str(web.data.get("context") or "").strip()
        if context:
            lines.append(f"{context}\n")
    if not lines:
        return None
    if has_live:
        lines.append("*Live data via Rug Munch tools.*")
    else:
        lines.append("*Rug Munch knowledge — live web unavailable.*")
    return "\n".join(lines)


__all__ = ["_format_tool_answer", "_render_bridge_actions"]

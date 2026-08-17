"""News-card formatting for the RugMunch bot.

Extracted from ``formatters/_sentinel.py`` during the Batch 13 de-godding
pass: news sentiment + news card lines. ``_sentinel.py`` re-exports every
public name.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.formatters._base import thin_sep

__all__ = ["format_news_card", "format_news_sentiment"]


def format_news_card(nc: dict[str, Any] | None) -> list[str]:
    """Format a Ground News-style news card into scan-report lines.

    Accepts the engine payload from ``get_token_news_card``:

    .. code-block:: text

        📰 News Sentiment: BEARISH 🔴 (72 trending)
        ────────────────────────
        ├─ CoinDesk: "Coldcard exploit..." [VERIFIED] [HIGH IMPACT]
        ├─ TheBlock: "Bitcoin wallets drained..." [REPORTED]
        └─ 3 sources corroborate · on-chain evidence ✓

    Returns an empty list when there is no usable data so callers can
    skip the block entirely.
    """
    if not isinstance(nc, dict):
        return []
    articles = nc.get("articles")
    if not isinstance(articles, list) or not articles:
        return []

    sentiment = str(nc.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    trending = int(nc.get("trending_score") or 0)

    lines = [
        "",
        f"\U0001f4f0 News Sentiment: <b>{sentiment}</b> {emoji} ({trending} trending)",
        thin_sep(),
    ]
    for art in articles[:5]:
        source = str(art.get("source") or "Unknown")
        raw_title = str(art.get("title") or "").strip()
        title = raw_title[:60] + ("..." if len(raw_title) > 60 else "")
        factuality = str(art.get("factuality") or "").upper()
        impact = str(art.get("market_impact") or "").upper()
        tags = []
        if factuality in ("VERIFIED", "REPORTED", "OPINION", "RUMOR"):
            tags.append(f"[{factuality}]")
        if impact:
            tags.append(f"[{impact} IMPACT]")
        line = f'\u251c\u2500 {source}: "{title}"'
        if tags:
            line += " " + " ".join(tags)
        lines.append(line)

    corr = nc.get("corroboration") or {}
    if isinstance(corr, dict):
        total = int(corr.get("total_sources") or 0)
        on_chain = bool(corr.get("on_chain_evidence"))
        if total >= 2 or on_chain:
            corr_line = f"\u2514\u2500 {total} sources corroborate"
            if on_chain:
                corr_line += " \u00b7 on-chain evidence \u2713"
            lines.append(corr_line)
    return lines


def format_news_sentiment(ns) -> list[str]:
    """Format a news_sentiment dict into scan-report lines (empty when absent).

    Accepts the engine payload from ``analyze_token_news_sentiment`` (or a
    dict with the same keys). Returns an empty list when there is no usable
    data so callers can skip the block entirely.
    """
    if not isinstance(ns, dict):
        return []
    if not ns.get("article_count"):
        return []
    sentiment = str(ns.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    lines = [
        "",
        "\U0001f4f0 <b>News Sentiment</b>",
        thin_sep(),
        f"{emoji} Sentiment: <b>{sentiment}</b> (confidence {float(ns.get('confidence') or 0) * 100:.0f}%)",
        f"\U0001f4f0 Articles: {int(ns.get('article_count') or 0)}",
    ]
    breakdown = ns.get("sentiment_breakdown") or {}
    if isinstance(breakdown, dict):
        lines.append(
            f"\U0001f4c8 Breakdown: \U0001f7e2{breakdown.get('positive', 0)} "
            f"\U0001f534{breakdown.get('negative', 0)} "
            f"\U0001f7e1{breakdown.get('neutral', 0)}"
        )
    if ns.get("trending_score") is not None:
        lines.append(f"\U0001f525 Media Attention: <b>{int(ns['trending_score'])}/100</b>")
    flags = ns.get("flags") or []
    if isinstance(flags, list) and flags:
        lines.append(f"\U0001f3c6 {', '.join(str(f) for f in flags[:3])}")
    return lines

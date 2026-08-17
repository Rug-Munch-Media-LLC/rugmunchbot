# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/news_sentiment.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""News sentiment analysis engine — RugBrain RAG ``news_v2`` sentiment for tokens.

Queries the RugBrain RAG ``news_v2`` collection (via ``HybridRetriever``) for
articles mentioning a token, runs lightweight keyword sentiment on each, and
aggregates into ``BULLISH`` / ``BEARISH`` / ``NEUTRAL`` / ``NO_DATA`` with a
0-100 media-attention (trending) score.

Architecture:
  1. ``HybridRetriever.retrieve(query=f"{name} {symbol} news",
     collection="news_v2", top_k=10)``
  2. ``analyze_sentiment(text)`` — keyword lexicon (``BULLISH_WORDS`` /
     ``BEARISH_WORDS``)
  3. ``compute_trending_score(articles)`` — recency-weighted media attention
     0-100
  4. Aggregate per-article labels → overall sentiment + confidence

Fail-open: any retrieval failure or empty result set degrades to
``NO_DATA`` with ``article_count: 0`` — this engine never raises into the
scan (news sentiment is best-effort enrichment, not a scan blocker).

Usage:
    from rugmunchbot._vendor.scanner_engines.news_sentiment import (
        analyze_token_news_sentiment,
        get_token_news_card,
    )

    result = await analyze_token_news_sentiment("PEPE", "Pepe", chain="ethereum")
    card = await get_token_news_card("BTC", "Bitcoin", chain="ethereum")
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.embeddings import RAGUnavailableError

if TYPE_CHECKING:
    from rugmunchbot._vendor.rag_pkg.retrieval import ScoredHit

logger = get_logger(__name__)

# ── Keyword lexicons (spec C-news) ───────────────────────────────────
BULLISH_WORDS = {
    "surge",
    "rally",
    "breakout",
    "bullish",
    "gain",
    "pump",
    "moon",
    "adoption",
    "partnership",
    "launch",
    "upgrade",
    "mainnet",
    "listing",
}

BEARISH_WORDS = {
    "crash",
    "dump",
    "hack",
    "exploit",
    "vulnerability",
    "SEC",
    "lawsuit",
    "ban",
    "delist",
    "rugpull",
    "scam",
    "fall",
    "drop",
    "decline",
    "bearish",
    "correction",
}

# ── Config ───────────────────────────────────────────────────────────
NEWS_COLLECTION = "news_v2"
MAX_ARTICLES = 10
HIGH_MEDIA_ATTENTION_THRESHOLD = 70

# Recency windows for trending score (hours)
_RECENT_HOURS = 24
_OLDER_HOURS = 72

# Per-article trending points
_TRENDING_RECENT = 15
_TRENDING_OLDER = 5
_TRENDING_OLD = 1
_TRENDING_CAP = 100

# ── News-card ratings (Ground News style) ────────────────────────────
# Markers that indicate a story is about verifiable on-chain activity —
# when present, the card reports ``on_chain_evidence: true`` (unless the
# caller passes an explicit override from the scanner's own on-chain data).
_ON_CHAIN_MARKERS = {
    "hack",
    "hacked",
    "exploit",
    "drained",
    "drainer",
    "stolen",
    "theft",
    "breach",
    "vulnerability",
    "attack",
    "transaction",
    "transactions",
    "wallet",
    "wallets",
    "transfer",
    "transfers",
    "contract",
    "contracts",
    "audit",
    "bridge",
    "swap",
    "withdraw",
    "deposit",
    "mint",
    "burn",
}


def analyze_sentiment(text: str) -> tuple[str, float]:
    """Simple keyword-based sentiment. Returns (label, confidence)."""
    if not text:
        return ("NEUTRAL", 0.3)
    lower = text.lower()
    bullish = sum(1 for w in BULLISH_WORDS if w in lower)
    bearish = sum(1 for w in BEARISH_WORDS if w in lower)
    if bullish > bearish * 2:
        return ("BULLISH", 0.7)
    elif bearish > bullish * 2:
        return ("BEARISH", 0.7)
    elif bullish > 0 or bearish > 0:
        return ("NEUTRAL", 0.5)
    return ("NEUTRAL", 0.3)


def _parse_published_at(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (with optional ``Z`` suffix) to aware UTC.

    Returns ``None`` for missing or unparseable values — callers treat that
    as "very old / undated" (conservative trending score).
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except (ValueError, TypeError, OSError):
        return None


def compute_trending_score(articles: list[dict[str, Any]]) -> int:
    """How much media attention is this token getting? 0-100.

    Recent articles (<24h): +15 each
    Older articles (24-72h): +5 each
    Very old / undated (>72h or missing): +1 each
    Capped at 100.

    Args:
        articles: List of article dicts with an optional ``published_at``
            key (ISO-8601 string).

    Returns:
        int 0-100.
    """
    score = 0
    now = datetime.now(UTC)
    for article in articles:
        published = _parse_published_at(article.get("published_at") if isinstance(article, dict) else None)
        if published is None:
            score += _TRENDING_OLD
            continue
        age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
        if age_hours < _RECENT_HOURS:
            score += _TRENDING_RECENT
        elif age_hours < _OLDER_HOURS:
            score += _TRENDING_OLDER
        else:
            score += _TRENDING_OLD
    return min(_TRENDING_CAP, score)


def _aggregate_sentiment(labels: list[tuple[str, float]]) -> tuple[str, float]:
    """Aggregate per-article ``(label, confidence)`` into overall sentiment.

    Majority label wins; ties fall to ``NEUTRAL``. Confidence blends the
    agreement ratio and article count, capped at 0.95. Returns ``NO_DATA``
    with 0.0 confidence for an empty list.
    """
    total = len(labels)
    if total == 0:
        return ("NO_DATA", 0.0)
    pos = sum(1 for label, _ in labels if label == "BULLISH")
    neg = sum(1 for label, _ in labels if label == "BEARISH")
    neu = total - pos - neg
    if pos > neg and pos >= neu:
        label = "BULLISH"
    elif neg > pos and neg >= neu:
        label = "BEARISH"
    else:
        label = "NEUTRAL"
    agreement = max(pos, neg, neu) / total
    count_bonus = min(0.15, total * 0.05)
    confidence = round(min(0.95, 0.35 + agreement * 0.5 + count_bonus), 2)
    return (label, confidence)


def _title_from_content(content: str) -> str:
    """Extract the title line from RAG news content (``Title: ...``)."""
    if not content:
        return ""
    for line in content.splitlines():
        line = line.strip()
        if line.lower().startswith("title:"):
            return line[len("title:") :].strip()
    return (content.splitlines()[0] if content.splitlines() else "")[:160]


def _no_data_result(token_symbol: str, token_name: str, chain: str, reason: str) -> dict[str, Any]:
    """Standard NO_DATA envelope (fail-open degradation)."""
    return {
        "check": "news_sentiment",
        "result": "no_data",
        "source": f"rag:{NEWS_COLLECTION}",
        "token_symbol": token_symbol,
        "token_name": token_name,
        "chain": chain,
        "sentiment": "NO_DATA",
        "confidence": 0.0,
        "article_count": 0,
        "recent_articles": [],
        "sentiment_breakdown": {"positive": 0, "negative": 0, "neutral": 0},
        "trending_score": 0,
        "reason": reason,
    }


async def _retrieve_news_hits(token_symbol: str, token_name: str) -> list[ScoredHit] | None:
    """Query RugBrain RAG ``news_v2`` for articles mentioning a token.

    Shared by ``analyze_token_news_sentiment`` and ``get_token_news_card``
    so both engines read the same underlying article set.

    Returns:
        List of hits, or ``None`` when retrieval is unavailable (callers
        degrade to their NO_DATA envelope). An empty list means "no
        articles found" (a successful retrieval with zero results).
    """
    query = f"{token_name} {token_symbol} news".strip()
    try:
        from rugmunchbot._vendor.rag_pkg.retrieval import HybridRetriever

        retriever = HybridRetriever()
        hits: list[ScoredHit] = await retriever.retrieve(
            query=query,
            collection=NEWS_COLLECTION,
            top_k=MAX_ARTICLES,
            rerank=False,
        )
        return hits
    except (
        RAGUnavailableError,
        httpx.HTTPError,
        httpx.TimeoutException,
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        ImportError,
    ) as exc:
        logger.debug("news_retrieve_failed: %s", exc)
        return None


async def analyze_token_news_sentiment(
    token_symbol: str,
    token_name: str,
    chain: str = "ethereum",
) -> dict[str, Any]:
    """Analyze recent news sentiment for a token.

    Queries RugBrain RAG ``news_v2`` for articles mentioning this token.
    Computes sentiment: ``BULLISH`` / ``BEARISH`` / ``NEUTRAL`` / ``NO_DATA``.

    Args:
        token_symbol: Token symbol (e.g. ``"PEPE"``).
        token_name: Token name (e.g. ``"Pepe"``).
        chain: Chain identifier (informational — news is chain-agnostic).

    Returns:
        Dict with the sentiment payload and an engine envelope::

            {
                "check": "news_sentiment",
                "result": "scanned" | "no_data",
                "source": "rag:news_v2",
                "token_symbol": "...",
                "token_name": "...",
                "chain": "ethereum",
                "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL" | "NO_DATA",
                "confidence": 0.0-0.95,
                "article_count": int,
                "recent_articles": [
                    {"title", "url", "published_at", "sentiment", "relevance"}
                ],
                "sentiment_breakdown": {"positive": n, "negative": n, "neutral": n},
                "trending_score": 0-100,
                "flags": ["HIGH_MEDIA_ATTENTION"],  # only when trending > 70
            }

    Fail-open: retrieval errors or empty results return ``NO_DATA`` with
    ``article_count: 0`` (never raises into the scan).
    """
    if not token_symbol and not token_name:
        return _no_data_result(token_symbol, token_name, chain, reason="no_token_identity")

    hits = await _retrieve_news_hits(token_symbol, token_name)
    if hits is None:
        return _no_data_result(token_symbol, token_name, chain, reason="retrieval_unavailable")
    if not hits:
        return _no_data_result(token_symbol, token_name, chain, reason="no_articles")

    articles: list[dict[str, Any]] = []
    labels: list[tuple[str, float]] = []
    for h in hits:
        meta = h.metadata or {}
        title = str(meta.get("title") or "") or _title_from_content(h.content or "")
        url = str(meta.get("source_url") or "")
        published_at = str(meta.get("published_at") or "")
        text = f"{title} {h.content or ''}".strip()
        label, conf = analyze_sentiment(text)
        labels.append((label, conf))
        articles.append(
            {
                "title": title[:160],
                "url": url,
                "published_at": published_at,
                "sentiment": label,
                "relevance": round(float(h.final_score or h.score or 0.0), 3),
            }
        )

    sentiment, confidence = _aggregate_sentiment(labels)
    breakdown = {
        "positive": sum(1 for label, _ in labels if label == "BULLISH"),
        "negative": sum(1 for label, _ in labels if label == "BEARISH"),
        "neutral": sum(1 for label, _ in labels if label == "NEUTRAL"),
    }
    trending = compute_trending_score(articles)

    result: dict[str, Any] = {
        "check": "news_sentiment",
        "result": "scanned",
        "source": f"rag:{NEWS_COLLECTION}",
        "token_symbol": token_symbol,
        "token_name": token_name,
        "chain": chain,
        "sentiment": sentiment,
        "confidence": confidence,
        "article_count": len(articles),
        "recent_articles": articles[:MAX_ARTICLES],
        "sentiment_breakdown": breakdown,
        "trending_score": trending,
    }
    if trending > HIGH_MEDIA_ATTENTION_THRESHOLD:
        result["flags"] = ["HIGH_MEDIA_ATTENTION"]

    logger.debug(
        "news_sentiment_computed",
        token=f"{token_symbol or token_name}",
        articles=len(articles),
        sentiment=sentiment,
        trending=trending,
    )
    return result


# ── News card (Ground News style) ─────────────────────────────────────


def _source_name_from_url(url: str) -> str:
    """Derive a display source name from an article URL (e.g. CoinDesk)."""
    if not url:
        return ""
    try:
        domain = urlparse(url).hostname or ""
    except ValueError:
        return ""
    domain = domain.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    parts = domain.split(".")
    if len(parts) >= 2 and parts[-1] in {"com", "co", "io", "org", "net"}:
        name = parts[-2]
    else:
        name = parts[0] if parts else domain
    if not name:
        return ""
    return name[:1].upper() + name[1:]


def _derive_group_id(meta: dict[str, Any], url: str, index: int) -> str:
    """Story group id for an article.

    Prefers the RAG crawler's ``group_id`` (CryptoPanic-style story
    grouping — same story from different sources shares one group).
    Falls back to a stable hash of the URL; last resort a per-index
    placeholder for undated/URL-less items.
    """
    group = str(meta.get("group_id") or "").strip()
    if group:
        return group
    if url:
        digest = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"grp:{digest}"
    return f"grp:untracked:{index}"


def _has_on_chain_markers(text: str) -> bool:
    """True when article text references verifiable on-chain activity."""
    if not text:
        return False
    lower = text.lower()
    return any(marker in lower for marker in _ON_CHAIN_MARKERS)


def _no_data_card(token_symbol: str, token_name: str, chain: str, reason: str) -> dict[str, Any]:
    """Standard NO_DATA news-card envelope (fail-open degradation)."""
    return {
        "check": "news_card",
        "result": "no_data",
        "source": f"rag:{NEWS_COLLECTION}",
        "token_symbol": token_symbol,
        "token_name": token_name,
        "chain": chain,
        "sentiment": "NO_DATA",
        "trending_score": 0,
        "articles": [],
        "corroboration": {
            "total_sources": 0,
            "independent_count": 0,
            "on_chain_evidence": False,
        },
        "reason": reason,
    }


async def get_token_news_card(
    token_symbol: str,
    token_name: str,
    chain: str = "ethereum",
    limit: int = 5,
    on_chain_evidence: bool | None = None,
) -> dict[str, Any]:
    """Get a complete news card for a token — Ground News style.

    Queries the same RugBrain RAG ``news_v2`` article set as
    ``analyze_token_news_sentiment``, then rates each article across the
    Ground News-style axes (factuality / source reputation / market impact /
    sentiment / tokens mentioned) via ``app.rag.ratings`` and aggregates
    corroboration across story groups.

    Args:
        token_symbol: Token symbol (e.g. ``"BTC"``).
        token_name: Token name (e.g. ``"Bitcoin"``).
        chain: Chain identifier (informational — news is chain-agnostic).
        limit: Max articles to include in the card (default 5).
        on_chain_evidence: Optional override for on-chain corroboration.
            ``True`` when the scanner has its own on-chain evidence (e.g.
            news-event-to-price correlation); ``None`` (default) lets the
            engine derive it from on-chain markers in the article text.

    Returns:
        Full news card::

            {
                "check": "news_card",
                "result": "scanned" | "no_data",
                "source": "rag:news_v2",
                "token_symbol": "...",
                "token_name": "...",
                "chain": "ethereum",
                "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL" | "NO_DATA",
                "trending_score": 0-100,
                "articles": [
                    {
                        "title": "...",
                        "url": "...",
                        "source": "CoinDesk",
                        "published_at": "...",
                        "factuality": "verified",
                        "source_reputation": "established",
                        "market_impact": "high",
                        "sentiment": "BEARISH",
                        "tokens_mentioned": ["BTC", "ETH"],
                        "group_id": "grp:abc123",
                    }
                ],
                "corroboration": {
                    "total_sources": 3,      # distinct reporting sources
                    "independent_count": 2,  # distinct story groups
                    "on_chain_evidence": True,
                },
            }

    Fail-open: retrieval errors or empty results return a ``no_data`` card
    with empty articles (never raises into the scan).
    """
    if not token_symbol and not token_name:
        return _no_data_card(token_symbol, token_name, chain, reason="no_token_identity")

    hits = await _retrieve_news_hits(token_symbol, token_name)
    if hits is None:
        return _no_data_card(token_symbol, token_name, chain, reason="retrieval_unavailable")
    if not hits:
        return _no_data_card(token_symbol, token_name, chain, reason="no_articles")

    from rugmunchbot._vendor.rag_pkg.ratings import rate_article

    articles: list[dict[str, Any]] = []
    labels: list[tuple[str, float]] = []
    sources: set[str] = set()
    groups: set[str] = set()
    marker_hit = False

    for i, h in enumerate(hits[:MAX_ARTICLES]):
        meta = h.metadata or {}
        title = str(meta.get("title") or "") or _title_from_content(h.content or "")
        url = str(meta.get("source_url") or "")
        published_at = str(meta.get("published_at") or "")
        text = f"{title} {h.content or ''}".strip()

        rating = rate_article(
            {
                "title": title,
                "content": h.content or "",
                # Prefer the URL so the ratings tier map can resolve the bare
                # domain (e.g. coindesk.com → established); fall back to the
                # source name for display when no URL is present.
                "source": url or str(meta.get("source_name") or ""),
                "source_url": url,
                "url": url,
                "published_at": published_at,
            }
        )

        display_source = str(meta.get("source_name") or "") or _source_name_from_url(url) or "Unknown"
        group_id = _derive_group_id(meta, url, i)

        labels.append((rating.sentiment, rating.confidence))
        sources.add(display_source)
        groups.add(group_id)
        if _has_on_chain_markers(text):
            marker_hit = True

        articles.append(
            {
                "title": title[:160],
                "url": url,
                "source": display_source,
                "published_at": published_at,
                "factuality": str(rating.factuality.value),
                "source_reputation": str(rating.source_reputation.value),
                "market_impact": str(rating.market_impact.value),
                "sentiment": rating.sentiment,
                "tokens_mentioned": list(rating.tokens_mentioned)[:8],
                "group_id": group_id,
            }
        )

    sentiment, _confidence = _aggregate_sentiment(labels)
    trending = compute_trending_score(articles)

    if on_chain_evidence is None:
        # Strong multi-source corroboration (2+ stories from 3+ sources)
        # also counts as evidence the event is real and verifiable.
        on_chain_evidence = marker_hit or (len(groups) >= 2 and len(sources) >= 3)

    card: dict[str, Any] = {
        "check": "news_card",
        "result": "scanned",
        "source": f"rag:{NEWS_COLLECTION}",
        "token_symbol": token_symbol,
        "token_name": token_name,
        "chain": chain,
        "sentiment": sentiment,
        "trending_score": trending,
        "articles": articles[:limit],
        "corroboration": {
            "total_sources": len(sources),
            "independent_count": len(groups),
            "on_chain_evidence": bool(on_chain_evidence),
        },
    }
    if trending > HIGH_MEDIA_ATTENTION_THRESHOLD:
        card["flags"] = ["HIGH_MEDIA_ATTENTION"]

    logger.debug(
        "news_card_computed",
        token=f"{token_symbol or token_name}",
        articles=len(articles),
        sentiment=sentiment,
        trending=trending,
        sources=len(sources),
        groups=len(groups),
    )
    return card

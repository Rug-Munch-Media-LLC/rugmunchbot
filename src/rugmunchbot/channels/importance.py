"""Reranker-based news importance scoring for the bot's news channel.

Scores the normalized ``NewsArticle`` objects produced by the TG news sweep
(``channels/telegram_news.py``) with the existing qwen3 cross-encoder
(``BAAI/bge-reranker-v2-m3`` via ``app.rag.rerank.rerank_results`` — the
same reranker the RAG pipeline uses, never a second client). Every article is
scored against a small set of **importance queries**; the composite
importance is the **max** relevance across queries.

Composite formula::

    importance(article) = max_q rerank(importance_query_q, article_text)

    article_text = title + body (truncated, ``MAX_ARTICLE_CHARS``)
    selected     = importance >= RMI_NEWS_IMPORTANCE_THRESHOLD (default 0.4)

The threshold is calibrated live against the model's sigmoid output: real
important items (exploit / ETF approval / whale move) score ~0.5-0.99 while
routine noise scores ~0.0001-0.05. Queries are phrased to lexically overlap
the way real headlines talk about each theme (the cross-encoder's output is
sharply compressed for paraphrase-level matches).

Fail-open
---------
If qwen3 /rerank is unreachable, the scorer falls back to a deterministic
keyword + ``rate_article`` sentiment heuristic; ``select_important`` then
keeps the top-N by heuristic score **without** the threshold so the channel
keeps flowing. The fallback never invents content.

Env knobs: ``RMI_NEWS_IMPORTANCE_THRESHOLD`` (0.4) · ``RMI_NEWS_IMPORTANCE_QUERIES``
(pipe-separated) · ``RMI_NEWS_FOCUS_KEYWORDS`` · ``RMI_NEWS_IMPORTANCE_MAX_BATCH``
(batch size — every article is scored, batches are chunked).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

logger = get_logger("rmi_bot.channels.importance")

# ── Queries ────────────────────────────────────────────────────────────

#: Default importance queries, phrased to lexically overlap real headlines.
#: Calibrated live on BAAI/bge-reranker-v2-m3 (2026-08-14): ETF approval
#: ~0.92-0.98, bridge exploit ~0.98, whale move ~0.59, noise ~0.0001-0.05.
#: Avoid "or"-heavy phrasings — the sigmoid is compressed and dilutes them.
DEFAULT_IMPORTANCE_QUERIES: tuple[str, ...] = (
    "major market-moving event in crypto",
    "Bitcoin or Ethereum price reaches a new all-time high or record high",
    "crypto price crash, liquidation cascade, or market panic",
    "crypto hack or exploit steals user funds",
    "smart contract or bridge vulnerability exploited with funds drained",
    "crypto project rugpull, scam, or exit scam",
    "spot ETF approval for Bitcoin or Ethereum",
    "major token or exchange announces delisting, suspension, or freeze",
    "large whale or institutional transfer of Bitcoin, Ethereum, or stablecoins",
    "major crypto company announces strategic partnership or institutional adoption",
)

#: Max characters of an article's title+body sent to the cross-encoder.
MAX_ARTICLE_CHARS: int = 1500


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, min(1.0, float(raw.strip())))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_queries(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    parts = [p.strip() for p in raw.replace(",", "|").split("|") if p.strip()]
    return tuple(parts) if parts else default


IMPORTANCE_THRESHOLD: float = _env_float("RMI_NEWS_IMPORTANCE_THRESHOLD", 0.4)
IMPORTANCE_QUERIES: tuple[str, ...] = _env_queries("RMI_NEWS_IMPORTANCE_QUERIES", DEFAULT_IMPORTANCE_QUERIES)
FOCUS_KEYWORDS: tuple[str, ...] = _env_queries("RMI_NEWS_FOCUS_KEYWORDS", ())
MAX_ARTICLES_PER_BATCH: int = _env_int("RMI_NEWS_IMPORTANCE_MAX_BATCH", 100)

# ── Fallback heuristic signals ──────────────────────────────────────────

#: Strong signals — one hit pushes an article to the top of the fallback
#: ordering; two or more near-certainty.
_STRONG_IMPORTANCE_KEYWORDS: tuple[str, ...] = (
    "hack",
    "exploit",
    "rug",
    "rugpull",
    "rug pull",
    "drained",
    "stolen",
    "breach",
    "etf",
    "sec approves",
    "sec rejects",
    "delist",
    "halt",
    "freeze",
    "liquidation",
    "bankrupt",
    "all-time high",
    "record high",
    "milestone",
)

#: Weak signals — one hit is a modest bump.
_WEAK_IMPORTANCE_KEYWORDS: tuple[str, ...] = (
    "whale",
    "inflow",
    "outflow",
    "institutional",
    "partnership",
    "regulation",
    "regulatory",
    "lawsuit",
    "crash",
    "collapse",
    "suspend",
    "approval",
    "rejection",
    "listing",
)


@dataclass(slots=True)
class ScoredArticle:
    """A normalized article with its rerank-derived importance score."""

    article: NewsArticle
    importance: float = 0.0
    best_query: str = ""
    fallback: bool = False  # True when scored by the heuristic path


# ── Text prep ───────────────────────────────────────────────────────────


def _article_text(article: NewsArticle) -> str:
    text = f"{article.title}. {article.body}".strip()
    return text[:MAX_ARTICLE_CHARS]


def _build_queries(focus_keywords: list[str] | tuple[str, ...] | None) -> list[str]:
    """Importance queries + focus keywords (``news related to <kw>``)."""
    queries: list[str] = list(IMPORTANCE_QUERIES)
    for kw in focus_keywords or FOCUS_KEYWORDS:
        kw = kw.strip()
        if kw:
            queries.append(f"news related to {kw}")
    return queries


# ── Fallback heuristic ──────────────────────────────────────────────────


def _heuristic_importance(article: NewsArticle) -> float:
    """Deterministic keyword + ``rate_article`` sentiment importance (0-1).

    Used only when qwen3 /rerank is unreachable. Combines strong/weak
    importance keyword hits with the magnitude of the existing
    ``rate_article`` sentiment score (a strongly bullish/bearish headline is
    more likely market-moving). Never invents content.
    """
    text = f"{article.title} {article.body}".lower()
    strong = sum(1 for kw in _STRONG_IMPORTANCE_KEYWORDS if kw in text)
    weak = sum(1 for kw in _WEAK_IMPORTANCE_KEYWORDS if kw in text)

    score = 0.1 + 0.2 * min(strong, 4) + 0.05 * min(weak, 4)
    try:
        from rugmunchbot._vendor.news_ratings import rate_article

        rating = rate_article(title=article.title, content=article.body, source=article.source_channel)
        score += abs(rating.sentiment_score) / 100.0 * 0.2
    except Exception:  # fail-open: sentiment is a bonus, never fatal
        logger.debug("importance:rate_article_fallback_failed", exc_info=True)
    return round(min(1.0, score), 4)


def _heuristic_scored(articles: list[NewsArticle]) -> list[ScoredArticle]:
    return [
        ScoredArticle(
            article=article,
            importance=_heuristic_importance(article),
            best_query="heuristic-fallback",
            fallback=True,
        )
        for article in articles
    ]


# ── Reranker circuit breaker ────────────────────────────────────────────

#: Consecutive all-failed scoring calls that trip the breaker + cooldown while tripped.
RERANKER_FAILURE_THRESHOLD: int = 3
RERANKER_COOLDOWN_SECONDS: float = 600.0

#: Process-local breaker state. Two scalar counters — nothing accumulates,
#: no locks (single event loop per process). Reset to 0.0/0 on success.
_consecutive_reranker_failures: int = 0
_reranker_cooldown_until: float = 0.0  # time.monotonic() deadline; 0.0 = open


def _reranker_tripped() -> bool:
    """True while the reranker circuit breaker is in its cooldown window."""
    return time.monotonic() < _reranker_cooldown_until


# ── Scoring ─────────────────────────────────────────────────────────────


async def _rerank_query(query: str, texts: list[str]) -> dict[int, float]:
    """Score all texts against one query via qwen3. Fail-open → {}."""
    try:
        from rugmunchbot._vendor.rag_pkg.rerank import rerank_results

        results = await rerank_results(query, texts, top_n=len(texts))
    except Exception as exc:  # any failure degrades to an empty score map
        logger.warning("importance:rerank_query_failed query=%s err=%s", query[:60], str(exc)[:160])
        return {}
    return {int(item["index"]): float(item.get("relevance_score", 0.0)) for item in results}


async def _score_batch(
    batch: list[NewsArticle], queries: list[str]
) -> tuple[list[ScoredArticle], bool]:
    """Score one bounded batch; returns (scored, reranker_answered)."""
    texts = [_article_text(a) for a in batch]
    # Breaker tripped: skip rerank entirely, go straight to the heuristic so
    # the tick never waits on a known-slow reranker.
    if _reranker_tripped():
        logger.debug("importance:reranker_breaker_skip fallback=heuristic")
        return _heuristic_scored(batch), False
    per_query: list[dict[int, float]] = await asyncio.gather(*(_rerank_query(q, texts) for q in queries))
    if not any(per_query):
        return _heuristic_scored(batch), False
    scored: list[ScoredArticle] = []
    for idx, article in enumerate(batch):
        best_score, best_query = 0.0, ""
        for query, scores in zip(queries, per_query, strict=True):
            val = scores.get(idx, 0.0)
            if val > best_score:
                best_score, best_query = val, query
        scored.append(
            ScoredArticle(article=article, importance=round(best_score, 4), best_query=best_query)
        )
    return scored, True


async def score_importance(
    articles: list[NewsArticle],
    focus_keywords: list[str] | tuple[str, ...] | None = None,
) -> list[ScoredArticle]:
    """Rerank *articles* against the importance queries — EVERY article.

    Returns one ``ScoredArticle`` per input article, in input order; composite
    importance is the max relevance across queries. When qwen3 is unreachable,
    every article is scored by the heuristic and ``fallback=True``.

    Batches are chunked by ``MAX_ARTICLES_PER_BATCH`` and ALL chunks are
    scored: the old ``articles[:MAX_ARTICLES_PER_BATCH]`` slice silently
    dropped every story past #100, so a wide sweep persisted and RAG-embedded
    stories that could never be routed or posted. Breaker bookkeeping is per
    call (not per chunk), so one big sweep cannot trip it repeatedly.
    """
    global _consecutive_reranker_failures, _reranker_cooldown_until
    if not articles:
        return []
    queries = _build_queries(focus_keywords)
    scored: list[ScoredArticle] = []
    answered = False
    for start in range(0, len(articles), MAX_ARTICLES_PER_BATCH):
        part, batch_answered = await _score_batch(
            articles[start : start + MAX_ARTICLES_PER_BATCH], queries
        )
        scored.extend(part)
        answered = answered or batch_answered
    if len(articles) > MAX_ARTICLES_PER_BATCH:
        logger.info("importance:batched articles=%s", len(articles))
    if answered:
        if _consecutive_reranker_failures:
            _consecutive_reranker_failures = 0
            _reranker_cooldown_until = 0.0
            logger.warning("importance:reranker_breaker_reset")
        return scored
    _consecutive_reranker_failures += 1
    logger.warning(
        "importance:reranker_unavailable fallback=heuristic consecutive_failures=%s",
        _consecutive_reranker_failures,
    )
    if _consecutive_reranker_failures >= RERANKER_FAILURE_THRESHOLD:
        _reranker_cooldown_until = time.monotonic() + RERANKER_COOLDOWN_SECONDS
        logger.warning(
            "importance:reranker_breaker_tripped cooldown_seconds=%s fallback=heuristic",
            RERANKER_COOLDOWN_SECONDS,
        )
    return scored


def select_important(scored: list[ScoredArticle], top_n: int = 5) -> list[ScoredArticle]:
    """Return the articles worth posting, best first, capped at *top_n*.

    Rerank mode: keep ``importance >= IMPORTANCE_THRESHOLD``, best first.
    Fallback mode (reranker down): top-N by heuristic score, threshold
    bypassed so news still flows. Never invents content.
    """
    if not scored:
        return []
    if scored[0].fallback:
        ordered = sorted(scored, key=lambda s: s.importance, reverse=True)
    else:
        ordered = sorted(
            (s for s in scored if s.importance >= IMPORTANCE_THRESHOLD),
            key=lambda s: s.importance,
            reverse=True,
        )
    return ordered[: max(1, top_n)]


# ── Lightweight market-awareness snapshot helpers (pure) ──────────────────


def top_headlines(scored: list[ScoredArticle], top_n: int = 5) -> list[NewsArticle]:
    """Deterministic top-N headlines by rerank importance, best first.

    Pure selection over an already-scored list — no network, no DB. Used by
    the sweep's market-awareness snapshot (``telegram_news.py``) so the
    bot's chat can pull "what's moving right now" without re-scoring.
    """
    if not scored:
        return []
    ordered = sorted(scored, key=lambda s: s.importance, reverse=True)
    return [s.article for s in ordered[: max(1, top_n)] if s.article is not None]


def trending_tokens(articles: list[NewsArticle], top_n: int = 8) -> list[str]:
    """Most-mentioned ``$SYMBOL`` tokens across a batch of articles.

    Counts the ``$TICKER`` entities already extracted at normalize time
    (``NewsArticle.entities``), most-mentioned first, alphabetical tiebreak.
    Pure and dependency-free. Returns ``[]`` when nothing is mentioned.
    """
    counts: Counter[str] = Counter()
    for article in articles or []:
        for entity in article.entities or []:
            if entity.startswith("$"):
                counts[entity[1:].upper()] += 1
    if not counts:
        return []
    return [token for token, _ in counts.most_common(max(1, top_n))]


__all__ = [
    "DEFAULT_IMPORTANCE_QUERIES",
    "FOCUS_KEYWORDS",
    "IMPORTANCE_QUERIES",
    "IMPORTANCE_THRESHOLD",
    "MAX_ARTICLES_PER_BATCH",
    "ScoredArticle",
    "score_importance",
    "select_important",
    "top_headlines",
    "trending_tokens",
]

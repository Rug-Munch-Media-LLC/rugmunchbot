"""News rating adapter — OSS fail-open.

The live bot scores article sentiment via a private ratings model
(``app.domains.news.ratings.rate_article``). OSS returns a neutral rating
(``sentiment_score=0.0``) so importance scoring keeps its heuristic base.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ArticleRating:
    sentiment_score: float = 0.0
    label: str = "neutral"
    bias: str = "neutral"
    sentiment_label: str = "neutral"
    factual_rating: str = "unrated"
    source_tier: str = "unknown"
    confidence: float = 0.0


def rate_article(title: str, content: str, source: str = "") -> ArticleRating:
    """Return a neutral rating (never asserts sentiment in OSS)."""
    return ArticleRating()


__all__ = ["ArticleRating", "rate_article"]

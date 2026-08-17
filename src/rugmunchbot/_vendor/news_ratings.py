# VENDORED 2026-08-17 from rmi-backend/app/domains/news/ratings.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Ground News-style article rating system.

Every article gets:
- Bias Rating: Left/Center/Right (for political/tech bias)
- Sentiment Score: -100 to +100 (bearish to bullish for crypto news)
- Factual Rating: High/Medium/Low (based on source credibility)
- Source Tier: 1 (primary/verified) / 2 (established) / 3 (aggregator)
"""

from __future__ import annotations

from typing import Any, Final, Literal, cast

__all__ = [
    "BIAS_COLORS",
    "BIAS_LABELS",
    "FACTUAL_LABELS",
    "SOURCE_CREDIBILITY",
    "TIER_LABELS",
    "ArticleRating",
    "analyze_sentiment_score",
    "enrich_article",
    "enrich_articles",
    "rate_article",
]

from pydantic import BaseModel, Field

# ── Pydantic Model ──────────────────────────────────────────────────


class ArticleRating(BaseModel):
    """Comprehensive rating for a single news article."""

    bias: Literal["left", "center-left", "center", "center-right", "right"] = "center"
    sentiment_score: float = Field(default=0.0, ge=-100.0, le=100.0)
    factual_rating: Literal["high", "medium", "low", "unrated"] = "unrated"
    source_tier: int = Field(default=3, ge=1, le=3)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    sentiment_label: Literal["bullish", "slightly_bullish", "neutral", "slightly_bearish", "bearish"] = "neutral"


# ── Source Credibility Database (50+ crypto & tech sources) ─────────

SourceCred = dict[str, Any]

SOURCE_CREDIBILITY: Final[dict[str, SourceCred]] = {
    # Tier 1 — Primary / Verified (high factual, established)
    "coindesk": {"tier": 1, "bias": "center", "factual": "high"},
    "the block": {"tier": 1, "bias": "center", "factual": "high"},
    "cointelegraph": {"tier": 1, "bias": "center", "factual": "high"},
    "cointelegraph.com": {"tier": 1, "bias": "center", "factual": "high"},
    "bloomberg": {"tier": 1, "bias": "center-left", "factual": "high"},
    "reuters": {"tier": 1, "bias": "center", "factual": "high"},
    "ap news": {"tier": 1, "bias": "center", "factual": "high"},
    "financial times": {"tier": 1, "bias": "center", "factual": "high"},
    "wsj": {"tier": 1, "bias": "center-right", "factual": "high"},
    "the wall street journal": {"tier": 1, "bias": "center-right", "factual": "high"},
    "nytimes": {"tier": 1, "bias": "center-left", "factual": "high"},
    "the new york times": {"tier": 1, "bias": "center-left", "factual": "high"},
    "wired": {"tier": 1, "bias": "center-left", "factual": "high"},
    "bbc": {"tier": 1, "bias": "center", "factual": "high"},
    "bbc news": {"tier": 1, "bias": "center", "factual": "high"},
    "npr": {"tier": 1, "bias": "center-left", "factual": "high"},
    "the guardian": {"tier": 1, "bias": "center-left", "factual": "high"},
    "peck shield": {"tier": 1, "bias": "center", "factual": "high"},
    "peckshield": {"tier": 1, "bias": "center", "factual": "high"},
    "slowmist": {"tier": 1, "bias": "center", "factual": "high"},
    "trail of bits": {"tier": 1, "bias": "center", "factual": "high"},
    "certik": {"tier": 1, "bias": "center", "factual": "high"},
    "immunefi": {"tier": 1, "bias": "center", "factual": "high"},
    "openzeppelin": {"tier": 1, "bias": "center", "factual": "high"},
    "chainalysis": {"tier": 1, "bias": "center", "factual": "high"},
    # Tier 2 — Established / trusted names
    "decrypt": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "decrypt.co": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "blockworks": {"tier": 2, "bias": "center", "factual": "medium"},
    "the defiant": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "thedefiant": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "bankless": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "bitcoin magazine": {"tier": 2, "bias": "center-right", "factual": "medium"},
    "protos": {"tier": 2, "bias": "center", "factual": "medium"},
    "beincrypto": {"tier": 2, "bias": "center", "factual": "medium"},
    "unchained": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "cryptoslate": {"tier": 2, "bias": "center", "factual": "medium"},
    "techcrunch": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "the verge": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "crypto.news": {"tier": 2, "bias": "center", "factual": "medium"},
    "cryptonews": {"tier": 2, "bias": "center", "factual": "medium"},
    "u.today": {"tier": 2, "bias": "center", "factual": "medium"},
    "insidebitcoins": {"tier": 2, "bias": "center", "factual": "medium"},
    "coinpedia": {"tier": 2, "bias": "center", "factual": "medium"},
    "coinspeaker": {"tier": 2, "bias": "center", "factual": "medium"},
    "cryptopolitan": {"tier": 2, "bias": "center", "factual": "medium"},
    "finbold": {"tier": 2, "bias": "center", "factual": "medium"},
    "coinjournal": {"tier": 2, "bias": "center", "factual": "medium"},
    "blockonomi": {"tier": 2, "bias": "center", "factual": "medium"},
    "dailycoin": {"tier": 2, "bias": "center", "factual": "medium"},
    "coinmarketcap": {"tier": 2, "bias": "center", "factual": "medium"},
    "coingecko": {"tier": 2, "bias": "center", "factual": "medium"},
    "glassnode": {"tier": 2, "bias": "center", "factual": "medium"},
    "messari": {"tier": 2, "bias": "center", "factual": "medium"},
    "nansen": {"tier": 2, "bias": "center", "factual": "medium"},
    "kaiko": {"tier": 2, "bias": "center", "factual": "medium"},
    "dune analytics": {"tier": 2, "bias": "center", "factual": "medium"},
    "defillama": {"tier": 2, "bias": "center", "factual": "medium"},
    "tokenterminal": {"tier": 2, "bias": "center", "factual": "medium"},
    "a16z crypto": {"tier": 2, "bias": "center-right", "factual": "medium"},
    "paradigm": {"tier": 2, "bias": "center-right", "factual": "medium"},
    "multicoin capital": {"tier": 2, "bias": "center-right", "factual": "medium"},
    "variant fund": {"tier": 2, "bias": "center-right", "factual": "medium"},
    "dragonfly": {"tier": 2, "bias": "center-right", "factual": "medium"},
    "pantera capital": {"tier": 2, "bias": "center-right", "factual": "medium"},
    "ethereum foundation": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "solana": {"tier": 2, "bias": "center", "factual": "medium"},
    "kraken": {"tier": 2, "bias": "center", "factual": "medium"},
    "coinbase": {"tier": 2, "bias": "center-left", "factual": "medium"},
    "binance": {"tier": 2, "bias": "center", "factual": "medium"},
    "sec": {"tier": 1, "bias": "center", "factual": "high"},
    "cftc": {"tier": 1, "bias": "center", "factual": "high"},
    "fatf": {"tier": 1, "bias": "center", "factual": "high"},
    # Tier 3 — Aggregators / less verified
    "reddit": {"tier": 3, "bias": "center", "factual": "low"},
    "twitter": {"tier": 3, "bias": "center", "factual": "low"},
    "google news": {"tier": 3, "bias": "center", "factual": "low"},
    "crypto panic": {"tier": 3, "bias": "center", "factual": "low"},
    "cryptopanic": {"tier": 3, "bias": "center", "factual": "low"},
    "hacker news": {"tier": 3, "bias": "center-left", "factual": "low"},
    "nostr": {"tier": 3, "bias": "center", "factual": "low"},
    "farcaster": {"tier": 3, "bias": "center-left", "factual": "low"},
    "bluesky": {"tier": 3, "bias": "center-left", "factual": "low"},
    "mastodon": {"tier": 3, "bias": "center-left", "factual": "low"},
    "nulltx": {"tier": 3, "bias": "center", "factual": "low"},
    "cryptodaily": {"tier": 3, "bias": "center", "factual": "low"},
}

# ── Sentiment Keywords (extended for -100 to +100 scoring) ─────────

BULLISH_KEYWORDS: Final[list[str]] = [
    "surge",
    "soar",
    "rally",
    "bullish",
    "breakout",
    "pump",
    "moon",
    "ath",
    "all-time high",
    "record high",
    "adoption",
    "launch",
    "partnership",
    "upgrade",
    "milestone",
    "growth",
    "accumulation",
    "green",
    "profit",
    "gain",
    "positive",
    "optimistic",
    "innovation",
    "mainstream",
    "institutional",
    "approval",
    "etf",
    "breakthrough",
    "billions",
    "outperform",
    "beat estimate",
    "upgrade",
    "bull run",
    "hodl",
    "accumulate",
    "oversold",
    "rebound",
    "recovery",
]

BEARISH_KEYWORDS: Final[list[str]] = [
    "crash",
    "dump",
    "bearish",
    "hack",
    "exploit",
    "scam",
    "rug",
    "drain",
    "loss",
    "decline",
    "sell-off",
    "liquidation",
    "collapse",
    "default",
    "bankrupt",
    "freeze",
    "halt",
    "suspend",
    "delist",
    "sec",
    "lawsuit",
    "fine",
    "penalty",
    "ban",
    "restrict",
    "phishing",
    "vulnerability",
    "attack",
    "stolen",
    "breach",
    "warning",
    "alert",
    "risk",
    "volatile",
    "uncertainty",
    "fear",
    "downgrade",
    "sell",
    "overbought",
    "correction",
    "bear trap",
    "fud",
    "panic",
    "bloodbath",
    "wipeout",
    "negative",
]

STRONG_BULLISH: Final[list[str]] = [
    "ath",
    "all-time high",
    "breakout",
    "surge",
    "soar",
    "moon",
    "record",
    "milestone",
    "breakthrough",
]

STRONG_BEARISH: Final[list[str]] = [
    "crash",
    "collapse",
    "bloodbath",
    "wipeout",
    "exploit",
    "hack",
    "stolen",
    "breach",
    "liquidation",
    "bankrupt",
]


def _normalize_source(source: str | None) -> str:
    """Normalize source name for lookup."""
    if not source:
        return ""
    return source.lower().strip().replace("_", " ").replace("-", " ")


def _lookup_source(source: str) -> SourceCred | None:
    """Look up source in credibility database with fuzzy matching."""
    key = _normalize_source(source)
    if not key:
        return None

    # Exact match first
    if key in SOURCE_CREDIBILITY:
        return SOURCE_CREDIBILITY[key]

    # Partial match (source name contains a known key or vice versa)
    for known_key, cred in SOURCE_CREDIBILITY.items():
        if known_key in key or key in known_key:
            return cred

    return None


def _log_confidence_bias(tier: int, source: str) -> tuple[float, str, str]:
    """Calculate confidence and bias from source lookup."""
    cred = _lookup_source(source)
    if cred:
        bias = cred["bias"]
        factual = cred["factual"]
        confidence = 0.85 if cred["tier"] == 1 else (0.7 if cred["tier"] == 2 else 0.5)
        return confidence, bias, factual

    # Unknown source: assign based on tier hint
    tier_map = {1: ("center", "high", 0.8), 2: ("center", "medium", 0.6), 3: ("center", "low", 0.4)}
    bias, factual, conf = tier_map.get(tier, ("center", "unrated", 0.3))
    return conf, bias, factual


def analyze_sentiment_score(text: str | None) -> tuple[float, str]:
    """Analyze sentiment of text. Returns (score: -100 to +100, label).

    Uses weighted keyword scoring for fast runtime with no external API.
    """
    if not text:
        return 0.0, "neutral"

    text_lower = text.lower()
    score = 0.0

    # Strong signals (±15 per match)
    for word in STRONG_BULLISH:
        count = text_lower.count(word)
        score += count * 15.0

    for word in STRONG_BEARISH:
        count = text_lower.count(word)
        score -= count * 15.0

    # Normal signals (±6 per match)
    for word in BULLISH_KEYWORDS:
        count = text_lower.count(word)
        score += count * 6.0

    for word in BEARISH_KEYWORDS:
        count = text_lower.count(word)
        score -= count * 6.0

    # Clamp
    score = max(-100.0, min(100.0, score))

    # Label
    if score >= 30:
        label: Literal["bullish", "slightly_bullish", "neutral", "slightly_bearish", "bearish"] = "bullish"
    elif score >= 10:
        label = "slightly_bullish"
    elif score <= -30:
        label = "bearish"
    elif score <= -10:
        label = "slightly_bearish"
    else:
        label = "neutral"

    return score, label


def rate_article(
    title: str | None = None,
    content: str | None = None,
    source: str | None = None,
    source_tier: int = 3,
) -> ArticleRating:
    """Rate a news article with bias, sentiment, factual, and tier info.

    Args:
        title: Article headline (primary sentiment signal).
        content: Article body/summary (secondary sentiment signal).
        source: Source name for credibility lookup.
        source_tier: Fallback tier if source not in DB.

    Returns:
        ArticleRating with all fields populated.
    """
    # Source credibility
    confidence, bias, factual_rating = _log_confidence_bias(source_tier, source or "")

    # Sentiment analysis
    text = f"{title or ''} {content or ''}"
    sentiment_score, sentiment_label = analyze_sentiment_score(text)

    # Look up actual source tier if known
    cred = _lookup_source(source or "")
    effective_tier = cred["tier"] if cred else source_tier

    return ArticleRating(
        bias=cast("Literal['left', 'center-left', 'center', 'center-right', 'right']", bias),
        sentiment_score=sentiment_score,
        sentiment_label=cast(
            "Literal['bullish', 'slightly_bullish', 'neutral', 'slightly_bearish', 'bearish']", sentiment_label
        ),
        factual_rating=cast("Literal['high', 'medium', 'low', 'unrated']", factual_rating),
        source_tier=effective_tier,
        confidence=confidence,
    )


def enrich_article(article: dict[str, Any]) -> dict[str, Any]:
    """Enrich an article dict with rating fields in-place and return it."""
    rating = rate_article(
        title=article.get("title"),
        content=article.get("excerpt") or article.get("summary") or article.get("description"),
        source=article.get("source") or article.get("publisher"),
        source_tier=article.get("source_tier", 3),
    )

    article["bias"] = rating.bias
    article["sentiment_score"] = rating.sentiment_score
    article["sentiment_label"] = rating.sentiment_label
    article["factual_rating"] = rating.factual_rating
    article["rating_confidence"] = rating.confidence
    article["source_tier"] = rating.source_tier

    return article


def enrich_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich a list of articles with ratings."""
    return [enrich_article(a) for a in articles]


# ── Quick lookup helpers for the frontend ──────────────────────────

BIAS_COLORS: Final[dict[str, str]] = {
    "left": "#dc2626",
    "center-left": "#f97316",
    "center": "#6b7280",
    "center-right": "#3b82f6",
    "right": "#2563eb",
}

BIAS_LABELS: Final[dict[str, str]] = {
    "left": "Left",
    "center-left": "Lean Left",
    "center": "Center",
    "center-right": "Lean Right",
    "right": "Right",
}

TIER_LABELS: Final[dict[int, str]] = {
    1: "Tier 1",
    2: "Tier 2",
    3: "Tier 3",
}

FACTUAL_LABELS: Final[dict[str, tuple[str, str]]] = {
    "high": ("High", "#22c55e"),
    "medium": ("Medium", "#eab308"),
    "low": ("Low", "#ef4444"),
    "unrated": ("Unrated", "#6b7280"),
}

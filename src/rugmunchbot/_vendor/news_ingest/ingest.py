# VENDORED 2026-08-24 from rmi-backend/app/domains/news_ingest/ingest.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Channel-monitoring ingestion: read → normalize → dedupe → rank.

``read_channel_messages`` is a thin async reader over the headless Telegram
client. It never edits ``app.domains.telegram.headless_client`` and never
raises — any failure degrades to an empty list so a single flaky channel can
not take down the whole sweep.

The dedup layers are URL / normalized-title aware so the same story that is
rephrased across channels (or re-posted with a slightly different body)
collapses into a single article instead of repeating:

1. **Canonical source URL** — the first non-``t.me`` http(s) URL inside the
   message (the original article link) is the strongest "same story" signal.
2. **Channel URL** — the stable ``https://t.me/<channel>/<message_id>`` link.
3. **Normalized title** — lowercase, punctuation/emoji stripped, first 60
   chars (only used when the title is substantive, >= 20 chars).
4. **Content hash** — last resort, raw ``content_hash`` (RAG-compatible).

The same key set is used for the in-batch dedup (``dedupe_articles``) and the
cross-run Redis dedup (``filter_seen_articles`` / ``mark_articles_ingested``),
so a story seen in a previous sweep — even rephrased — is never re-read.
"""

from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final

import httpx

from rugmunchbot._vendor.dedup import content_hash, url_hash
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle
from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article, extract_entities

logger = get_logger(__name__)

# Injectable reader protocol: async (channel_username, limit) -> messages.
ReadMessagesCallable = Callable[[str, int], Awaitable[list[ChannelMessage]]]

#: Redis dedup-set namespace for TG-sourced articles. Reuses the shared
#: ``app.rag.dedup`` ZSETs (``rag:dedup:content:<collection>``) so the news
#: sweep and the RAG pipeline never re-embed content the other already saw.
DEFAULT_DEDUP_COLLECTION: str = "tg_news"


# ── Dedup (pure, no Redis) ────────────────────────────────────────────────

#: Minimum normalized-title length before it is trusted as a dedup key.
#: Shorter titles ("market update") are too generic to be a story fingerprint.
_MIN_TITLE_KEY_CHARS: Final = 20
#: Normalized-title truncation length (lowercase, punctuation stripped).
_TITLE_KEY_CHARS: Final = 60

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TME_LINK_RE = re.compile(r"^https?://(?:www\.)?t\.me/", re.IGNORECASE)
_IMAGE_URL_RE = re.compile(
    r"https?://[^\s<>\"']+\.(?:jpe?g|png|webp|gif|avif)(?:\?[^\s<>\"']*)?",
    re.IGNORECASE,
)
#: Leading breaking markers stripped from a normalized title so "BREAKING: X"
#: and "X" (the same story) share one dedup key.
_NORM_BREAKING_PREFIX_RE = re.compile(r"^(?:breaking|urgent|just in|flash)\s+", re.IGNORECASE)

#: Prefixes that keep news-ingest keys distinct inside the shared Redis
#: ``rag:dedup:content:<collection>`` ZSET (the raw content hash stays
#: unprefixed so RAG-pipeline marks keep matching).
_DEDUP_KEY_URL = "url:"
_DEDUP_KEY_TITLE = "title:"


def _clean_url(url: str) -> str:
    """Trim trailing punctuation that URL regexes tend to swallow."""
    return url.rstrip(".,;:!?)]}>\"'")


def extract_canonical_url(article: NewsArticle) -> str:
    """First non-``t.me`` http(s) URL inside an article (title first, then body).

    The canonical source URL is the strongest "same story" signal: every
    channel that forwards/paraphrases the same piece usually keeps the
    original article link. ``t.me`` links are skipped — they are the channel's
    own share link, not the story. Returns ``""`` when nothing usable.
    """
    if article is None:
        return ""
    for text in (article.title, article.body):
        for match in _URL_RE.finditer(text or ""):
            url = _clean_url(match.group(0))
            if not url or _TME_LINK_RE.match(url):
                continue
            return url
    return ""


def extract_image_url(article: NewsArticle) -> str:
    """First inline image URL (``.jpg/.png/.webp/...``) in the message text.

    Some news channels embed the article's hero image directly in the post
    (``Image: https://.../photo.jpg``). Best-effort — returns ``""`` when the
    text carries no image link (photo *media* on the Telegram message itself
    is not exposed here; see [REVIEW] notes in the channel module).
    """
    if article is None:
        return ""
    for text in (article.title, article.body):
        for match in _IMAGE_URL_RE.finditer(text or ""):
            url = _clean_url(match.group(0))
            if url:
                return url
    return ""


def normalize_title(title: str) -> str:
    """Canonical title form for dedup: lowercase, punctuation/emoji stripped.

    Removes everything except word chars / whitespace / ``$`` (so ``$BTC``
    survives), strips a leading breaking marker (``BREAKING:`` / ``URGENT`` /
    ``JUST IN`` / ``FLASH`` — same story with or without the marker), then
    collapses whitespace and truncates to ``_TITLE_KEY_CHARS``.
    """
    if not title:
        return ""
    text = title.lower()
    text = re.sub(r"[^\w\s$]", " ", text)  # emoji/punct are non-word chars
    text = re.sub(r"\s+", " ", text).strip()
    text = _NORM_BREAKING_PREFIX_RE.sub("", text)
    return text[:_TITLE_KEY_CHARS]


def article_dedup_keys(article: NewsArticle) -> list[str]:
    """Stable dedup keys for an article, strongest first.

    Order: canonical source URL → channel t.me URL → normalized title →
    raw content hash (unprefixed, shared with the RAG pipeline). Callers
    (in-batch + cross-run dedup) treat an article as a duplicate when ANY of
    its keys has been seen before — so a rephrased story that keeps its
    original URL or headline collapses, while a genuinely new story passes.
    """
    if article is None:
        return []
    keys: list[str] = []
    canonical = extract_canonical_url(article)
    if canonical:
        keys.append(f"{_DEDUP_KEY_URL}{url_hash(canonical)}")
    if article.source_url:
        keys.append(f"{_DEDUP_KEY_URL}{url_hash(article.source_url)}")
    norm_title = normalize_title(article.title)
    if len(norm_title) >= _MIN_TITLE_KEY_CHARS:
        keys.append(f"{_DEDUP_KEY_TITLE}{content_hash(norm_title)}")
    fp = article.content_hash or content_hash(f"{article.title}\n{article.body}")
    if fp:
        keys.append(fp)  # raw content hash — stays RAG-pipeline compatible
    # Dedupe within the list (a URL may repeat when title/body repeat it).
    return list(dict.fromkeys(keys))


def dedupe_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    """First-wins multi-key dedup (canonical URL / t.me URL / title / hash).

    Uses the pure ``app.rag.dedup`` helpers (``content_hash`` / ``url_hash``)
    so batch dedup needs no Redis. Preserves input order; an article whose
    canonical URL, channel URL, normalized title, or content hash matches an
    earlier article is dropped — this collapses the same story forwarded
    across channels even when each channel rephrases the body.
    """
    seen: set[str] = set()
    result: list[NewsArticle] = []
    for article in articles or []:
        keys = article_dedup_keys(article)
        if not keys:
            continue
        if any(key in seen for key in keys):
            continue
        seen.update(keys)
        result.append(article)
    return result


def _article_fingerprints(article: NewsArticle) -> list[str]:
    """Stable dedup fingerprints for the shared Redis cross-run sets.

    Mirrors :func:`article_dedup_keys` (canonical URL → t.me URL → title →
    raw content hash) so ``filter_seen_articles`` / ``mark_articles_ingested``
    key on the URL/title, not just the raw content hash — a rephrased story
    from a previous sweep is still recognized.
    """
    return article_dedup_keys(article)


def filter_seen_articles(
    articles: list[NewsArticle], collection: str = DEFAULT_DEDUP_COLLECTION
) -> tuple[list[NewsArticle], int]:
    """Drop articles already recorded in the shared Redis content-hash sets.

    Check-only (never marks): callers mark with :func:`mark_articles_ingested`
    only after the article was actually persisted, so a transient Postgres
    outage never permanently hides an un-persisted story.

    Each article is checked against its **full key set** (canonical URL →
    t.me URL → normalized title → raw content hash); any key already present
    in the collection marks the story as seen. Reuses ``app.rag.dedup``'s
    ``is_duplicate_content`` — the same Redis ZSET the RAG pipeline maintains
    (``rag:dedup:content:tg_news``) — under a dedicated *collection*
    namespace. Fail-open: Redis down / auth-failure treats every article as
    fresh, matching the rest of the ingest layer.

    Returns ``(fresh, skipped)``; ``fresh`` preserves input order.
    """
    from rugmunchbot._vendor.dedup import is_duplicate_content

    fresh: list[NewsArticle] = []
    skipped = 0
    for article in articles or []:
        fps = _article_fingerprints(article)
        if not fps:
            fresh.append(article)
            continue
        duplicate = False
    for fp in fps:
        try:  # fail-open by contract: dedup must never raise or drop articles
            if is_duplicate_content(fp, collection):
                duplicate = True
                break
        except Exception as exc:
            logger.debug(
                "news_ingest:dedup_check_failed fp=%s error=%s", str(fp)[:50], str(exc)[:100]
            )
        if duplicate:
            skipped += 1
            continue
        fresh.append(article)
    if skipped:
        logger.info(
            "news_ingest:seen_skipped collection=%s fresh=%s skipped=%s",
            collection,
            len(fresh),
            skipped,
        )
    return fresh, skipped


def mark_articles_ingested(
    articles: list[NewsArticle], collection: str = DEFAULT_DEDUP_COLLECTION
) -> int:
    """Record article dedup keys in the shared Redis sets.

    Best-effort (never raises): the ``app.rag.dedup`` markers are fail-open,
    so a Redis outage only means the next sweep re-reads the same content —
    the Postgres ``news_id`` guard still blocks duplicate persistence.

    Marks **every** key of the article (URL / title / content hash) so a
    later rephrase of the same story is caught by the cross-run check.
    Returns the number of articles marked.
    """
    from rugmunchbot._vendor.dedup import mark_content_ingested

    marked = 0
    for article in articles or []:
        fps = _article_fingerprints(article)
        if not fps:
            continue
        for fp in fps:
            mark_content_ingested(fp, collection)
        marked += 1
    return marked


# ── Lightweight importance + breaking detection (pure, no network) ─────────

#: Source-authority hints — direct outlets outrank aggregators/repackagers.
_HIGH_AUTHORITY_HINTS: Final = (
    "cointelegraph",
    "coindesk",
    "theblock",
    "decrypt",
    "bitcoin magazine",
    "blockworks",
    "the defiant",
    "forbes",
    "reuters",
    "bloomberg",
    "wall street journal",
    "wsj",
    "associated press",
    "apnews",
    "financial times",
)
#: Aggregator/repackager channels — lower authority than primary outlets.
_LOW_AUTHORITY_HINTS: Final = (
    "watcher",
    "cryptoslate",
    "cryptonews",
    "wu blockchain",
    "wublockchain",
    "panews",
    "cryptopanic",
    "coinmarketcap",
    "coingecko",
    "daily hodl",
    "altcoin buzz",
)

#: Title-level breaking markers → post the story solo (high priority).
_BREAKING_TITLE_RE = re.compile(r"(?:🚨|\b(?:breaking|urgent|just in|flash)\b)", re.IGNORECASE)
#: Body-level urgency markers → a strong importance bump, but not solo-worthy.
_BREAKING_ANY_RE = re.compile(
    r"(?:🚨|\b(?:breaking|urgent|just in|flash|alert|developing)\b)",
    re.IGNORECASE,
)

#: Crypto-relevance keywords — market-moving vocabulary over generic tech.
_CRYPTO_KEYWORD_RE = re.compile(
    r"\b(?:bitcoin|btc|ethereum|eth|solana|etf|stablecoin|blackrock|sec|fomc|"
    r"federal reserve|fed|halving|all[- ]time high|institutional|"
    r"liquidation|hack|exploit|rug|drained|stolen|approves|rejects|delist|"
    r"cbdc|regulation|lawsuit|crash)\b",
    re.IGNORECASE,
)


def _as_utc(value: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_breaking_article(article: NewsArticle) -> bool:
    """True when the *title* carries an explicit breaking marker.

    Deterministic and cheap: checks the title (the first line the channel
    posts) for 🚨 / BREAKING / URGENT / JUST IN / FLASH. Used by the channel
    router to decide between a solo breaking post and a multi-story digest.
    """
    if article is None:
        return False
    return bool(_BREAKING_TITLE_RE.search(article.title or ""))


def lightweight_importance(article: NewsArticle, now: datetime | None = None) -> float:
    """Deterministic 0-1 importance score (no network, no reranker).

    Combines cheap, explainable signals — the same axes the cross-encoder
    reranker is calibrated on, usable everywhere (ordering, website feed):

    * source authority — cointelegraph/coindesk/theblock & co. outrank
      aggregator/repackager channels;
    * breaking markers in the title (strong) or body (weak);
    * crypto-relevance keyword density in the title (strong) and body (weak);
    * freshness — newer posts score higher (age windows: <1h, <3h).

    Fail-open: any missing field (no ``posted_at``, empty body) degrades to
    the remaining signals; never raises. Returns a value in ``[0, 1]``.
    """
    if article is None:
        return 0.0
    score = 0.2  # baseline — every normalized article is minimally relevant
    title = article.title or ""
    body = article.body or ""

    if is_breaking_article(article):
        score += 0.25
    elif _BREAKING_ANY_RE.search(f"{title} {body}"):
        score += 0.12

    source = (article.source_channel or "").lower()
    if any(hint in source for hint in _HIGH_AUTHORITY_HINTS):
        score += 0.25
    elif any(hint in source for hint in _LOW_AUTHORITY_HINTS):
        score += 0.0  # aggregator — no authority bump
    else:
        score += 0.12  # unknown source — neutral

    title_hits = len(_CRYPTO_KEYWORD_RE.findall(title))
    body_hits = len(_CRYPTO_KEYWORD_RE.findall(body))
    score += 0.1 * min(title_hits, 3) + 0.03 * min(body_hits, 5)

    if article.posted_at is not None:
        try:
            age_hours = (
                _as_utc(article.posted_at) - _as_utc(now or datetime.now(UTC))
            ).total_seconds() / 3600.0
            if age_hours < 1:
                score += 0.1
            elif age_hours < 3:
                score += 0.05
        except (TypeError, ValueError, OverflowError):
            pass  # freshness is a bonus, never fatal

    return round(min(1.0, max(0.0, score)), 4)


def rank_articles(articles: list[NewsArticle], now: datetime | None = None) -> list[NewsArticle]:
    """Return *articles* sorted by lightweight importance, best first.

    Stable sort: articles with equal scores keep their input order, so the
    digest is deterministic. Pure and dependency-free.
    """
    if not articles:
        return []
    return sorted(
        articles,
        key=lambda article: lightweight_importance(article, now=now),
        reverse=True,
    )


# ── Content quality scoring (2026-09-04) ───────────────────────────────────
# Multiplicative quality model for the RAG content pipeline. Each factor is a
# multiplier around 1.0; the final score is their product, so a story that is
# credible, important, corroborated, diverse, and fresh compounds, while a
# weak story on any single axis is pulled down. Every factor is pure and
# fail-open (missing fields degrade to the remaining signals, never raise).

#: Security-researcher / on-chain-forensics sources — highest trust (1.0).
_SECURITY_RESEARCHER_HINTS: Final = (
    "zachxbt",
    "zachxbt_",
    "peckshield",
    "peckshieldalert",
    "slowmist",
    "slowmist_team",
    "certik",
    "certikalert",
    "rekt",
    "rektnews",
    "cyvers",
    "cyversalerts",
    "blocksec",
    "blocksec_team",
    "chainalysis",
    "elliptic",
    "trmlabs",
    "halborn",
    "immunefi",
)
#: Major news outlets — high trust (0.8).
_MAJOR_NEWS_HINTS: Final = (
    "coindesk",
    "cointelegraph",
    "theblock",
    "bloomberg",
    "reuters",
    "wsj",
    "wall street journal",
    "financial times",
    "associated press",
    "apnews",
    "forbes",
    "decrypt",
    "bitcoin magazine",
    "blockworks",
    "the defiant",
)
#: Blog / independent / aggregator channels — medium trust (0.5).
_BLOG_HINTS: Final = (
    "medium",
    "substack",
    "mirror",
    "blog",
    "wordpress",
    "ghost",
    "hashnode",
    "dev.to",
    "cryptoslate",
    "cryptonews",
    "daily hodl",
    "altcoin buzz",
    "watcher",
    "panews",
    "cryptopanic",
)

#: Exploit / security-critical vocabulary → importance boost.
_EXPLOIT_KEYWORD_RE: Final = re.compile(
    r"\b(?:exploit|exploits|exploited|exploiting|hack|hacks|hacked|hacking|"
    r"drained|drain|drains|draining|rug|rugs|rugpull|rug pull|stolen|steal|"
    r"steals|stole|breach|breaches|breached|vulnerability|vulnerabilities|"
    r"vulnerable|compromised|compromise|attack|attacks|attacked|attacker|"
    r"phishing|scam|scams|scammed|fraud|theft|pulled|liquidity removed)\b",
    re.IGNORECASE,
)

#: Minimum distinct sources before a story counts as corroborated.
_CORROBORATION_MIN_SOURCES: Final = 3
#: Corroboration boost when >= ``_CORROBORATION_MIN_SOURCES`` report the story.
_CORROBORATION_BOOST: Final = 0.2
#: Max share of a feed a single source may own before diversity is penalized.
_DIVERSITY_MAX_SHARE: Final = 0.30
#: Diversity penalty applied to over-represented sources.
_DIVERSITY_PENALTY: Final = 0.2
#: Engagement threshold for the importance boost.
_ENGAGEMENT_THRESHOLD: Final = 50
#: Importance boost for exploit/security-critical articles.
_EXPLOIT_BOOST: Final = 0.3
#: Importance boost for high-engagement articles.
_ENGAGEMENT_BOOST: Final = 0.2


def source_credibility(source_channel: str) -> float:
    """Credibility multiplier for a source channel (1.0 / 0.8 / 0.5 / 0.3).

    Security researchers / on-chain forensics (zachxbt, peckshield, ...) are
    the most trusted (1.0); major news outlets (coindesk, cointelegraph, ...)
    are 0.8; blogs / independent / aggregator channels are 0.5; anything
    unknown is 0.3. Pure and deterministic.
    """
    source = (source_channel or "").lower()
    if any(hint in source for hint in _SECURITY_RESEARCHER_HINTS):
        return 1.0
    if any(hint in source for hint in _MAJOR_NEWS_HINTS):
        return 0.8
    if any(hint in source for hint in _BLOG_HINTS):
        return 0.5
    return 0.3


def _article_engagements(article: NewsArticle) -> int:
    """Best-effort engagement count for an article (duck-typed, fail-open).

    ``NewsArticle`` does not carry views/reactions today (they live on the raw
    ``ChannelMessage``), so this reads optional duck-typed attributes
    (``engagements`` / ``views`` / ``reactions``) and returns 0 when absent.
    """
    if article is None:
        return 0
    for attr in ("engagements", "views"):
        value = getattr(article, attr, None)
        if isinstance(value, int):
            return value
    reactions = getattr(article, "reactions", None)
    if isinstance(reactions, dict):
        return sum(int(v) for v in reactions.values() if isinstance(v, (int, float)))
    return 0


def content_importance(article: NewsArticle) -> float:
    """Importance multiplier for an article (1.0 base, +0.3 / +0.2 boosts).

    Articles mentioning exploit/security-critical vocabulary (exploit, hack,
    drained, rug, ...) get +0.3; articles with >50 engagements get +0.2.
    Returns a multiplier in ``[1.0, 1.5]``. Fail-open: missing fields degrade
    to the remaining signals.
    """
    if article is None:
        return 1.0
    boost = 1.0
    text = f"{article.title or ''} {article.body or ''}"
    if _EXPLOIT_KEYWORD_RE.search(text):
        boost += _EXPLOIT_BOOST
    if _article_engagements(article) > _ENGAGEMENT_THRESHOLD:
        boost += _ENGAGEMENT_BOOST
    return round(boost, 4)


def _story_key(article: NewsArticle) -> str:
    """A stable 'same story' fingerprint for corroboration counting.

    Prefers the canonical source URL (strongest same-story signal), then the
    article's own ``source_url`` when it is a real article link (not a
    ``t.me`` share link), then the normalized title. Returns ``""`` when none
    is usable (cannot corroborate).
    """
    if article is None:
        return ""
    canonical = extract_canonical_url(article)
    if canonical:
        return f"url:{url_hash(canonical)}"
    src_url = (article.source_url or "").strip()
    if src_url and not _TME_LINK_RE.match(src_url):
        return f"url:{url_hash(src_url)}"
    norm = normalize_title(article.title)
    if len(norm) >= _MIN_TITLE_KEY_CHARS:
        return f"title:{content_hash(norm)}"
    return ""


def corroboration_factor(articles: list[NewsArticle], article: NewsArticle) -> float:
    """Corroboration multiplier: +0.2 when 3+ distinct sources report the story.

    Groups *articles* by story fingerprint (canonical URL / normalized title)
    and counts distinct ``source_channel`` values. Returns 1.0 when fewer than
    ``_CORROBORATION_MIN_SOURCES`` distinct sources report it, else 1.2.
    """
    if article is None:
        return 1.0
    key = _story_key(article)
    if not key:
        return 1.0
    sources: set[str] = set()
    for other in articles or []:
        if other is None:
            continue
        if _story_key(other) == key:
            src = (other.source_channel or "").strip().lower()
            if src:
                sources.add(src)
    if len(sources) >= _CORROBORATION_MIN_SOURCES:
        return 1.0 + _CORROBORATION_BOOST
    return 1.0


def diversity_factor(articles: list[NewsArticle], article: NewsArticle) -> float:
    """Diversity multiplier: penalize a source that dominates the feed.

    Computes the share of *articles* that come from the article's source; when
    that share exceeds ``_DIVERSITY_MAX_SHARE`` (30%), the multiplier drops by
    ``_DIVERSITY_PENALTY`` (0.8). Returns 1.0 otherwise. Fail-open: an empty
    feed or unknown source returns 1.0.
    """
    if article is None or not articles:
        return 1.0
    source = (article.source_channel or "").strip().lower()
    if not source:
        return 1.0
    total = len(articles)
    same = sum(
        1
        for other in articles
        if other is not None and (other.source_channel or "").strip().lower() == source
    )
    share = same / total
    if share > _DIVERSITY_MAX_SHARE:
        return 1.0 - _DIVERSITY_PENALTY
    return 1.0


def freshness_factor(article: NewsArticle, now: datetime | None = None) -> float:
    """Freshness multiplier: +0.2 (<1h), +0.1 (1-6h), 1.0 (6-24h), -0.2 (>24h).

    Newer stories score higher; stale stories (>24h) are penalized. Fail-open:
    a missing ``posted_at`` returns 1.0 (no freshness signal).
    """
    if article is None or article.posted_at is None:
        return 1.0
    try:
        age_hours = (
            _as_utc(now or datetime.now(UTC)) - _as_utc(article.posted_at)
        ).total_seconds() / 3600.0
    except (TypeError, ValueError, OverflowError):
        return 1.0
    if age_hours < 1:
        return 1.2
    if age_hours < 6:
        return 1.1
    if age_hours > 24:
        return 0.8
    return 1.0


def content_quality_score(
    articles: list[NewsArticle], article: NewsArticle, now: datetime | None = None
) -> float:
    """Multiplicative content-quality score: credibility x importance x
    corroboration x diversity x freshness.

    Each factor is a multiplier around 1.0; the product is the final score.
    Returns a value in ``[0, ~2.6]`` (all factors bounded). Fail-open: any
    missing field degrades to the remaining factors; never raises.
    """
    if article is None:
        return 0.0
    score = (
        source_credibility(article.source_channel)
        * content_importance(article)
        * corroboration_factor(articles, article)
        * diversity_factor(articles, article)
        * freshness_factor(article, now=now)
    )
    return round(score, 4)


def rank_by_quality(articles: list[NewsArticle], now: datetime | None = None) -> list[NewsArticle]:
    """Return *articles* sorted by content-quality score, best first.

    Stable sort: equal scores keep input order (deterministic digest). This is
    the gate that lets only top-scoring content through to the main channel
    (@cryptorugmuncher). Pure and dependency-free.
    """
    if not articles:
        return []
    return sorted(
        articles,
        key=lambda article: content_quality_score(articles, article, now=now),
        reverse=True,
    )


# ── Telethon message conversion ────────────────────────────────────────────


def _message_to_channel_message(msg: Any, channel_username: str) -> ChannelMessage | None:
    """Convert a Telethon-style message object (duck-typed) to a ChannelMessage."""
    try:
        message_id = getattr(msg, "id", None)
        if message_id is None:
            return None
        text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
        text = text if isinstance(text, str) else str(text)

        chat = getattr(msg, "chat", None)
        channel = ""
        if chat is not None:
            channel = getattr(chat, "title", None) or getattr(chat, "username", None) or ""

        forwarded_from: str | None = None
        fwd = getattr(msg, "fwd_from", None)
        if fwd is not None:
            forwarded_from = getattr(fwd, "from_name", None)
            if forwarded_from is None:
                peer = getattr(fwd, "from_id", None)
                forwarded_from = str(peer) if peer is not None else "forwarded"

        views = getattr(msg, "views", None)
        return ChannelMessage(
            channel=channel or channel_username,
            channel_username=channel_username,
            message_id=int(message_id),
            text=text,
            media_type=_media_type(getattr(msg, "media", None)),
            posted_at=getattr(msg, "date", None),
            forwarded_from=forwarded_from,
            views=int(views) if views is not None else None,
            reactions=_reaction_counts(getattr(msg, "reactions", None)),
        )
    except Exception as exc:
        logger.debug("news_ingest_convert_failed error=%s", str(exc)[:100])
        return None


def _media_type(media: Any) -> str:
    if media is None:
        return "text"
    name = type(media).__name__.lower()
    for kind in ("photo", "video", "document", "audio", "sticker", "gif", "poll", "voice"):
        if kind in name:
            return kind
    return "media"


def _reaction_counts(reactions: Any) -> dict[str, int] | None:
    results = getattr(reactions, "results", None)
    if not results:
        return None
    counts: dict[str, int] = {}
    for item in results:
        reaction = getattr(item, "reaction", None)
        count = int(getattr(item, "count", None) or 0)
        key: Any = None
        if reaction is not None:
            key = (
                getattr(reaction, "emoticon", None)
                or getattr(reaction, "document_id", None)
                or str(reaction)
            )
        if key is not None:
            counts[str(key)] = count
    return counts or None


# ── Thin reader ────────────────────────────────────────────────────────────


#: Consecutive read failures before a channel is auto-disabled (then retried
#: after a cooldown). Guards against spending an MTProto round-trip on a
#: channel that repeatedly fails to resolve ("No user has X as username" for
#: dead/renamed handles) on every sweep tick.
_CHANNEL_FAILURE_THRESHOLD: Final = 3
#: Cooldown after auto-disable before the channel is tried again.
_CHANNEL_COOLDOWN_SECONDS: Final = 24 * 60 * 60

#: channel_username -> consecutive read failures (reset on any success).
_CHANNEL_READ_FAILURES: dict[str, int] = {}
#: channel_username -> monotonic timestamp until which the channel is skipped.
_CHANNEL_COOLDOWN_UNTIL: dict[str, float] = {}

# ── Cooldown persistence (bot-side deviation, 2026-08-25) ──────────────────
# Upstream keeps cooldowns in memory only, so every bot restart re-probes all
# dead channels (3 failures × ~14 dead channels × every sweep = log-noise
# bursts and wasted Telegram RPCs after each deploy). Persist the cooldown
# map to disk; failures-counters stay in-memory (cheap to rebuild).
# Deviation note: upstream app/domains/news_ingest has no persistence yet —
# refresh-vendors.sh would drop this. Re-add after refresh if reverted.
_COOLDOWN_STATE_PATH: Final[str] = os.getenv(
    "TG_NEWS_COOLDOWN_STATE", "/var/lib/rugmunchbot/tg_news_channel_cooldowns.json"
)
_cooldown_state_loaded = False


def _load_cooldown_state() -> None:
    """Load persisted cooldown deadlines once per process (fail-open)."""
    global _cooldown_state_loaded
    _cooldown_state_loaded = True
    try:
        with open(_COOLDOWN_STATE_PATH) as f:
            raw = json.load(f)
        now = time.monotonic()
        for username, deadline in raw.items():
            # Stored as remaining-seconds offsets from save time → rebase.
            until = now + float(deadline)
            if until > now:
                _CHANNEL_COOLDOWN_UNTIL[username] = until
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug("news_ingest_cooldown_state_load_failed err=%s", e)


def _save_cooldown_state() -> None:
    """Persist remaining cooldown seconds per channel atomically (best-effort)."""
    try:
        now = time.monotonic()
        remaining = {
            u: round(until - now, 1) for u, until in _CHANNEL_COOLDOWN_UNTIL.items() if until > now
        }
        os.makedirs(os.path.dirname(_COOLDOWN_STATE_PATH), exist_ok=True)
        tmp = f"{_COOLDOWN_STATE_PATH}.tmp"
        with open(tmp, "w") as f:
            json.dump(remaining, f)
        os.replace(tmp, _COOLDOWN_STATE_PATH)
    except Exception as e:
        logger.debug("news_ingest_cooldown_state_save_failed err=%s", e)


def is_channel_cooldown(channel_username: str, now: float | None = None) -> bool:
    """True while *channel_username* is in auto-disable cooldown (skip the RPC).

    Fail-open: unknown channels are never in cooldown.
    """
    if not channel_username:
        return False
    if not _cooldown_state_loaded:
        _load_cooldown_state()
    until = _CHANNEL_COOLDOWN_UNTIL.get(channel_username)
    if until is None:
        return False
    return (now if now is not None else time.monotonic()) < until


def note_channel_failure(
    channel_username: str,
    *,
    threshold: int = _CHANNEL_FAILURE_THRESHOLD,
    cooldown_seconds: float = _CHANNEL_COOLDOWN_SECONDS,
) -> bool:
    """Record a read failure; auto-disable after *threshold* consecutive ones.

    Returns True when the channel was *just* disabled (caller should log at
    warning). Failures are reset by :func:`note_channel_success`, so a channel
    that recovers after cooldown starts from zero again.
    """
    if not channel_username:
        return False
    count = _CHANNEL_READ_FAILURES.get(channel_username, 0) + 1
    if count >= max(1, threshold):
        _CHANNEL_READ_FAILURES.pop(channel_username, None)
        _CHANNEL_COOLDOWN_UNTIL[channel_username] = time.monotonic() + max(0.0, cooldown_seconds)
        _save_cooldown_state()
        return True
    _CHANNEL_READ_FAILURES[channel_username] = count
    return False


def note_channel_success(channel_username: str) -> None:
    """Reset the failure counter — the channel resolved and read fine."""
    if channel_username:
        _CHANNEL_READ_FAILURES.pop(channel_username, None)
        if _CHANNEL_COOLDOWN_UNTIL.pop(channel_username, None) is not None:
            _save_cooldown_state()


async def read_channel_messages(
    client: Any, channel_username: str, limit: int = 50
) -> list[ChannelMessage]:
    """Read the last *limit* messages from a channel. Never raises.

    Accepts either:
      * the ``HeadlessTelegramClient`` wrapper (its underlying Telethon
        ``_client.iter_messages`` is used), or
      * any object exposing ``iter_messages(entity, limit=...)`` (Telethon
        style), or
      * an injectable fake exposing ``read_messages(channel_username, limit)``
        returning ``list[ChannelMessage]`` (used by tests).

    Any failure → ``[]`` (fail-open, logged at warning).
    """
    try:
        if client is None or not channel_username:
            return []
        raw = getattr(client, "_client", None) or client

        if callable(getattr(raw, "iter_messages", None)):
            messages: list[ChannelMessage] = []
            async for msg in raw.iter_messages(channel_username, limit=max(1, int(limit))):
                converted = _message_to_channel_message(msg, channel_username)
                if converted is not None:
                    messages.append(converted)
            note_channel_success(channel_username)
            return messages

        read_messages = getattr(client, "read_messages", None)
        if callable(read_messages):
            result = await read_messages(channel_username, limit)
            note_channel_success(channel_username)
            if not result:
                return []
            return [m for m in result if isinstance(m, ChannelMessage)]

        return []
    except Exception as e:
        if note_channel_failure(channel_username):
            logger.warning(
                "news_ingest_channel_disabled channel=%s failures=%d cooldown_hours=%s",
                channel_username,
                _CHANNEL_FAILURE_THRESHOLD,
                int(_CHANNEL_COOLDOWN_SECONDS // 3600),
            )
        logger.warning("news_ingest_read_failed channel=%s err=%s", channel_username, e)
        return []


# ── Orchestration ──────────────────────────────────────────────────────────


def channel_username_from_config(channel: dict[str, Any]) -> str:
    """Resolve the channel @username from a source-config dict (never raises)."""
    for key in ("username", "channel_username", "id"):
        value = channel.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("@")
    url = str(channel.get("url") or "")
    if "t.me/" in url:
        return url.split("t.me/", 1)[1].split("/", 1)[0].strip()
    return ""


# ── RSS Feed Support ─────────────────────────────────────────────────────


async def fetch_rss_feed(url: str) -> str | None:
    """Fetch an RSS feed from the given URL.

    Args:
        url: The RSS feed URL

    Returns:
        The raw XML content as a string, or None if failed
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.warning(f"news_ingest:rss_fetch_failed url={url} error={e}")
        return None


def parse_rss_feed(xml_content: str) -> list[dict[str, Any]]:
    """Parse RSS feed XML content into a list of item dictionaries.

    Args:
        xml_content: Raw RSS/XML content as string

    Returns:
        List of dictionaries representing RSS items with keys:
        - title: Item title
        - description: Item description/content
        - link: Item URL
        - pub_date: Publication date string
    """
    if not xml_content:
        return []

    try:
        root = ET.fromstring(xml_content)
        items = []

        # Handle both RSS and Atom formats
        # RSS 2.0: /rss/channel/item
        # Atom: /feed/entry
        channel = root.find("channel")
        if channel is not None:
            # RSS 2.0 format
            for item in channel.findall("item"):
                item_data = {
                    "title": item.findtext("title", ""),
                    "description": item.findtext("description", ""),
                    "link": item.findtext("link", ""),
                    "pub_date": item.findtext("pubDate", ""),
                }
                items.append(item_data)
        else:
            # Try Atom format
            for entry in root.findall("{http://www.w3.org/2005/Atom}entry"):
                item_data = {
                    "title": entry.findtext("{http://www.w3.org/2005/Atom}title", ""),
                    "description": entry.findtext("{http://www.w3.org/2005/Atom}summary", ""),
                    "link": entry.findtext("{http://www.w3.org/2005/Atom}link", ""),
                    "pub_date": entry.findtext("{http://www.w3.org/2005/Atom}updated", ""),
                }
                items.append(item_data)

        return items
    except Exception as e:
        logger.warning(f"news_ingest:rss_parse_failed error={e}")
        return []


def rss_item_to_article(
    item: dict[str, Any], source_name: str, source_url: str, category: str = "news"
) -> NewsArticle | None:
    """Convert an RSS item to a NewsArticle.

    Args:
        item: Dictionary representing an RSS item
        source_name: Name of the source (e.g., "CoinDesk")
        source_url: URL of the RSS feed
        category: News category (news, security, defi, scam, alpha)

    Returns:
        NewsArticle object or None if invalid
    """
    if not isinstance(item, dict):
        return None

    title = str(item.get("title", "")).strip()
    description = str(item.get("description", "")).strip()
    link = str(item.get("link", "")).strip()
    pub_date_str = str(item.get("pub_date", "")).strip()

    # Skip if no title or description
    if not title and not description:
        return None

    # Use title as the main text if description is empty, otherwise combine
    # For RSS, we'll use description as body and title as title
    if not description:
        description = title
    if not title:
        # Extract first sentence from description as title
        first_sentence = description.split(".")[0][:100]
        title = first_sentence if first_sentence else description[:100]

    # Truncate title to fit NewsArticle model
    title = title[:120] if title else ""

    # Parse publication date (best effort)
    posted_at = None
    if pub_date_str:
        try:
            # Try common RSS date formats
            from email.utils import parsedate_to_datetime

            posted_at = parsedate_to_datetime(pub_date_str)
            if posted_at and posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=UTC)
        except Exception:
            # If parsing fails, leave as None (will use current time in persistence)
            pass

    # Extract crypto entities from title and description
    entities = extract_entities(f"{title} {description}")

    return NewsArticle(
        title=title,
        body=description,
        source_channel=source_name,
        source_url=link or source_url,  # Use item link if available, otherwise feed URL
        category=category,
        posted_at=posted_at,
        content_hash="",  # Will be computed by dedupe_articles
        entities=entities,
        media=None,
    )


async def sweep_rss_feeds(
    feeds: list[Any] | None = None, lookback_hours: int | None = None
) -> list[NewsArticle]:
    """Sweep RSS feeds for news articles.

    Args:
        feeds: List of RSS feed configurations (from sources.RSS_FEEDS if None)
        lookback_hours: Only return articles newer than this many hours

    Returns:
        List of NewsArticle objects from RSS feeds
    """
    from rugmunchbot._vendor.news_ingest.sources import RSS_FEEDS as DEFAULT_RSS_FEEDS

    if feeds is None:
        feeds = DEFAULT_RSS_FEEDS

    if not feeds:
        return []

    # Determine cutoff time for lookback
    cutoff_time = None
    if lookback_hours is not None:
        from datetime import timedelta

        cutoff_time = datetime.now(UTC) - timedelta(hours=lookback_hours)

    articles: list[NewsArticle] = []

    # Process each feed
    for feed in feeds:
        if not feed.enabled:
            continue

        # Fetch the RSS feed
        xml_content = await fetch_rss_feed(feed.url)
        if xml_content is None:
            continue

        # Parse the feed
        items = parse_rss_feed(xml_content)
        if not items:
            continue

        # Convert each item to a NewsArticle
        for item in items:
            article = rss_item_to_article(
                item=item, source_name=feed.name, source_url=feed.url, category=feed.category
            )

            if article is None:
                continue

            # Apply lookback filter if specified
            if cutoff_time is not None and article.posted_at is not None:
                if article.posted_at < cutoff_time:
                    continue

            articles.append(article)

    return articles


async def sweep_channels(
    client: Any, channels: list[dict[str, Any]], limit: int = 50
) -> list[NewsArticle]:
    """Read → normalize → dedupe across every configured channel.

    Each entry of *channels* follows the ``unified_news_sources.json`` schema
    (``username``/``id``/``url`` + optional ``category``). Channels without a
    resolvable username are skipped; individual channel failures degrade to
    empty without aborting the sweep.
    """
    articles: list[NewsArticle] = []
    for channel in channels or []:
        username = channel_username_from_config(channel)
        if not username:
            continue
        if is_channel_cooldown(username):
            logger.debug("news_ingest:channel_cooldown_skip channel=%s", username)
            continue
        category = str(channel.get("category") or "crypto_news")
        messages = await read_channel_messages(client, username, limit)
        for message in messages:
            article = channel_message_to_article(message, category)
            if article is not None:
                articles.append(article)
    return dedupe_articles(articles)


# ── Backend news bridge (bot-side addition, 2026-09-04) ────────────────────
# The bot and the rmi-backend news system share ONE ingestion/dedup/rerank
# pipeline. The backend aggregates 200+ sources, dedups, applies Ground
# News-style ratings (bias, factuality, market impact, corroboration) and
# stores in Qdrant — the single source of truth. The bot:
#
#   1. PULLS the already-deduped, rated feed via ``fetch_backend_news``
#      (``GET /api/v1/news/feed``) as its PRIMARY content source.
#   2. PUSHES its own RSS/Telegram/X ingestion via ``push_to_backend``
#      (``POST /api/v1/news/articles``) so the backend dedups + rates +
#      stores it — no double ingestion, no divergent dedup sets.
#
# Both are fail-open by contract: a backend outage degrades to the bot's own
# direct ingestion (fallback) instead of crashing the sweep.


def _article_to_backend_payload(article: NewsArticle) -> dict[str, Any]:
    """Map a bot ``NewsArticle`` to the backend ``NewsArticle`` wire shape.

    The backend ``NewsArticle`` Pydantic model (``app/routers/news.py``) uses
    ``url``/``description``/``source``/``published_at``/``image_url``/
    ``tokens_mentioned`` where the bot dataclass uses ``source_url``/``body``/
    ``source_channel``/``posted_at``/``media``/``entities``. ``resolved_url``
    (the real article URL after dereferencing a ``t.me`` link) wins over the
    raw ``source_url`` when present so the backend dedups on the true story
    URL, not the Telegram share link.
    """
    if article is None:
        return {}
    published_at = article.posted_at.isoformat() if article.posted_at else ""
    url = article.resolved_url or article.source_url or ""
    return {
        "title": article.title or "",
        "description": article.body or "",
        "url": url,
        "source": article.source_channel or "",
        "published_at": published_at,
        "category": article.category or "",
        "image_url": article.media or "",
        "tokens_mentioned": list(article.entities or []),
        "content_hash": article.content_hash or "",
    }


async def fetch_backend_news(limit: int = 50) -> list[dict[str, Any]]:
    """Pull already-deduped, rated articles from the backend news feed.

    PRIMARY source for the bot's content pipeline. The backend aggregates
    200+ sources, dedups, and applies Ground News-style ratings before
    storing in Qdrant — so the bot gets a single, deduplicated, ranked feed
    instead of running its own ingestion.

    Fail-open: returns ``[]`` on any network/HTTP/parse failure so a backend
    outage degrades to the bot's own direct ingestion (fallback), never a
    crash.

    Args:
        limit: Max articles to fetch (clamped to the backend's 1..200 range).

    Returns:
        List of rated article dicts (backend ``NewsArticle`` shape).
    """
    from rugmunchbot.data_resolver import backend_get_json

    body = await backend_get_json(
        "/api/v1/news/feed",
        params={"limit": max(1, min(int(limit), 200))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    articles = body.get("articles") or []
    return [a for a in articles if isinstance(a, dict) and a.get("title")]


async def push_to_backend(articles: list[NewsArticle]) -> int:
    """Push freshly-ingested bot articles to the backend for dedup + rating.

    The bot's own RSS/Telegram/X ingestion pushes raw articles here so the
    backend can dedup (against its 200+ sources), rate (Ground News-style),
    and store in Qdrant — the single source of truth. The bot then pulls the
    rated feed back via :func:`fetch_backend_news` for content generation.

    Fail-open: returns the number of articles the backend accepted (0 on any
    failure) so a backend outage never aborts the sweep — the bot keeps its
    own direct ingestion as a fallback.

    Args:
        articles: Bot-normalized ``NewsArticle`` objects to push.

    Returns:
        Count of articles the backend accepted (``accepted``/``ingested``
        from the response, else 0).
    """
    if not articles:
        return 0
    from rugmunchbot.config import BACKEND_URL, backend_headers
    from rugmunchbot.tools._client import http_post_json

    payload = [_article_to_backend_payload(a) for a in articles]
    body = await http_post_json(
        f"{BACKEND_URL}/api/v1/news/articles",
        json={"articles": payload},
        headers=backend_headers(),
        timeout=10.0,
    )
    if not body:
        return 0
    accepted = body.get("accepted")
    if accepted is None:
        accepted = body.get("ingested")
    try:
        return int(accepted or 0)
    except (TypeError, ValueError):
        return 0

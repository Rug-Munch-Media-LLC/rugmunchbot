# VENDORED 2026-09-04 from rmi-backend/app/domains/news_ingest/ingest.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
# read_channel_messages is a thin async reader over the headless Telegram
# client. It never edits app.domains.telegram.headless_client and never
# raises - any failure degrades to an empty list so a single flaky channel
# can not take down the whole sweep.
#
# The dedup layers are URL / normalized-title aware so the same story that is
# rephrased across channels (or re-posted with a slightly different body)
# collapses into a single article instead of repeating:
# 1. Canonical source URL - the first non-t.me http(s) URL inside the
#    message (the original article link) is the strongest "same story" signal.
# 2. Channel URL - the stable https://t.me/<channel>/<message_id> link.
# 3. Normalized title - lowercase, punctuation/emoji stripped, first 60
#    chars (only used when the title is substantive, >= 20 chars).
# 4. Content hash - last resort, raw content_hash (RAG-compatible).
#
# The same key set is used for the in-batch dedup (dedupe_articles) and the
# cross-run Redis dedup (filter_seen_articles / mark_articles_ingested),
# so a story seen in a previous sweep - even rephrased - is never re-read.
"""News ingestion pipeline - vendored from rmi-backend."""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Final

import httpx

from rugmunchbot._vendor.dedup import content_hash, url_hash
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle
from rugmunchbot._vendor.news_ingest.normalize import extract_entities

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
    original article link. ``t.me`` links are skipped - they are the channel's
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
    (``Image: https://.../photo.jpg``). Best-effort - returns ``""`` when the
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
    ``JUST IN`` / ``FLASH`` - same story with or without the marker), then
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
    its keys has been seen before - so a rephrased story that keeps its
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
        keys.append(fp)  # raw content hash - stays RAG-pipeline compatible
    # Dedupe within the list (a URL may repeat when title/body repeat it).
    return list(dict.fromkeys(keys))


def dedupe_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    """First-wins multi-key dedup (canonical URL / t.me URL / title / hash).

    Uses the pure ``app.rag.dedup`` helpers (``content_hash`` / ``url_hash``)
    so batch dedup needs no Redis. Preserves input order; an article whose
    canonical URL, channel URL, normalized title, or content hash matches an
    earlier article is dropped - this collapses the same story forwarded
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
    key on the URL/title, not just the raw content hash - a rephrased story
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
    ``is_duplicate_content`` - the same Redis ZSET the RAG pipeline maintains
    (``rag:dedup:content:tg_news``) - under a dedicated *collection*
    namespace. Fail-open: Redis down / auth-failure treats every article as
    fresh, matching the rest of the ingest layer.

    Returns ``(fresh, skipped)``; ``fresh`` preserves input order.
    """
    from app.utils.dedup import is_duplicate_content

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
            except Exception:
                logger.debug("news_ingest:dedup_check_failed", exc_info=True)
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
    so a Redis outage only means the next sweep re-reads the same content -
    the Postgres ``news_id`` guard still blocks duplicate persistence.

    Marks **every** key of the article (URL / title / content hash) so a
    later rephrase of the same story is caught by the cross-run check.
    Returns the number of articles marked.
    """
    from app.utils.dedup import mark_content_ingested

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

#: Source-authority hints - direct outlets outrank aggregators/repackagers.
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
#: Aggregator/repackager channels - lower authority than primary outlets.
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

#: Crypto-relevance keywords - market-moving vocabulary over generic tech.
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

    Combines cheap, explainable signals - the same axes the cross-encoder
    reranker is calibrated on, usable everywhere (ordering, website feed):

    * source authority - cointelegraph/coindesk/theblock & co. outrank
      aggregator/repackager channels;
    * breaking markers in the title (strong) or body (weak);
    * crypto-relevance keyword density in the title (strong) and body (weak);
    * freshness - newer posts score higher (age windows: <1h, <3h).

    Fail-open: any missing field (no ``posted_at``, empty body) degrades to
    the remaining signals; never raises. Returns a value in ``[0, 1]``.
    """
    if article is None:
        return 0.0
    score = 0.2  # baseline - every normalized article is minimally relevant
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
        score += 0.0  # aggregator - no authority bump
    else:
        score += 0.12  # unknown source - neutral

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
    except Exception:
        logger.debug("news_ingest_convert_failed", exc_info=True)
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


def is_channel_cooldown(channel_username: str, now: float | None = None) -> bool:
    """True while *channel_username* is in auto-disable cooldown (skip the RPC).

    Fail-open: unknown channels are never in cooldown.
    """
    if not channel_username:
        return False
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
        return True
    _CHANNEL_READ_FAILURES[channel_username] = count
    return False


def note_channel_success(channel_username: str) -> None:
    """Reset the failure counter - the channel resolved and read fine."""
    if channel_username:
        _CHANNEL_READ_FAILURES.pop(channel_username, None)


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


# ── Newsletter Scraping Support ────────────────────────────────────────
#: List of newsletter sources to scrape (those without RSS)
NEWSLETTER_SOURCES: list[dict[str, str]] = [
    {"name": "Hayes Substack", "url": "https://hayes.substack.com", "category": "alpha"},
    {"name": "DeFi Mirror", "url": "https://defi.mirror.xyz", "category": "defi"},
    {
        "name": "Not Only Black and White",
        "url": "https://notonlyblackandwhite.substack.com",
        "category": "alpha",
    },
    {"name": "Mirror.xyz Crypto", "url": "https://mirror.xyz/tag/crypto", "category": "news"},
    {"name": "Paragraph.xyz Crypto", "url": "https://paragraph.xyz/tag/crypto", "category": "news"},
    {"name": "Bankless Newsletter", "url": "https://bankless.substack.com", "category": "alpha"},
    {
        "name": "The Defiant Newsletter",
        "url": "https://thedefiant.io/subscribe",
        "category": "defi",
    },
    {"name": "Messari Free", "url": "https://messari.io/newsletter", "category": "news"},
    {
        "name": "CoinGecko Newsletter",
        "url": "https://www.coingecko.com/en/newsletter",
        "category": "news",
    },
    {"name": "Uniswap Blog", "url": "https://uniswap.org/blog", "category": "defi"},
    {"name": "Aave Blog", "url": "https://aave.com/blog", "category": "defi"},
    {"name": "Compound Blog", "url": "https://compound.finance/blog", "category": "defi"},
    {"name": "MakerDAO Blog", "url": "https://blog.makerdao.com", "category": "defi"},
    {"name": "Lido Blog", "url": "https://blog.lido.fi", "category": "defi"},
    {"name": "EigenLayer Blog", "url": "https://blog.eigenlayer.xyz", "category": "defi"},
    {"name": "Celestia Blog", "url": "https://blog.celestia.org", "category": "defi"},
    {"name": "Solana Blog", "url": "https://solana.com/blog", "category": "defi"},
    {"name": "Helius Blog", "url": "https://helius.dev/blog", "category": "defi"},
    {"name": "Jito Blog", "url": "https://jito.xyz/blog", "category": "defi"},
    {"name": "Jupiter Blog", "url": "https://jup.ag/blog", "category": "defi"},
    {"name": "Pyth Blog", "url": "https://pyth.network/blog", "category": "defi"},
    {"name": "Chainlink Blog", "url": "https://blog.chainlink.org", "category": "defi"},
    {"name": "Wormhole Blog", "url": "https://wormhole.com/blog", "category": "defi"},
    {"name": "LayerZero Blog", "url": "https://layerzero.network/blog", "category": "defi"},
    {"name": "Across Blog", "url": "https://across.to/blog", "category": "defi"},
    {"name": "Stargate Blog", "url": "https://stargate.finance/blog", "category": "defi"},
    {"name": "0x Blog", "url": "https://blog.0xproject.com", "category": "defi"},
    {"name": "Matcha Blog", "url": "https://matcha.xyz/blog", "category": "defi"},
    {"name": "DexScreener Blog", "url": "https://dexscreener.com/blog", "category": "defi"},
    {"name": "GeckoTerminal Blog", "url": "https://www.geckoterminal.com/blog", "category": "defi"},
    {"name": "Bubblemaps Blog", "url": "https://bubblemaps.io/blog", "category": "security"},
    {"name": "Arkham Blog", "url": "https://arkhamintelligence.com/blog", "category": "security"},
    {
        "name": "Etherscan Blog",
        "url": "https://etherscan.io/blog",
        "category": "news",
    },
]

#: PryScraper API base URL (Docker service, healthy on the host loopback).
_PRY_BASE_URL: Final[str] = os.getenv("PRYSCRAPER_URL", "http://127.0.0.1:8005").rstrip("/")
#: Redis cache TTL for Pry scrape results (1 hour).
_PRY_CACHE_TTL_SECONDS: Final[int] = 3600
#: Pry scrape timeout (15s per spec).
_PRY_TIMEOUT_SECONDS: Final[float] = 15.0


# Async function to scrape a URL via PryScraper, fail-open.
async def scrape_with_pry(url: str) -> dict | None:
    """Scrape *url* via PryScraper, fail-open.

    POSTs to ``{PRY_BASE_URL}/v1/scrape`` with ``bypassCloudflare`` and
    ``onlyMainContent`` enabled so feeds that block direct httpx (403/429/
    Cloudflare) can still be read. Results are cached in Redis for 1 hour
    under ``pry:scrape:{url_hash}``.

    Fail-open by contract: any error (network, HTTP, parse, Redis) returns
    ``None`` so a Pry outage never aborts the RSS sweep.

    Returns a dict with ``title`` / ``content`` / ``html`` when available,
    else ``None``.
    """
    if not url:
        return None

    cache_key = f"pry:scrape:{url_hash(url)}"

    # Try Redis cache first (best-effort, fail-open).
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is not None:
            cached = await r.get(cache_key)
            if cached:
                try:
                    return json.loads(cached)
                except (TypeError, ValueError):
                    pass  # corrupt cache entry - fall through to scrape
    except Exception as exc:
        logger.debug("news_ingest:pry_cache_read_failed url=%s err=%s", url, str(exc)[:100])

    try:
        from rugmunchbot.support.pry_auth import pry_headers

        async with httpx.AsyncClient(timeout=_PRY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{_PRY_BASE_URL}/v1/scrape",
                headers=pry_headers(),
                json={"url": url, "bypassCloudflare": True, "onlyMainContent": True},
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        logger.warning("news_ingest:pry_scrape_failed url=%s err=%s", url, str(exc)[:200])
        return None

    if not isinstance(body, dict):
        return None

    # Pry returns the payload under ``data`` on success; tolerate both shapes.
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if not isinstance(data, dict):
        return None

    result: dict = {
        "title": data.get("title") or "",
        "content": data.get("content") or data.get("markdown") or "",
        "html": data.get("html") or "",
    }

    # Cache the result for 1 hour (best-effort, fail-open).
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is not None:
            await r.setex(cache_key, _PRY_CACHE_TTL_SECONDS, json.dumps(result))
    except Exception as exc:
        logger.debug("news_ingest:pry_cache_write_failed url=%s err=%s", url, str(exc)[:100])

    return result


async def scrape_rss_with_pry(feed_url: str) -> list[dict]:
    """Scrape an RSS feed URL via Pry and parse the returned HTML as RSS/XML.

    Some feeds block direct httpx (403/429/Cloudflare). Pry bypasses that and
    returns the rendered page; when the page is the raw feed XML we parse it
    with :func:`parse_rss_feed`. Fail-open: returns ``[]`` on any failure.

    Returns a list of RSS item dicts (see :func:`parse_rss_feed`).
    """
    if not feed_url:
        return []

    result = await scrape_with_pry(feed_url)
    if not result:
        return []

    # Prefer the raw HTML (the feed body); fall back to the extracted content.
    xml_content = result.get("html") or result.get("content") or ""
    if not xml_content:
        return []

    return parse_rss_feed(xml_content)


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

        # Fetch the RSS feed - try direct httpx first (fast, no overhead),
        # then fall back to PryScraper for feeds that block direct requests
        # (403/429/Cloudflare). If both fail, skip the feed.
        xml_content = await fetch_rss_feed(feed.url)
        if xml_content is None:
            logger.info("news_ingest:rss_direct_failed_fallback_pry url=%s", feed.url)
            items = await scrape_rss_with_pry(feed.url)
            if not items:
                logger.warning("news_ingest:rss_pry_fallback_failed_skip url=%s", feed.url)
                continue
        else:
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


# Helper to extract links from HTML


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for attr in attrs:
                if attr[0] == "href":
                    self.links.append(attr[1])


# Async function to scrape a newsletter page and extract article links
async def scrape_newsletter_with_pry(url: str) -> list[dict]:
    """Scrape a newsletter page via Pry, extract article links, and scrape each article.
    Returns a list of article dicts with keys: title, url, content, image.
    Fail-open: returns [] on any error.
    """
    try:
        # Scrape the newsletter page
        newsletter_page = await scrape_with_pry(url)
        if not newsletter_page:
            return []
        html_content = newsletter_page.get("html", "")
        if not html_content:
            return []

        # Extract links from the HTML
        extractor = LinkExtractor()
        extractor.feed(html_content)
        links = extractor.links

        # Filter links to those that are likely article URLs (same domain and not common non-article paths)
        from urllib.parse import urljoin, urlparse

        parsed_base = urlparse(url)
        base_domain = parsed_base.netloc
        article_links = []
        for link in links:
            # Make absolute URL
            abs_link = urljoin(url, link)
            parsed_link = urlparse(abs_link)
            # Skip if not same domain
            if parsed_link.netloc != base_domain:
                continue
            # Skip common non-article paths
            skip_paths = [
                "/about",
                "/archive",
                "/subscribe",
                "/newsletter",
                "/search",
                "/tag/",
                "/author/",
                "/feed",
                "/rss",
                "/privacy",
                "/terms",
            ]
            if any(path in parsed_link.path for path in skip_paths):
                continue
            # Skip if it's the base URL itself (the newsletter homepage)
            if abs_link == url or abs_link == url + "/":
                continue
            article_links.append(abs_link)

        # Scrape each article link
        articles = []
        for article_url in article_links:
            article_data = await scrape_with_pry(article_url)
            if not article_data:
                continue
            # Pry returns title, content, html. We also want to extract an image if available.
            # We'll try to get an image from the HTML content if available, or from the content if it contains an img tag.
            # For simplicity, we'll use the HTML to extract the first image URL.
            image_url = ""
            html_content = article_data.get("html", "")
            if html_content:
                # Use a simple regex to find the first img src
                import re

                img_match = re.search(r'<img[^>]+src="([^"]+)"', html_content, re.IGNORECASE)
                if img_match:
                    image_url = img_match.group(1)
            # If no image found in HTML, try to look in the content (markdown) for an image?
            # We'll leave it empty if not found.

            articles.append(
                {
                    "title": article_data.get("title", ""),
                    "url": article_url,
                    "content": article_data.get("content", "") or article_data.get("markdown", ""),
                    "image": image_url,
                }
            )
        return articles
    except Exception as e:
        logger.warning(f"news_ingest:newsletter_scrape_failed url={url} error={e}")
        return []


# Function to sweep all newsletter sources
async def sweep_newsletters_with_pry() -> list[NewsArticle]:
    """Scrape all newsletter sources and return a list of NewsArticle objects."""
    from rugmunchbot._vendor.news_ingest.normalize import extract_entities

    articles: list[NewsArticle] = []
    for source in NEWSLETTER_SOURCES:
        url = source["url"]
        name = source["name"]
        category = source["category"]
        newsletter_articles = await scrape_newsletter_with_pry(url)
        for article_dict in newsletter_articles:
            # Convert the article dict to a NewsArticle
            title = article_dict.get("title", "")
            body = article_dict.get("content", "")
            article_url = article_dict.get("url", "")
            image_url = article_dict.get("image", "")

            # Skip if no title or body
            if not title and not body:
                continue

            # Extract crypto entities
            entities = extract_entities(f"{title} {body}")

            # Truncate title to fit NewsArticle model
            title = title[:120] if title else ""

            # We don't have a publication date from the scraping, so leave it as None
            # The content_hash will be computed by dedupe_articles if empty.
            news_article = NewsArticle(
                title=title,
                body=body,
                source_channel=name,
                source_url=article_url,
                category=category,
                posted_at=None,  # unknown
                content_hash="",  # will be computed by dedupe_articles
                entities=entities,
                media=image_url,  # Note: the NewsArticle model has a `media` field for the image URL
            )
            articles.append(news_article)
    return articles


#: Base URL of the rmi-backend news API (local loopback).
_BACKEND_NEWS_BASE: Final[str] = "http://127.0.0.1:8000"
#: Timeout for backend news HTTP calls (seconds).
_BACKEND_NEWS_TIMEOUT: Final[float] = 10.0


def _parse_article_datetime(value: Any) -> datetime | None:
    """Best-effort parse of a backend ``published_at`` value into an aware UTC datetime."""
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=UTC)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _as_utc(parsed)
    except (ValueError, TypeError, OverflowError):
        return None


def fetch_backend_news(limit: int = 50) -> list[NewsArticle]:
    """Fetch the latest news feed from rmi-backend and return ``NewsArticle`` objects.

    GET ``/api/v1/news/feed?limit={limit}`` and convert each item (title, url,
    source, published_at, rating) into a :class:`NewsArticle`. Fail-open: any
    error (backend down, connection refused, non-200, parse failure) degrades
    to an empty list so a flaky backend can never take down the sweep.
    """
    try:
        url = f"{_BACKEND_NEWS_BASE}/api/v1/news/feed?limit={int(limit)}"
        headers = {"X-Internal-Key": os.getenv("RMI_INTERNAL_KEY", "")}
        with httpx.Client(timeout=_BACKEND_NEWS_TIMEOUT) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()

        # Accept either a bare list or a wrapper dict with an "items"/"articles" key.
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("items") or payload.get("articles") or []
        else:
            items = []

        articles: list[NewsArticle] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            article_url = str(item.get("url") or "").strip()
            source = str(item.get("source") or "").strip()
            if not title and not article_url:
                continue
            rating = item.get("rating")
            articles.append(
                NewsArticle(
                    title=title[:120],
                    body=str(item.get("body") or item.get("summary") or ""),
                    source_channel=source,
                    source_url=article_url,
                    category=str(rating) if rating is not None else "",
                    posted_at=_parse_article_datetime(item.get("published_at")),
                    content_hash="",  # computed by dedupe_articles if empty
                    entities=extract_entities(f"{title} {item.get('body') or ''}"),
                    media=item.get("media") or item.get("image"),
                )
            )
        return articles
    except Exception as e:
        logger.warning("fetch_backend_news failed (fail-open -> []): %s", e)
        return []


def push_to_backend(articles: list) -> int:
    """POST a list of articles to the rmi-backend news endpoint.

    Returns the number of articles pushed. Fail-open: any error (backend down,
    connection refused, non-200, serialization failure) degrades to ``0``.
    """
    if not articles:
        return 0
    try:
        payloads = []
        for article in articles:
            if isinstance(article, NewsArticle):
                payloads.append(
                    {
                        "title": article.title,
                        "url": article.source_url,
                        "source": article.source_channel,
                        "published_at": (
                            article.posted_at.isoformat() if article.posted_at else None
                        ),
                        "rating": article.category or None,
                        "body": article.body,
                        "media": article.media,
                    }
                )
            elif isinstance(article, dict):
                payloads.append(article)
            else:
                continue

        if not payloads:
            return 0

        url = f"{_BACKEND_NEWS_BASE}/api/v1/news/articles"
        headers = {"X-Internal-Key": os.getenv("RMI_INTERNAL_KEY", "")}
        with httpx.Client(timeout=_BACKEND_NEWS_TIMEOUT) as client:
            resp = client.post(url, json=payloads, headers=headers)
            resp.raise_for_status()
        return len(payloads)
    except Exception as e:
        logger.warning("push_to_backend failed (fail-open -> 0): %s", e)
        return 0

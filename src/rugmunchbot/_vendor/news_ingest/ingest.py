# VENDORED 2026-08-17 from rmi-backend/app/domains/news_ingest/ingest.py (frozen copy; do not edit).
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

import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Final

from rugmunchbot._vendor.dedup import content_hash, url_hash
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle
from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article

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
            except Exception:
                logger.debug("news_ingest:dedup_check_failed", exc_info=True)
        if duplicate:
            skipped += 1
            continue
        fresh.append(article)
    if skipped:
        logger.info("news_ingest:seen_skipped collection=%s fresh=%s skipped=%s", collection, len(fresh), skipped)
    return fresh, skipped


def mark_articles_ingested(articles: list[NewsArticle], collection: str = DEFAULT_DEDUP_COLLECTION) -> int:
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
            age_hours = (_as_utc(article.posted_at) - _as_utc(now or datetime.now(UTC))).total_seconds() / 3600.0
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
            key = getattr(reaction, "emoticon", None) or getattr(reaction, "document_id", None) or str(reaction)
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
    """Reset the failure counter — the channel resolved and read fine."""
    if channel_username:
        _CHANNEL_READ_FAILURES.pop(channel_username, None)


async def read_channel_messages(client: Any, channel_username: str, limit: int = 50) -> list[ChannelMessage]:
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


async def sweep_channels(client: Any, channels: list[dict[str, Any]], limit: int = 50) -> list[NewsArticle]:
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

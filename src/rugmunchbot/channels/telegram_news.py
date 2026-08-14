"""Telegram news-channel ingestion → website news + bot news channel.

Sweeps a curated set of public crypto-news Telegram channels (via the same
headless Telethon user-session client the alerts domain uses), normalizes the
recent messages with ``rugmunchbot.integrations.news_ingest``, then:

1. **Persists** newly-seen articles into the website news table
   (``news_items``, the table the ``/api/v1/news/*`` routes serve) — mirroring
   the RSS ingest path in ``rugmunchbot.integrations.news_ingest``.
2. **Posts** the important subset to the bot's news channel (same chat id as
   the existing ``post_news_update`` job; same ``format_news_post`` +
   ``_safe_send`` machinery). Importance is the reranker-derived score from
   ``channels/importance.py`` (Infinity ``BAAI/bge-reranker-v2-m3``); every
   article is persisted, only those above ``RMI_NEWS_IMPORTANCE_THRESHOLD``
   are posted (fail-open: heuristic fallback when Infinity is down).

Everything fails open: a missing headless client, a flaky channel, Postgres
being unreachable, or a Telegram send error logs and skips — the JobQueue keeps
running and the other half of the pipeline still works.

Read mechanism
--------------
``python-telegram-bot``'s Bot API cannot read arbitrary public channels the bot
is not a member of, so channel reads go through the **headless Telethon client**
(``rugmunchbot.integrations.headless.get_headless_client`` → MTProto user session)
via the canonical thin reader ``rugmunchbot.integrations.news_ingest.read_channel_messages``.
This is the exact mechanism the alerts domain (``telegram_transport``) and the
daily pipeline (``rugmunchbot.integrations.content.daily_pipeline``) already use.

Setup required before the sweep returns data (documented, never hardcoded):
* ``telethon`` is a runtime dependency (see ``requirements.txt`` /
  ``pyproject.toml``).
* gopass entries the headless client reads in
  ``rugmunchbot.integrations.headless_credentials``: ``rmi/telegram/api_id`` and
  ``rmi/telegram/api_hash`` exist in the store; ``rmi/telegram/phone`` must be
  created (``insert a phone entry in the secrets store (see headless_credentials)``). Until the phone entry exists
  the sweep degrades to ``[]`` and logs ``headless_client_unavailable``.

Channel list
------------
The sweep prefers the account's own followed/member public channels, discovered
at runtime via Telethon ``client.get_dialogs()``
(``rugmunchbot.integrations.news_ingest.discover_followed_channels``) — gated by
``RMI_TG_NEWS_AUTO_DISCOVER`` (default on). Every discovered channel is
categorized by title/username heuristics (:func:`classify_channel_category`)
into **news** / **alpha** / **scam** / **other**:

* ``news``  → swept with ``category="crypto_news"`` → news channel
* ``alpha`` → swept with ``category="alpha"`` → alpha channel
* ``scam``  → left to ``channels/scam_watch.py`` (existing categorization,
  never duplicated here)
* ``other`` → skipped (personal chats / non-crypto noise)

The discovered sweep is bounded by ``RMI_TG_NEWS_MAX_CHANNELS`` (default 40);
per-channel message reads are bounded by ``RMI_TG_NEWS_PER_CHANNEL_LIMIT``
(default 30). When discovery is disabled, returns nothing, or the client is
unavailable, the sweep falls back to the env-overridable explicit list
``RMI_TG_NEWS_CHANNELS`` (comma- or pipe-separated @handles), then to
``_DEFAULT_CHANNELS`` (10 well-known public crypto-news channels). Several
default handles carry ``[REVIEW]`` notes in ``app/databus/tg_channels.json``;
verify them before production monitoring. No secrets involved.

Dedup
-----
Four layers:
* **In-batch** — ``dedupe_articles`` (content-hash / source-URL first-wins) so
  the same story forwarded across channels collapses.
* **Cross-run (Redis)** — ``rugmunchbot.integrations.news_ingest.filter_seen_articles``
  reuses the shared ``rugmunchbot.integrations.dedup`` content-hash ZSETs
  (``rag:dedup:content:tg_news``), so content seen in a previous sweep — or
  ingested by the RAG pipeline — is never re-read/re-embedded. Fresh articles
  are marked (``mark_articles_ingested``) only AFTER a successful
  ``news_items`` insert (fail-open; Redis down never hides a story).
* **Cross-run (Postgres)** — the ``news_items.news_id`` primary key is derived
  from the canonical ``https://t.me/<channel>/<message_id>`` URL, so
  re-sweeping the same message is skipped by the ``SELECT 1 ...
  WHERE news_id=$1`` guard.
* **Post-level** — the scheduler's in-memory ``_remember`` set (key prefix
  ``tgnews`` / ``tgalpha``) stops the digest re-posting the same article on
  the next tick.

Pipeline order (efficiency contract)
------------------------------------
``read (bounded) → normalize → dedupe (content hash) → store (news_items +
Redis dedup mark) → reranker importance → route``.

Routing
-------
* **news** articles → persisted → posted to the news channel
* **alpha** articles → persisted → posted to the alpha channel (when
  ``_chat_id("alpha")`` resolves; skipped otherwise)
* **scam** → owned by ``channels/scam_watch.py`` (feeds the scanner)

Additive side-effects (all fail-open): freshly-persisted TG items are merged
into the website full-feed Redis cache (``inject_into_news_cache``) so
``/api/v1/news/feed`` + ``/headlines`` surface them with
``kind="telegram"`` + channel attribution — the Postgres-backed
``/api/v1/news/trending`` + ``POST /api/v1/news`` routes already serve TG
items directly from ``news_items``. A lightweight market-awareness snapshot
(top headlines + trending tokens) is published to
``rmi:news:market_awareness`` (``publish_market_awareness``) for the bot's
chat context.

Schedule
--------
Registered additively in ``channels/scheduler.py`` (``register_channel_jobs``):
every ``RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES`` (default 30 — ~2 posts/hour on
the news channel) minutes, gated by ``RMI_TG_NEWS_CHANNEL_ENABLED`` (default
on) and a resolvable news chat id.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.formatters import format_news_post
from rugmunchbot.channels.importance import (
    score_importance,
    select_important,
    top_headlines,
    trending_tokens,
)
from rugmunchbot.integrations.news_ingest.discovery import discover_followed_channels
from rugmunchbot.integrations.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    dedupe_articles,
    filter_seen_articles,
    mark_articles_ingested,
    read_channel_messages,
)
from rugmunchbot.integrations.news_ingest.normalize import channel_message_to_article
from rugmunchbot.support.logging import get_logger
from rugmunchbot.support.redis import get_redis_async

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from rugmunchbot.integrations.news_ingest.models import ChannelMessage, NewsArticle

logger = get_logger("rmi_bot.channels.tg_news")

# ── Channel list (env-overridable, no secrets) ──────────────────────────────

#: Default public crypto-news channels. All real, public handles; the ones also
#: listed in ``app/databus/tg_channels.json`` carry that registry's confidence
#: notes (some are [REVIEW] — re-verify before production monitoring).
_DEFAULT_CHANNELS: tuple[str, ...] = (
    "cointelegraph",  # official CoinTelegraph
    "wublockchain",  # Wu Blockchain (Colin Wu) — high-frequency news aggregator
    "coindesk",  # official CoinDesk
    "TheBlock__",  # The Block [REVIEW: exact handle, double underscore]
    "cryptoslate",  # CryptoSlate
    "beincrypto",  # BeInCrypto
    "cryptonews",  # Cryptonews.com [REVIEW: verify handle]
    "PANewsOfficial",  # PANews [REVIEW: verify casing]
    "WatcherGuru",  # Watcher Guru
    "bitcoinmagazine",  # Bitcoin Magazine [REVIEW: verify handle]
)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return list(default)
    parts = [p.strip().lstrip("@") for p in re_split(raw)]
    return [p for p in parts if p]


def re_split(value: str) -> list[str]:
    """Split an env list on commas, pipes, or whitespace."""
    return [p for p in value.replace(",", "|").replace(" ", "|").split("|") if p]


TG_NEWS_ENABLED = _env_bool("RMI_TG_NEWS_CHANNEL_ENABLED", True)
TG_NEWS_INTERVAL_MINUTES = _env_int("RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES", 30)
TG_NEWS_SWEEP_HOURS = _env_int("RMI_TG_NEWS_SWEEP_HOURS", 6)
TG_NEWS_PER_CHANNEL_LIMIT = _env_int("RMI_TG_NEWS_PER_CHANNEL_LIMIT", 30)
TG_NEWS_MAX_CHANNELS = _env_int("RMI_TG_NEWS_MAX_CHANNELS", 40)
TG_NEWS_POST_LIMIT = _env_int("RMI_TG_NEWS_POST_LIMIT", 5)
TG_NEWS_PERSIST = _env_bool("RMI_TG_NEWS_PERSIST", True)
TG_NEWS_AUTO_DISCOVER = _env_bool("RMI_TG_NEWS_AUTO_DISCOVER", True)
TG_NEWS_CHANNELS = _env_list("RMI_TG_NEWS_CHANNELS", list(_DEFAULT_CHANNELS))


# ── Followed-channel categorization (news / alpha / scam / other) ────────────


# Own channels (the account follows its own news/alerts/alpha channels) must
# never be auto-classified as news/alpha — they are already curated digests.
_CHANNEL_OWN_KEYWORDS: tuple[str, ...] = ("rugmunch", "rugmunchbot", "cryptorugmunch")

#: Alpha/whale channel title signals — routed to the alpha channel.
_CHANNEL_ALPHA_KEYWORDS: tuple[str, ...] = (
    "alpha",
    "whale",
    "whales",
    "signal",
    "signals",
    "calls",
    "call",
    "gem",
    "gems",
    "100x",
    "1000x",
    "moon",
    "mooner",
    "plays",
    "insider",
    "sniper",
    "smart money",
    "accumulation",
    "early entries",
    "pump group",
    "pnd",
)

#: News/media/market channel title signals — routed to the news channel.
_CHANNEL_NEWS_KEYWORDS: tuple[str, ...] = (
    "news",
    "cryptonews",
    "crypto",
    "coin",
    "coins",
    "finance",
    "market",
    "markets",
    "headlines",
    "daily",
    "today",
    "update",
    "updates",
    "media",
    "journal",
    "wire",
    "insight",
    "insights",
    "briefing",
    "report",
    "bulletin",
    "digest",
    "magazine",
    "times",
    "cointelegraph",
    "coindesk",
    "decrypt",
    "block",
    "bitcoin",
    "ethereum",
    "solana",
    "altcoin",
    "macro",
    "trading",
    "slate",
    "guru",
    "watcher",
)


def classify_channel_category(title: str, username: str) -> str:
    """Classify a followed channel by title/username heuristics.

    Returns one of ``news`` | ``alpha`` | ``scam`` | ``other``:

    * ``scam``  — reuses the existing scam/shill classification in
      ``channels/scam_watch.py`` (``_classify_dialog``) for **unambiguous**
      scam signals (pump/shill/airdrop/seed-phrase/rug/honeypot). Channels
      the scam sweep flags only as ``ALPHA_RUG_SHILL`` (alpha/calls/signals/
      gem/100x titles) are *not* excluded here — their call content routes to
      the alpha channel while scam_watch still scans their tokens
      independently. No keyword duplication.
    * ``alpha`` — whale/alpha/signal/calls-style titles → routed to the alpha
      channel.
    * ``news``  — news/media/market-style titles → routed to the news channel.
    * ``other`` — everything else (personal chats, groups, non-crypto) →
      excluded from the sweep entirely.

    Pure + fail-open (a broken scam_watch import never breaks categorization).
    """
    haystack = f"{title or ''} {username or ''}".lower()
    if any(word in haystack for word in _CHANNEL_OWN_KEYWORDS):
        return "other"
    try:
        from rugmunchbot.channels.scam_watch import (
            ScamSourceType,
            _classify_dialog,
        )

        subtype = _classify_dialog(str(title or ""), str(username or ""))
        if subtype is not None and subtype != ScamSourceType.ALPHA_RUG_SHILL:
            return "scam"
    except Exception:  # fail-open by contract
        logger.debug("tg_news:scam_classify_unavailable", exc_info=True)
    if any(kw in haystack for kw in _CHANNEL_ALPHA_KEYWORDS):
        return "alpha"
    if any(kw in haystack for kw in _CHANNEL_NEWS_KEYWORDS):
        return "news"
    return "other"


# ── Source config ────────────────────────────────────────────────────────────


def channel_sources() -> list[dict[str, Any]]:
    """Source-config dicts (``username`` + ``category``) for the sweep.

    Returns the explicit ``RMI_TG_NEWS_CHANNELS`` list (falling back to
    ``_DEFAULT_CHANNELS``). This is the *fallback* list — when auto-discovery
    is enabled and the headless client is available,
    :func:`resolve_channel_sources` prefers the account's own channels.
    """
    return [{"username": u, "category": "crypto_news", "name": u} for u in TG_NEWS_CHANNELS]


async def resolve_channel_sources(client: Any | None = None) -> list[dict[str, Any]]:
    """Resolve the sweep's channel list, preferring the account's own channels.

    Priority (highest first):
      1. **Discovered** — the logged-in account's followed/member public
         channels, enumerated via Telethon ``get_dialogs()``
         (:func:`rugmunchbot.integrations.news_ingest.discover_followed_channels`), gated
         by ``RMI_TG_NEWS_AUTO_DISCOVER`` (default on). Every discovered
         channel is categorized by :func:`classify_channel_category`:
         **news** → ``crypto_news`` and **alpha** → ``alpha`` are swept;
         **scam** channels are left to the scam_watch job (no duplication)
         and **other** channels (personal chats, non-crypto) are skipped.
         The result is bounded by ``RMI_TG_NEWS_MAX_CHANNELS``.
      2. **Explicit** — ``RMI_TG_NEWS_CHANNELS`` env list.
      3. **Default** — ``_DEFAULT_CHANNELS``.

    Fail-open: when auto-discovery is disabled, the client is missing, or
    discovery returns nothing, the explicit/default list is used.
    """
    if TG_NEWS_AUTO_DISCOVER and client is not None:
        try:
            discovered = await discover_followed_channels(client)
        except Exception as exc:  # discovery must never break the sweep
            logger.warning("tg_news:auto_discover_failed err=%s", str(exc)[:200])
            discovered = []
        if discovered:
            sources: list[dict[str, Any]] = []
            seen: set[str] = set()
            counts = {"news": 0, "alpha": 0, "scam": 0, "other": 0}
            for username, title in discovered:
                handle = str(username or "").strip().lstrip("@")
                if not handle or handle in seen:
                    continue
                seen.add(handle)
                category = classify_channel_category(str(title or ""), handle)
                counts[category] += 1
                if category in ("scam", "other"):
                    continue  # scam → scam_watch; other → not news-relevant
                category_tag = "crypto_news" if category == "news" else "alpha"
                sources.append(
                    {
                        "username": handle,
                        "category": category_tag,
                        "name": str(title or handle),
                        "channel_category": category,
                    }
                )
                if len(sources) >= TG_NEWS_MAX_CHANNELS:
                    break
            if sources:
                logger.info(
                    "tg_news:using_discovered_channels count=%s by_category=%s",
                    len(sources),
                    counts,
                )
                return sources
            logger.debug(
                "tg_news:auto_discover_no_news_channels fallback_to_explicit counts=%s", counts
            )
    return channel_sources()


# ── Client bootstrap (fail-open) ─────────────────────────────────────────────


async def _build_headless_client() -> Any:
    """Return the singleton headless Telethon client, or ``None`` (never raises).

    When telethon is missing or the gopass API credentials are unpopulated the
    existing client raises; we log the exact setup needed and degrade.
    """
    try:
        from rugmunchbot.integrations.headless import get_headless_client

        client = await get_headless_client()
        if getattr(client, "is_connected", False):
            return client
        return None
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_news:headless_client_unavailable err=%s", str(exc)[:200])
        return None


# ── Time filter ──────────────────────────────────────────────────────────────


def _as_utc(value: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _inside_window(message: ChannelMessage, cutoff: datetime) -> bool:
    """True when a message falls inside the lookback window.

    Messages without a date are kept (best-effort — cross-run dedup on the
    stable ``news_id`` still prevents re-ingesting ancient items).
    """
    posted = message.posted_at
    if posted is None:
        return True
    return _as_utc(posted) >= cutoff


# ── Sweep: read → normalize → time-filter → dedupe ──────────────────────────


async def sweep_telegram_news(
    *,
    client: Any | None = None,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
) -> list[NewsArticle]:
    """Read recent messages from the configured channels.

    Returns deduped ``NewsArticle`` objects newer than ``lookback_hours``
    (default ``TG_NEWS_SWEEP_HOURS``). Accepts an injected client (any object
    exposing ``iter_messages`` or the news_ingest ``read_messages`` protocol —
    used by tests); when omitted the headless singleton is built (fail-open →
    ``[]``).

    The channel list is resolved by :func:`resolve_channel_sources`: the
    account's own followed/member channels (when ``RMI_TG_NEWS_AUTO_DISCOVER``
    is on and the client exposes ``get_dialogs``) are primary — categorized
    into news / alpha / scam / other (scam left to scam_watch, other skipped)
    and bounded by ``RMI_TG_NEWS_MAX_CHANNELS`` — and the explicit
    ``RMI_TG_NEWS_CHANNELS`` / ``_DEFAULT_CHANNELS`` list is the fallback.

    Dedup is two-layered: in-batch (content hash / source URL) then
    cross-run via the shared Redis content-hash sets
    (:func:`rugmunchbot.integrations.news_ingest.ingest.filter_seen_articles`) so content
    seen in a previous sweep — or ingested by the RAG pipeline — is never
    re-read/re-embedded (Redis down → fail-open, everything treated as fresh).
    """
    if client is None:
        client = await _build_headless_client()
    if client is None:
        logger.warning("tg_news:sweep_skipped_no_client")
        return []

    sources = await resolve_channel_sources(client)
    if not sources:
        logger.info("tg_news:no_sources_configured")
        return []

    lookback = max(1, lookback_hours or TG_NEWS_SWEEP_HOURS)
    per_channel = max(5, per_channel_limit or TG_NEWS_PER_CHANNEL_LIMIT)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)

    articles: list[NewsArticle] = []
    for source in sources:
        username = str(source.get("username") or "").strip().lstrip("@")
        if not username:
            continue
        category = str(source.get("category") or "crypto_news")
        messages = await read_channel_messages(client, username, per_channel)
        for message in messages:
            if not _inside_window(message, cutoff):
                continue
            article = channel_message_to_article(message, category)
            if article is not None:
                articles.append(article)
    articles = dedupe_articles(articles)
    articles, seen_skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)
    if seen_skipped:
        logger.info("tg_news:cross_run_dedup skipped=%s kept=%s", seen_skipped, len(articles))
    return articles


# ── Persist to the website news table (news_items) ───────────────────────────


def _news_id_for(article: NewsArticle) -> str:
    """Stable news_id from the canonical t.me URL (message id = dedup key)."""
    digest = hashlib.sha256(article.source_url.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"tg:{digest}"


# Chain keyword map mirrors ``rugmunchbot.integrations.news_ingest._detect_chains`` so TG
# articles carry the same chain tags as RSS articles on the website.
_CHAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "solana": ("solana", "sol ", "$sol"),
    "ethereum": ("ethereum", "eth ", "$eth", "ether"),
    "bitcoin": ("bitcoin", "btc ", "$btc"),
    "base": ("base ", "basechain", "coinbase base"),
    "arbitrum": ("arbitrum", "arb "),
    "polygon": ("polygon", "matic"),
    "bsc": ("bsc", "bnb chain", "binance smart chain"),
    "tron": ("tron", "trx "),
    "avalanche": ("avalanche", "avax"),
}


def _detect_chains(text: str) -> list[str]:
    """Ordered chain mentions found in article text."""
    lowered = (text or "").lower()
    return [
        chain for chain, keywords in _CHAIN_KEYWORDS.items() if any(k in lowered for k in keywords)
    ]


def _tokens_from_entities(entities: list[str]) -> list[str]:
    """Map ``$TICKER`` entities to the bare ticker list the site expects."""
    return [entity[1:] for entity in entities if entity.startswith("$")]


def _article_to_news_item(article: NewsArticle) -> Any | None:
    """Map a ``NewsArticle`` to the catalog ``NewsItem`` row shape (fail-open).

    Reuses ``rugmunchbot.integrations.news_ratings.rate_article`` (deterministic heuristic —
    the same rating card the RSS ingest writes) so TG articles get the same
    bias/sentiment/factual/tier columns on the website.
    """
    try:
        from pydantic import HttpUrl

        from rugmunchbot.integrations.catalog import NewsItem, utcnow
        from rugmunchbot.integrations.news_ratings import rate_article

        rating = rate_article(
            title=article.title, content=article.body, source=article.source_channel
        )
        published = article.posted_at if article.posted_at is not None else utcnow()
        return NewsItem(
            news_id=_news_id_for(article),
            url=HttpUrl(article.source_url),
            title=article.title,
            summary=article.body,
            body_markdown=article.body,
            source=article.source_channel,
            published_at=published,
            ingested_at=utcnow(),
            chains_mentioned=_detect_chains(article.body),
            tokens_mentioned=_tokens_from_entities(article.entities),
            sentiment_score=rating.sentiment_score / 100.0,  # -1..1 column scale
            bias=rating.bias,
            sentiment_label=rating.sentiment_label,
            factual_rating=rating.factual_rating,
            source_tier=rating.source_tier,
            rating_confidence=rating.confidence,
        )
    except Exception as exc:  # fail-open: a malformed article never aborts the batch
        logger.warning(
            "tg_news:article_build_failed title=%s err=%s", article.title[:60], str(exc)[:160]
        )
        return None


async def persist_articles(articles: list[NewsArticle]) -> dict[str, int]:
    """Insert new articles into ``news_items`` (website feed), deduped by PK.

    Fail-open: Postgres outage or per-item errors log and are counted; the
    caller still posts the Telegram digest. Returns
    ``{"ingested", "duplicate", "errors"}``.
    """
    counts = {"ingested": 0, "duplicate": 0, "errors": 0}
    if not TG_NEWS_PERSIST:
        logger.info("tg_news:persist_disabled")
        return counts
    try:
        from rugmunchbot.integrations.catalog import get_catalog

        catalog = get_catalog()
        await catalog._init_stores()  # catalog store probe (same as RSS ingest)
        if not catalog._health.postgres:
            logger.warning("tg_news:persist_skipped_postgres_unavailable")
            return counts

        items: list[tuple[NewsArticle, Any]] = []
        for article in articles:
            item = _article_to_news_item(article)
            if item is None:
                counts["errors"] += 1
            else:
                items.append((article, item))
        if not items:
            return counts

        async with catalog._pg_pool.acquire() as conn:
            for article, item in items:
                try:
                    existing = await conn.fetchval(
                        "SELECT 1 FROM news_items WHERE news_id=$1", item.news_id
                    )
                    if existing:
                        counts["duplicate"] += 1
                        continue
                    await conn.execute(
                        """
                        INSERT INTO news_items (
                            news_id, url, title, summary, body_markdown,
                            source, published_at, ingested_at,
                            chains_mentioned, tokens_mentioned, sentiment_score,
                            bias, sentiment_label, factual_rating, source_tier,
                            rating_confidence
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                        """,
                        item.news_id,
                        str(item.url),
                        item.title,
                        item.summary,
                        item.body_markdown,
                        item.source,
                        item.published_at,
                        item.ingested_at,
                        item.chains_mentioned,
                        item.tokens_mentioned,
                        item.sentiment_score,
                        item.bias,
                        item.sentiment_label,
                        item.factual_rating,
                        item.source_tier,
                        item.rating_confidence,
                    )
                    counts["ingested"] += 1
                    # Record in the shared Redis content-hash sets only AFTER a
                    # real insert, so a transient Postgres outage never hides
                    # an un-persisted story from the next sweep (fail-open).
                    mark_articles_ingested([article], DEFAULT_DEDUP_COLLECTION)
                except Exception:  # fail-open per item
                    counts["errors"] += 1
                    logger.debug(
                        "tg_news:item_insert_failed news_id=%s", item.news_id, exc_info=True
                    )
    except Exception as exc:  # fail-open: an outage never crashes the job
        logger.warning("tg_news:persist_failed err=%s", str(exc)[:200])
        counts["errors"] = len(articles)
    return counts


# ── Post to the bot's news channel ───────────────────────────────────────────


def _article_to_post_dict(article: NewsArticle) -> dict[str, Any]:
    """Shape a ``NewsArticle`` for ``format_news_post``."""
    return {
        "title": article.title,
        "summary": article.body,
        "url": article.source_url,
        "source": article.source_channel,
        "sentiment_label": "",
        "factual_rating": "",
    }


# ── Website feed injection (additive, fail-open) ────────────────────────────


#: The Redis full-feed key the website's ``/api/v1/news/feed`` +
#: ``/api/v1/news/headlines`` endpoints serve (populated by the RSS/API
#: aggregator ``app/databus/news_provider.py``). TG articles are merged into
#: it additively so they surface on the main feed with their channel
#: attribution; the Postgres-backed ``/api/v1/news/trending`` route already
#: reads ``news_items`` directly.
_NEWS_FEED_CACHE_KEY = "rmi:news:full_feed"
_NEWS_FEED_CACHE_MAX = 50
_TG_ARTICLE_KIND = "telegram"


async def _redis_json_get(key: str) -> dict[str, Any] | None:
    """Read a JSON dict from Redis (fail-open → None)."""
    r = get_redis_async()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:  # fail-open by contract
        logger.debug("tg_news:redis_get_failed key=%s", key, exc_info=True)
        return None


async def _redis_json_set(key: str, data: dict[str, Any], ttl: int) -> bool:
    """Write a JSON dict to Redis. Returns True on success; fail-open → False."""
    r = get_redis_async()
    if r is None:
        return False
    try:
        await r.set(key, json.dumps(data, default=str), ex=ttl)
        return True
    except Exception:  # fail-open by contract
        logger.debug("tg_news:redis_set_failed key=%s", key, exc_info=True)
        return False


def _article_to_feed_item(article: NewsArticle) -> dict[str, Any]:
    """Shape a TG article for the website full-feed cache (with attribution)."""
    return {
        "id": _news_id_for(article),
        "title": article.title,
        "url": article.source_url,
        "description": article.body[:300],
        "source": article.source_channel,
        "published_at": (article.posted_at or datetime.now(UTC)).isoformat(),
        "category": article.category or "news",
        "sentiment": "neutral",
        "kind": _TG_ARTICLE_KIND,
        "tier": "telegram",
    }


async def inject_into_news_cache(articles: list[NewsArticle]) -> int:
    """Merge freshly-ingested TG articles into the website full-feed cache.

    The main feed (``/api/v1/news/feed`` + ``/headlines``) is served from the
    Redis cache populated by ``rugmunchbot.integrations.news_provider`` (RSS + API only),
    so TG items would otherwise be invisible there — this prepends them with
    ``kind="telegram"`` + channel attribution. The Postgres-backed
    ``/api/v1/news/trending`` + ``POST /api/v1/news`` routes serve TG items
    directly from ``news_items`` regardless.

    Additive + fail-open: Redis down → returns 0; the aggregator's next
    periodic refresh replaces the cache wholesale and the injection re-applies
    on the next sweep. Returns the number of articles injected.
    """
    if not articles:
        return 0
    feed = await _redis_json_get(_NEWS_FEED_CACHE_KEY)
    existing: list[dict[str, Any]] = []
    if feed is not None:
        headlines = feed.get("headlines")
        if isinstance(headlines, dict):
            existing = list(headlines.get("articles") or [])
    ids = {str(a.get("id") or "") for a in existing}
    added = 0
    for article in articles:
        item = _article_to_feed_item(article)
        if item["id"] in ids:
            continue
        ids.add(item["id"])
        existing.insert(0, item)
        added += 1
        if len(existing) > _NEWS_FEED_CACHE_MAX:
            existing = existing[:_NEWS_FEED_CACHE_MAX]
    if not added:
        return 0
    merged = dict(feed or {})
    merged["headlines"] = {
        "articles": existing,
        "total": len(existing),
        "source": "rmi_news_aggregator+tg",
        "free": True,
    }
    merged["generated_at"] = datetime.now(UTC).isoformat()
    if not await _redis_json_set(_NEWS_FEED_CACHE_KEY, merged, 300):
        logger.info("tg_news:feed_cache_inject_skipped_redis_down added=%s", added)
        return 0
    logger.info("tg_news:feed_cache_injected added=%s total=%s", added, len(existing))
    return added


# ── Lightweight market-awareness snapshot (additive, fail-open) ─────────────


#: Redis key the bot's chat context can pull for a lightweight "what's moving"
#: snapshot built directly from the sweep (headlines + trending tokens).
#: ``rugmunchbot.daily_context`` already covers the chat
#: context via ``/api/v1/news/trending`` (which includes TG items); this key
#: is the sweep's own direct artifact, refreshed every run.
_MARKET_AWARENESS_KEY = "rmi:news:market_awareness"
_MARKET_AWARENESS_TTL = 3600
_MARKET_AWARENESS_HEADLINES = 5
_MARKET_AWARENESS_TOKENS = 8


async def publish_market_awareness(articles: list[NewsArticle], scored: list[Any]) -> bool:
    """Write a lightweight market-awareness snapshot to Redis.

    Snapshot shape::

        {
            "headlines": [{"title", "source", "url"}, ...],   # top by importance
            "trending_tokens": ["SOL", "PEPE", ...],          # most-mentioned $SYMBOLs
            "article_count": int,
            "generated_at": ISO-8601,
            "source": "tg_news_sweep",
        }

    Pure selection (``importance.top_headlines`` / ``trending_tokens``) over
    the already-scored sweep; the only I/O is one fail-open Redis write.
    Returns True when the snapshot was written.
    """
    if not articles:
        return False
    try:
        headlines = [
            {
                "title": article.title,
                "source": article.source_channel,
                "url": article.source_url,
            }
            for article in top_headlines(list(scored), top_n=_MARKET_AWARENESS_HEADLINES)
        ]
        tokens = trending_tokens(articles, top_n=_MARKET_AWARENESS_TOKENS)
        snapshot = {
            "headlines": headlines,
            "trending_tokens": tokens,
            "article_count": len(articles),
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "tg_news_sweep",
        }
        if not await _redis_json_set(_MARKET_AWARENESS_KEY, snapshot, _MARKET_AWARENESS_TTL):
            logger.info("tg_news:market_awareness_skipped_redis_down articles=%s", len(articles))
            return False
        logger.info(
            "tg_news:market_awareness_published articles=%s tokens=%s", len(articles), len(tokens)
        )
        return True
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_news:market_awareness_failed err=%s", str(exc)[:200])
        return False


def _category_is_news(category: str) -> bool:
    """True when a swept article belongs on the news channel (not alpha)."""
    return category != "alpha"


async def post_tg_news_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Sweep TG channels → persist to website → score importance → route digests.

    Returns 1 when a digest was posted (news or alpha), 0 otherwise. Fails
    open at every step: missing chat ids, no client, empty sweep, Postgres
    outage, or a Telegram send error all log and return without raising.

    Pipeline order (efficiency contract):
    ``read (bounded) → normalize → dedupe (content hash, in-batch + Redis
    cross-run) → store (news_items + Redis dedup mark) → reranker importance
    → route``.

    Routing: **news**-categorized articles are persisted and posted to the
    news channel; **alpha**-categorized articles are persisted and posted to
    the alpha channel (when ``_chat_id("alpha")`` resolves). Every swept
    article is persisted (store everything); only the articles
    ``select_important`` keeps (rerank importance >=
    ``RMI_NEWS_IMPORTANCE_THRESHOLD``, or the top-N under the heuristic
    fallback when Infinity /rerank is down) are posted — per category.

    Additive side-effects (all fail-open):
    * freshly-persisted TG items are merged into the website full-feed Redis
      cache (``inject_into_news_cache``) so ``/api/v1/news/feed`` +
      ``/headlines`` surface them with attribution;
    * a lightweight market-awareness snapshot (top headlines + trending
      tokens) is published to ``rmi:news:market_awareness`` for the bot's
      chat context (``publish_market_awareness``).
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from rugmunchbot.channels.scheduler import (
        _chat_id,
        _dedupe_key,
        _remember,
        _safe_send,
    )

    news_chat_id = _chat_id("news")
    alpha_chat_id = _chat_id("alpha")
    if not news_chat_id and not alpha_chat_id:
        return 0

    articles = await sweep_telegram_news()
    if not articles:
        logger.debug("tg_news:no_fresh_articles")
        return 0

    counts = await persist_articles(articles)
    logger.info(
        "tg_news:sweep_done articles=%s ingested=%s duplicate=%s errors=%s",
        len(articles),
        counts["ingested"],
        counts["duplicate"],
        counts["errors"],
    )

    scored = await score_importance(articles)
    if not scored:
        logger.info("tg_news:importance_scoring_empty articles=%s", len(articles))
        return 0

    posted = 0

    # Route: news category → news channel
    if news_chat_id:
        news_scored = [s for s in scored if _category_is_news(s.article.category)]
        selected = select_important(news_scored, top_n=TG_NEWS_POST_LIMIT)
        fresh = [
            scored_article.article
            for scored_article in selected
            if _remember(
                "tgnews", _dedupe_key("tgnews", _article_to_post_dict(scored_article.article))
            )
        ]
        if fresh:
            ok = await _safe_send(
                ctx.bot,
                news_chat_id,
                format_news_post([_article_to_post_dict(a) for a in fresh[:TG_NEWS_POST_LIMIT]]),
            )
            if ok:
                logger.info("tg_news:posted_news count=%s", min(len(fresh), TG_NEWS_POST_LIMIT))
                posted += 1

    # Route: alpha category → alpha channel (additive; no alpha chat id → skip)
    if alpha_chat_id:
        alpha_scored = [s for s in scored if s.article.category == "alpha"]
        selected_alpha = select_important(alpha_scored, top_n=TG_NEWS_POST_LIMIT)
        fresh_alpha = [
            scored_article.article
            for scored_article in selected_alpha
            if _remember(
                "tgalpha", _dedupe_key("tgalpha", _article_to_post_dict(scored_article.article))
            )
        ]
        if fresh_alpha:
            ok = await _safe_send(
                ctx.bot,
                alpha_chat_id,
                format_news_post(
                    [_article_to_post_dict(a) for a in fresh_alpha[:TG_NEWS_POST_LIMIT]]
                ),
            )
            if ok:
                logger.info(
                    "tg_news:posted_alpha count=%s", min(len(fresh_alpha), TG_NEWS_POST_LIMIT)
                )
                posted += 1

    if not posted:
        logger.debug("tg_news:all_already_posted")

    # Additive, fail-open side-effects.
    await inject_into_news_cache(articles)
    await publish_market_awareness(articles, scored)

    return 1 if posted else 0


__all__ = [
    "TG_NEWS_AUTO_DISCOVER",
    "TG_NEWS_CHANNELS",
    "TG_NEWS_ENABLED",
    "TG_NEWS_INTERVAL_MINUTES",
    "TG_NEWS_MAX_CHANNELS",
    "TG_NEWS_PER_CHANNEL_LIMIT",
    "TG_NEWS_POST_LIMIT",
    "TG_NEWS_SWEEP_HOURS",
    "channel_sources",
    "classify_channel_category",
    "inject_into_news_cache",
    "persist_articles",
    "post_tg_news_update",
    "publish_market_awareness",
    "resolve_channel_sources",
    "sweep_telegram_news",
]

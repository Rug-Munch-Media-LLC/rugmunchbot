"""Security keyword search — GetXAPI advanced_search → news + RAG pipeline.

Why this exists
---------------
The X-ingest lane (``tools.x_ingest``) only reads tweets from a fixed set of
security accounts. Breaking security news often breaks FIRST on accounts we
don't follow, or on a keyword that no single account covers. This module is
the **keyword-search** complement: it runs GetXAPI's ``tweet/advanced_search``
(``tools.getxapi.search_tweets``) against a rotating set of security queries
(exploit / hack / drained / rug pull / vulnerability / audit / bug bounty /
scam / honeypot / flash loan / MEV / bridge / DEFI), normalizes the hits into
the shared ``NewsArticle`` shape, dedupes through the same in-batch +
cross-run Redis sets the TG news sweep and X-ingest use, and feeds the
survivors into the RAG ``news_v2`` collection — so RAG learns about breaking
security news even when it comes from an account we don't track.

Budget doctrine (GetXAPI is metered, $0.001/call)
-------------------------------------------------
* ONE search call per run — the scheduler fires this every 30 minutes, so
  the steady-state burn is 48 calls/day = $0.048/day. This module NEVER
  paginates and NEVER issues more than one request per
  ``run_security_search()`` invocation.
* The call is gated through ``getxapi_budget.can_afford_call()`` (the shared
  Redis-backed $5 envelope) and metered via ``getxapi.record_call`` — the
  same budget the X-ingest lane uses, so all GetXAPI lanes share one cap.
* Queries rotate round-robin so we don't search the same thing every tick —
  each of the 6 queries is covered roughly every 3 hours.
* Fail-open everywhere: no key, budget exhausted, rate-limited, API error, or
  malformed payload → ``[]`` / zero counts with a debug log. A dead GetXAPI
  never breaks a tick and never raises into the scheduler.

Dedup contract (matches the TG news sweep + X-ingest)
-----------------------------------------------------
* In-batch: ``news_ingest.ingest.dedupe_articles`` (canonical URL / title /
  content hash) collapses the same tweet surfaced under multiple queries.
* Cross-run: ``filter_seen_articles`` against the shared Redis content-hash
  sets (``DEFAULT_DEDUP_COLLECTION`` = ``tg_news``) so a tweet already
  ingested by a previous tick — or by the RAG pipeline — is never re-read /
  re-embedded. Redis down → fail-open, everything treated as fresh.
* Marked ingested ONLY after the RAG feed succeeds
  (``mark_articles_ingested``), so a transient RAG outage never permanently
  hides an un-embedded tweet.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    dedupe_articles,
    filter_seen_articles,
    mark_articles_ingested,
)
from rugmunchbot._vendor.news_ingest.normalize import extract_entities

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

logger = logging.getLogger(__name__)

#: Rotating security search queries. Each run picks ONE (round-robin) so we
#: maximize coverage without hammering the same query every 30 minutes. The
#: ``-is:retweet`` + ``min_faves`` operators keep the signal high (original
#: posts with real engagement) and the noise low.
SECURITY_SEARCH_QUERIES: tuple[str, ...] = (
    '"exploit" OR "hack" OR "drained" OR "rug pull" -is:retweet min_faves:50',
    '"vulnerability" OR "audit" OR "bug bounty" crypto -is:retweet min_faves:20',
    '"scam" OR "honeypot" OR "exit scam" -is:retweet min_faves:30',
    '"flash loan" OR "MEV" OR "sandwich" exploit -is:retweet min_faves:20',
    '"bridge" exploit OR hack -is:retweet min_faves:50',
    '"DEFI" exploit OR hack -is:retweet min_faves:100',
)

#: Minimum tweet length (chars) to be considered substantive security intel.
#: Drops empty / link-only dust from the search results.
SECURITY_SEARCH_MIN_TWEET_CHARS: int = 8
#: RAG collection the security-search lane feeds — the same ``news_v2`` the
#: TG news + X-ingest lanes use, so all three rails share one corpus.
SECURITY_SEARCH_RAG_COLLECTION: str = "news_v2"


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SECURITY_SEARCH_MIN_TWEET_CHARS = _env_int(
    "RMI_SECURITY_SEARCH_MIN_TWEET_CHARS", SECURITY_SEARCH_MIN_TWEET_CHARS
)


def _parse_created_at(value: str | None) -> datetime | None:
    """Best-effort parse of a tweet ``created_at`` into an aware UTC datetime.

    Tolerates ISO-8601 (``2026-08-29T10:00:00Z``) and X's native
    ``Sun Jan 25 13:05:46 +0000 2026`` format (what GetXAPI returns). Returns
    None on any failure (fail-open — the article just carries no timestamp).
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    # ISO-8601 with trailing Z / offset.
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except ValueError:
        pass
    # X native format: "Sun Jan 25 13:05:46 +0000 2026".
    try:
        dt = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(UTC)
    except ValueError:
        return None


def tweet_to_article(tweet: dict[str, Any]) -> NewsArticle | None:
    """Convert one normalized GetXAPI tweet dict into a ``NewsArticle``.

    The input is the normalized shape produced by ``getxapi.normalize_tweet``
    (``tweet_id`` / ``text`` / ``handle`` / ``created_at`` / ``url``) — the
    same shape ``x_ingest.tweet_to_article`` consumes. Returns None for
    hostile rows (no text / too short / no URL) so the caller can skip them.
    ``source_channel`` is the author handle (with ``@`` prefix for display),
    ``source_url`` is the canonical ``x.com/<handle>/status/<id>`` URL — the
    same stable dedup key the TG lane uses for its t.me URLs.
    """
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

    if not isinstance(tweet, dict):
        return None
    text = str(tweet.get("text") or "").strip()
    if len(text) < SECURITY_SEARCH_MIN_TWEET_CHARS:
        return None
    url = str(tweet.get("url") or "").strip()
    if not url:
        return None
    handle = str(tweet.get("handle") or "").strip().lstrip("@")
    posted_at = _parse_created_at(str(tweet.get("created_at") or ""))
    # Title = first line, truncated to the news model's 120-char cap.
    first_line = text.splitlines()[0].strip()
    title = first_line[:120] if first_line else text[:120]
    entities = extract_entities(text)
    return NewsArticle(
        title=title,
        body=text,
        source_channel=f"@{handle}" if handle else "security_search",
        source_url=url,
        category="crypto_news",
        posted_at=posted_at,
        content_hash="",  # computed by dedupe_articles from title+body
        entities=entities,
        media=None,
    )


def _next_query_index() -> int:
    """Round-robin index into ``SECURITY_SEARCH_QUERIES``.

    Persisted via env ``RMI_SECURITY_SEARCH_QUERY_INDEX`` so the rotation
    survives restarts (the scheduler re-reads it each tick and advances it).
    """

    idx = _env_int("RMI_SECURITY_SEARCH_QUERY_INDEX", 0)
    return idx % len(SECURITY_SEARCH_QUERIES)


def _advance_query_index() -> int:
    """Advance the rotation index for the NEXT run (wraps around)."""
    import os

    idx = _next_query_index()
    nxt = (idx + 1) % len(SECURITY_SEARCH_QUERIES)
    os.environ["RMI_SECURITY_SEARCH_QUERY_INDEX"] = str(nxt)
    return nxt


async def fetch_security_search_tweets() -> list[dict[str, Any]]:
    """Run ONE GetXAPI advanced_search against the next rotating query.

    Returns normalized tweet dicts (fail-open ``[]``). Never paginates and
    never issues more than one request per call — the 48-calls/day budget is
    hard. The caller owns dedup / RAG feed.
    """
    try:
        from rugmunchbot.tools import getxapi
        from rugmunchbot.tools.getxapi_budget import can_afford_call
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.debug("security_search:getxapi_import_failed err=%s", str(exc)[:120])
        return []
    if not getxapi.api_key_configured():
        logger.debug("security_search:skipped_no_key")
        return []
    if not can_afford_call():
        logger.debug("security_search:budget_exhausted")
        return []
    query = SECURITY_SEARCH_QUERIES[_next_query_index()]
    try:
        # ``getxapi.search_tweets`` calls /tweet/advanced_search, normalizes
        # the hits, caches in Redis (cached hit = zero calls), and meters the
        # call count for budget tracking.
        tweets = await getxapi.search_tweets(query, count=20)
    except Exception as exc:  # fail-open by contract
        logger.debug("security_search:search_failed err=%s", str(exc)[:120])
        return []
    # Advance the rotation ONLY after a successful call — a failed tick
    # retries the same query next time instead of silently skipping it.
    _advance_query_index()
    logger.info(
        "security_search:fetched query=%.80s tweets=%s",
        query,
        len(tweets),
    )
    return [t for t in tweets if isinstance(t, dict)]


async def run_security_search() -> dict[str, Any]:
    """Search → normalize → dedupe → feed RAG for the rotating security queries.

    Returns ``{"query", "fetched", "articles", "fresh", "skipped", "rag"}``.
    Fails open: no key, empty input, or a RAG outage all return counts
    without raising — the scheduler keeps running and the other rails still
    work.

    Pipeline (mirrors the TG news sweep + X-ingest):
      1. ``fetch_security_search_tweets`` — ONE GetXAPI advanced_search call.
      2. ``tweet_to_article`` — normalize each tweet into a ``NewsArticle``.
      3. ``dedupe_articles`` — in-batch dedup (URL / title / content hash).
      4. ``filter_seen_articles`` — cross-run Redis dedup (fail-open).
      5. ``ingest_into_rag`` — cluster + embed into ``news_v2``.
      6. ``mark_articles_ingested`` — record dedup keys ONLY after RAG feed
         succeeds, so a transient RAG outage never hides an un-embedded tweet.
    """
    query = SECURITY_SEARCH_QUERIES[_next_query_index()]
    counts: dict[str, Any] = {
        "query": query,
        "fetched": 0,
        "articles": 0,
        "fresh": 0,
        "skipped": 0,
        "rag": {},
    }
    tweets = await fetch_security_search_tweets()
    counts["fetched"] = len(tweets)
    if not tweets:
        return counts

    articles: list[NewsArticle] = []
    for tweet in tweets:
        article = tweet_to_article(tweet)
        if article is not None:
            articles.append(article)
    counts["articles"] = len(articles)
    if not articles:
        return counts

    # In-batch dedup (URL / title / content hash) — the same story surfaced
    # under multiple queries collapses to one article.
    articles = dedupe_articles(articles)
    # Cross-run Redis dedup — content seen in a previous tick (or by the RAG
    # pipeline) is never re-read/re-embedded. Fail-open: Redis down → fresh.
    articles, seen_skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)
    counts["skipped"] = seen_skipped
    counts["fresh"] = len(articles)
    if not articles:
        logger.debug("security_search:all_seen")
        return counts

    # Feed RAG (the same function the TG news sweep + X-ingest use) — cluster
    # into stories and embed into ``news_v2``. Fail-open: a RAG outage never
    # blocks the rest of the pipeline.
    try:
        from rugmunchbot.channels.telegram_news import ingest_into_rag

        rag_counts = await ingest_into_rag(articles)
    except Exception as exc:  # fail-open by contract
        logger.warning("security_search:rag_feed_failed err=%s", str(exc)[:200])
        rag_counts = {"errors": len(articles)}
    counts["rag"] = rag_counts

    # Mark ingested ONLY after the RAG feed — a transient RAG outage must not
    # permanently hide an un-embedded tweet (the next tick re-reads it).
    if not (rag_counts.get("errors") or 0):
        marked = mark_articles_ingested(articles, DEFAULT_DEDUP_COLLECTION)
        logger.info(
            "security_search:done query=%.80s fetched=%s articles=%s fresh=%s skipped=%s marked=%s rag=%s",
            query,
            counts["fetched"],
            counts["articles"],
            counts["fresh"],
            counts["skipped"],
            marked,
            rag_counts,
        )
    else:
        logger.info(
            "security_search:rag_failed_not_marked query=%.80s fetched=%s fresh=%s errors=%s",
            query,
            counts["fetched"],
            counts["fresh"],
            rag_counts.get("errors", 0),
        )
    # Budget visibility: report the GetXAPI call count + remaining envelope so
    # the 48-calls/day burn is observable in logs (the getxapi module meters
    # each call into the durable ledger; this is the per-tick readout).
    try:
        from rugmunchbot.tools import getxapi

        budget = getxapi.budget_remaining()
        logger.info(
            "security_search:budget calls=%s remaining=%s days_left=%s",
            budget.get("total_calls", 0),
            budget.get("calls_remaining", 0),
            budget.get("days_left", 0),
        )
    except Exception as exc:  # fail-open by contract
        logger.debug("security_search:budget_log_failed err=%s", str(exc)[:120])
    return counts


__all__ = [
    "SECURITY_SEARCH_MIN_TWEET_CHARS",
    "SECURITY_SEARCH_QUERIES",
    "SECURITY_SEARCH_RAG_COLLECTION",
    "fetch_security_search_tweets",
    "run_security_search",
    "tweet_to_article",
]

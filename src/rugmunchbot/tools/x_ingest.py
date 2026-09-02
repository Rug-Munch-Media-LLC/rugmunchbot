"""X/Twitter content ingestion — crypto security accounts → news + RAG pipeline.

Why this exists
---------------
The bot previously had NO first-party X content rail: security intel from
@zachxbt, @peckshield, @blockaid, @certikalert, @slowmist, @beosin,
@chainalysis, @ledger, @metamask (and our own @rugmunchbot) only reached the
news channel when a Telegram channel happened to forward it. This module is
the dedicated X ingestion lane: it pulls real tweets from those accounts via
the ApiPass X source (``tools.apipass_x``), normalizes them into the shared
``NewsArticle`` shape, dedupes through the same in-batch + cross-run Redis
sets the TG news sweep uses, and feeds the survivors into the RAG ``news_v2``
collection (``channels.telegram_news.ingest_into_rag``) so RAG "benefits
from" the X lane exactly like it does the TG news lane.

Budget doctrine (inherited from ``apipass_x``)
----------------------------------------------
* ApiPass task creations are a metered credit burn (default ≤5/hr). This
  module NEVER exceeds ``apipass_x.task_creates_remaining()`` — it fetches
  at most ``X_INGEST_MAX_ACCOUNTS_PER_TICK`` accounts per tick, and each
  account costs 2 creates (handle→id resolve + timeline). When the budget is
  exhausted the tick returns ``[]`` and the next tick resumes.
* Fail-open everywhere: no key, rate-limited, API error, or malformed
  payload → ``[]`` / zero counts with a debug log. A dead ApiPass never
  breaks a tick and never raises into the scheduler.

Dedup contract (matches the TG news sweep)
------------------------------------------
* In-batch: ``news_ingest.ingest.dedupe_articles`` (canonical URL / title /
  content hash) collapses the same tweet surfaced under multiple accounts.
* Cross-run: ``filter_seen_articles`` against the shared Redis content-hash
  sets (``DEFAULT_DEDUP_COLLECTION`` = ``tg_news``) so a tweet already
  ingested by a previous X tick — or by the RAG pipeline — is never
  re-read/re-embedded. Redis down → fail-open, everything treated as fresh.
* Marked ingested ONLY after the RAG feed succeeds (``mark_articles_ingested``),
  so a transient RAG outage never permanently hides an un-embedded tweet.
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

#: Crypto security / wallet-security accounts whose tweets are security intel.
#: Each is a first-party source of exploit / hack / scam / wallet-security
#: news — exactly the content the news + RAG lanes should learn from.
X_SECURITY_ACCOUNTS: tuple[str, ...] = (
    "zachxbt",
    "peckshield",
    "blockaid",
    "certikalert",
    "slowmist",
    "beosin",
    "chainalysis",
    "ledger",
    "metamask",
    "rugmunchbot",
)

#: Max accounts fetched per tick (each costs 2 ApiPass creates). Default 3 —
#: a modest governor so a small credit envelope lasts; env-overridable.
X_INGEST_MAX_ACCOUNTS_PER_TICK: int = 3
#: Tweets pulled per account per tick (bounded; the timeline endpoint caps at
#: 50 anyway).
X_INGEST_TWEETS_PER_ACCOUNT: int = 10
#: Minimum tweet length (chars) to be considered substantive security intel.
#: Terse one-liners like "exploited" are still kept — the gate only drops
#: empty / link-only dust.
X_INGEST_MIN_TWEET_CHARS: int = 8
#: RAG collection the X lane feeds — the same ``news_v2`` the TG news lane
#: uses, so both rails share one searchable news corpus.
X_INGEST_RAG_COLLECTION: str = "news_v2"


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


X_INGEST_MAX_ACCOUNTS_PER_TICK = _env_int(
    "RMI_X_INGEST_MAX_ACCOUNTS_PER_TICK", X_INGEST_MAX_ACCOUNTS_PER_TICK
)
X_INGEST_TWEETS_PER_ACCOUNT = _env_int(
    "RMI_X_INGEST_TWEETS_PER_ACCOUNT", X_INGEST_TWEETS_PER_ACCOUNT
)
X_INGEST_MIN_TWEET_CHARS = _env_int("RMI_X_INGEST_MIN_TWEET_CHARS", X_INGEST_MIN_TWEET_CHARS)


def _parse_created_at(value: str | None) -> datetime | None:
    """Best-effort parse of a tweet ``created_at`` into an aware UTC datetime.

    Tolerates ISO-8601 (``2026-08-29T10:00:00Z``) and X's native
    ``Wed Aug 29 10:00:00 +0000 2026`` format. Returns None on any failure
    (fail-open — the article just carries no timestamp).
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
    # X native format: "Wed Aug 29 10:00:00 +0000 2026".
    try:
        dt = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(UTC)
    except ValueError:
        return None


def tweet_to_article(tweet: dict[str, Any], *, account: str) -> NewsArticle | None:
    """Convert one normalized ApiPass tweet dict into a ``NewsArticle``.

    Returns None for hostile rows (no text / too short / no URL) so the
    caller can skip them. ``source_channel`` is the X handle (with ``@``
    prefix for display), ``source_url`` is the canonical ``x.com/<handle>/
    status/<id>`` URL — the same stable dedup key the TG lane uses for its
    t.me URLs.
    """
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

    if not isinstance(tweet, dict):
        return None
    text = str(tweet.get("text") or "").strip()
    if len(text) < X_INGEST_MIN_TWEET_CHARS:
        return None
    url = str(tweet.get("url") or "").strip()
    if not url:
        return None
    handle = str(tweet.get("handle") or account or "").strip().lstrip("@")
    posted_at = _parse_created_at(str(tweet.get("created_at") or ""))
    # Title = first line, truncated to the news model's 120-char cap.
    first_line = text.splitlines()[0].strip()
    title = first_line[:120] if first_line else text[:120]
    entities = extract_entities(text)
    return NewsArticle(
        title=title,
        body=text,
        source_channel=f"@{handle}",
        source_url=url,
        category="crypto_news",
        posted_at=posted_at,
        content_hash="",  # computed by dedupe_articles from title+body
        entities=entities,
        media=None,
    )


async def fetch_security_tweets() -> list[dict[str, Any]]:
    """Fetch recent tweets from the monitored security accounts (budget-bounded).

    Round-robins over ``X_SECURITY_ACCOUNTS`` — at most
    ``X_INGEST_MAX_ACCOUNTS_PER_TICK`` accounts per tick, and never more
    ApiPass creates than ``task_creates_remaining()`` allows (2 per account:
    handle→id resolve + timeline). Returns normalized tweet dicts (fail-open
    ``[]``). The caller owns dedup / RAG feed.
    """
    try:
        from rugmunchbot.tools import apipass_x
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.debug("x_ingest:apipass_import_failed err=%s", str(exc)[:120])
        return []
    if not apipass_x.api_key_configured():
        logger.debug("x_ingest:skipped_no_key")
        return []
    # 2 creates per account (handle→id resolve + timeline); clamp to budget.
    budget_accounts = max(0, apipass_x.task_creates_remaining() // 2)
    count = min(X_INGEST_MAX_ACCOUNTS_PER_TICK, budget_accounts)
    if count <= 0:
        logger.debug("x_ingest:budget_exhausted")
        return []

    # Round-robin: start at a rotating offset so every account is eventually
    # swept even when the per-tick cap is small.
    import os

    offset = int(os.getenv("RMI_X_INGEST_ACCOUNT_INDEX", "0")) % len(X_SECURITY_ACCOUNTS)
    accounts = (X_SECURITY_ACCOUNTS[offset:] + X_SECURITY_ACCOUNTS[:offset])[:count]

    tweets: list[dict[str, Any]] = []
    for handle in accounts:
        try:
            batch = await apipass_x.fetch_user_tweets(handle, limit=X_INGEST_TWEETS_PER_ACCOUNT)
        except Exception as exc:  # fail-open by contract
            logger.debug("x_ingest:timeline_failed handle=%s err=%s", handle, str(exc)[:120])
            continue
        for tweet in batch:
            if isinstance(tweet, dict):
                # Ensure the handle is attributed even if the payload omits it.
                tweet.setdefault("handle", handle)
                tweets.append(tweet)
    logger.info("x_ingest:fetched accounts=%s tweets=%s", len(accounts), len(tweets))
    return tweets


async def ingest_x_news() -> dict[str, Any]:
    """Fetch → normalize → dedupe → feed RAG for the X security accounts.

    Returns ``{"fetched", "articles", "fresh", "skipped", "rag"}``. Fails
    open: no key, empty input, or a RAG outage all return counts without
    raising — the scheduler keeps running and the other rails still work.

    Pipeline (mirrors the TG news sweep):
      1. ``fetch_security_tweets`` — budget-bounded pull from the accounts.
      2. ``tweet_to_article`` — normalize each tweet into a ``NewsArticle``.
      3. ``dedupe_articles`` — in-batch dedup (URL / title / content hash).
      4. ``filter_seen_articles`` — cross-run Redis dedup (fail-open).
      5. ``ingest_into_rag`` — cluster + embed into ``news_v2``.
      6. ``mark_articles_ingested`` — record dedup keys ONLY after RAG feed
         succeeds, so a transient RAG outage never hides an un-embedded tweet.
    """
    counts: dict[str, Any] = {"fetched": 0, "articles": 0, "fresh": 0, "skipped": 0, "rag": {}}
    tweets = await fetch_security_tweets()
    counts["fetched"] = len(tweets)
    if not tweets:
        return counts

    articles: list[NewsArticle] = []
    for tweet in tweets:
        article = tweet_to_article(tweet, account=str(tweet.get("handle") or ""))
        if article is not None:
            articles.append(article)
    counts["articles"] = len(articles)
    if not articles:
        return counts

    # In-batch dedup (URL / title / content hash) — same story from multiple
    # accounts collapses to one article.
    articles = dedupe_articles(articles)
    # Cross-run Redis dedup — content seen in a previous tick (or by the RAG
    # pipeline) is never re-read/re-embedded. Fail-open: Redis down → fresh.
    articles, seen_skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)
    counts["skipped"] = seen_skipped
    counts["fresh"] = len(articles)
    if not articles:
        logger.debug("x_ingest:all_seen")
        return counts

    # Feed RAG (the same function the TG news sweep uses) — cluster into
    # stories and embed into ``news_v2``. Fail-open: a RAG outage never
    # blocks the rest of the pipeline.
    try:
        from rugmunchbot.channels.telegram_news import ingest_into_rag

        rag_counts = await ingest_into_rag(articles)
    except Exception as exc:  # fail-open by contract
        logger.warning("x_ingest:rag_feed_failed err=%s", str(exc)[:200])
        rag_counts = {"errors": len(articles)}
    counts["rag"] = rag_counts

    # Mark ingested ONLY after the RAG feed — a transient RAG outage must not
    # permanently hide an un-embedded tweet (the next tick re-reads it).
    if not (rag_counts.get("errors") or 0):
        marked = mark_articles_ingested(articles, DEFAULT_DEDUP_COLLECTION)
        logger.info(
            "x_ingest:done fetched=%s articles=%s fresh=%s skipped=%s marked=%s rag=%s",
            counts["fetched"],
            counts["articles"],
            counts["fresh"],
            counts["skipped"],
            marked,
            rag_counts,
        )
    else:
        logger.info(
            "x_ingest:rag_failed_not_marked fetched=%s fresh=%s errors=%s",
            counts["fetched"],
            counts["fresh"],
            rag_counts.get("errors", 0),
        )
    return counts


__all__ = [
    "X_INGEST_MAX_ACCOUNTS_PER_TICK",
    "X_INGEST_MIN_TWEET_CHARS",
    "X_INGEST_RAG_COLLECTION",
    "X_INGEST_TWEETS_PER_ACCOUNT",
    "X_SECURITY_ACCOUNTS",
    "fetch_security_tweets",
    "ingest_x_news",
    "tweet_to_article",
]

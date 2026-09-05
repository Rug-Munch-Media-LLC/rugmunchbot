"""Trending topics + @cryptorugmuncher mention monitoring — GetXAPI trends + mentions.

Why this exists
---------------
The X-ingest lane (``tools.x_ingest``) and the security-search lane
(``tools.security_search``) both read *security* intel. This module is the
**social pulse** complement: it watches what the crypto ecosystem is talking
about (GetXAPI ``/trends``) and who is talking *about us* (GetXAPI
``/user/mentions`` for ``@cryptorugmuncher``), plus a daily account-status
health check (GetXAPI ``/user/status``) so a suspended / deactivated account
is caught early.

Three fetchers, each budget-gated and fail-open:

* ``fetch_crypto_trends`` — GET /trends, filter for crypto-related trends,
  cache in Redis 30 min (a cached hit costs ZERO calls).
* ``fetch_mentions`` — GET /user/mentions?username=cryptorugmuncher&count=20,
  normalize each hit into the shared ``NewsArticle`` shape, dedupe through the
  same in-batch + cross-run Redis sets the TG news sweep / X-ingest /
  security-search use, and feed the survivors into the RAG ``news_v2``
  collection — so RAG learns what the community is saying about us.
* ``fetch_account_status`` — GET /user/status?username={username}, log a
  WARNING when the account is suspended / deactivated / not found.

Budget doctrine (GetXAPI is metered, $0.001/call)
-------------------------------------------------
* ONE trends call + ONE mentions call per run, plus ONE account-status call
  per monitored account per day. The scheduler fires trends + mentions every
  60 minutes (24 calls/day each) and the account-status sweep once per 24
  hours across the 15 monitored accounts (15 calls/day) → 63 calls/day =
  $0.063/day. This module NEVER paginates and NEVER issues more than one
  request per fetcher invocation.
* Every call is gated through ``getxapi_budget.can_afford_call()`` (the shared
  Redis-backed $5 envelope) and metered via ``getxapi.record_call`` — the same
  budget all GetXAPI lanes share, so all lanes stay under one cap.
* Fail-open everywhere: no key, budget exhausted, rate-limited, API error, or
  malformed payload → ``[]`` / ``None`` / zero counts with a debug log. A dead
  GetXAPI never breaks a tick and never raises into the scheduler.

Dedup contract (matches the TG news sweep + X-ingest + security-search)
-----------------------------------------------------------------------
* In-batch: ``news_ingest.ingest.dedupe_articles`` (canonical URL / title /
  content hash) collapses the same tweet surfaced under multiple mentions.
* Cross-run: ``filter_seen_articles`` against the shared Redis content-hash
  sets (``DEFAULT_DEDUP_COLLECTION`` = ``tg_news``) so a mention already
  ingested by a previous tick — or by the RAG pipeline — is never re-read /
  re-embedded. Redis down → fail-open, everything treated as fresh.
* Marked ingested ONLY after the RAG feed succeeds
  (``mark_articles_ingested``), so a transient RAG outage never permanently
  hides an un-embedded mention.
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

#: The X account whose mentions we monitor (the bot's own handle).
SOCIAL_MONITOR_USERNAME: str = "cryptorugmuncher"

#: Crypto-related keywords used to filter the raw GetXAPI /trends payload down
#: to the trends that matter to a crypto-intel bot. Matched case-insensitively
#: against the trend name / query / url. Broad enough to catch token tickers
#: and protocol names, narrow enough to drop celebrity / sports / politics.
CRYPTO_TREND_KEYWORDS: tuple[str, ...] = (
    "btc",
    "bitcoin",
    "eth",
    "ethereum",
    "sol",
    "solana",
    "crypto",
    "defi",
    "nft",
    "token",
    "coin",
    "altcoin",
    "stablecoin",
    "usdt",
    "usdc",
    "xrp",
    "doge",
    "shib",
    "pepe",
    "meme coin",
    "memecoin",
    "airdrop",
    "blockchain",
    "web3",
    "dex",
    "cex",
    "exchange",
    "wallet",
    "mining",
    "staking",
    "yield",
    "liquidity",
    "rug",
    "scam",
    "hack",
    "exploit",
    "drain",
    "phishing",
    "honeypot",
    "bridge",
    "layer 2",
    "layer2",
    "l2",
    "zk",
    "oracle",
    "gaming",
    "metaverse",
    "binance",
    "coinbase",
    "etf",
    "halving",
    "gas fee",
    "smart contract",
)

#: Minimum tweet length (chars) for a mention to be considered substantive
#: social intel. Drops empty / link-only dust from the mentions payload.
SOCIAL_MONITOR_MIN_TWEET_CHARS: int = 8
#: RAG collection the mention lane feeds — the same ``news_v2`` the TG news,
#: X-ingest, and security-search lanes use, so all rails share one corpus.
SOCIAL_MONITOR_RAG_COLLECTION: str = "news_v2"


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SOCIAL_MONITOR_MIN_TWEET_CHARS = _env_int(
    "RMI_SOCIAL_MONITOR_MIN_TWEET_CHARS", SOCIAL_MONITOR_MIN_TWEET_CHARS
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
    except ValueError as e:
        logger.debug("ts_parse:iso_failed text=%r err=%s", text, e)
    # X native format: "Sun Jan 25 13:05:46 +0000 2026".
    try:
        dt = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(UTC)
    except ValueError:
        return None


def _trend_text(trend: Any) -> str:
    """Best-effort textual content of one GetXAPI trend row (fail-open '')."""
    if not isinstance(trend, dict):
        return ""
    for key in ("name", "trend", "query", "title", "topic"):
        value = trend.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def is_crypto_trend(trend: Any) -> bool:
    """True when a GetXAPI trend row looks crypto-related.

    Matches the trend's name / query / url against ``CRYPTO_TREND_KEYWORDS``
    (case-insensitive substring). Fail-open: a non-dict row is never crypto.
    """
    text = _trend_text(trend).lower()
    if not text:
        return False
    return any(kw in text for kw in CRYPTO_TREND_KEYWORDS)


def tweet_to_article(tweet: dict[str, Any]) -> NewsArticle | None:
    """Convert one normalized GetXAPI mention tweet dict into a ``NewsArticle``.

    The input is the normalized shape produced by ``getxapi.normalize_tweet``
    (``tweet_id`` / ``text`` / ``handle`` / ``created_at`` / ``url``) — the
    same shape ``x_ingest.tweet_to_article`` and
    ``security_search.tweet_to_article`` consume. Returns None for hostile
    rows (no text / too short / no URL) so the caller can skip them.
    ``source_channel`` is the author handle (with ``@`` prefix for display),
    ``source_url`` is the canonical ``x.com/<handle>/status/<id>`` URL — the
    same stable dedup key the other lanes use.
    """
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

    if not isinstance(tweet, dict):
        return None
    text = str(tweet.get("text") or "").strip()
    if len(text) < SOCIAL_MONITOR_MIN_TWEET_CHARS:
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
        source_channel=f"@{handle}" if handle else "social_monitor",
        source_url=url,
        category="crypto_news",
        posted_at=posted_at,
        content_hash="",  # computed by dedupe_articles from title+body
        entities=entities,
        media=None,
    )


async def fetch_crypto_trends() -> list[dict[str, Any]]:
    """Fetch current X trends and filter for crypto-related ones.

    Calls GetXAPI ``GET /trends`` (via ``getxapi.get_trends``, which caches in
    Redis 30 min — a cached hit costs ZERO calls) and keeps only the trends
    that match ``is_crypto_trend``. Fail-open ``[]``. Never paginates and
    never issues more than one request per call — the 24-calls/day budget is
    hard.
    """
    try:
        from rugmunchbot.tools import getxapi
        from rugmunchbot.tools.getxapi_budget import can_afford_call
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.debug("social_monitor:getxapi_import_failed err=%s", str(exc)[:120])
        return []
    if not getxapi.api_key_configured():
        logger.debug("social_monitor:trends_skipped_no_key")
        return []
    if not can_afford_call():
        logger.debug("social_monitor:trends_budget_exhausted")
        return []
    try:
        # ``getxapi.get_trends`` calls /trends, caches in Redis (cached hit =
        # zero calls), and meters the call count for budget tracking.
        trends = await getxapi.get_trends()
    except Exception as exc:  # fail-open by contract
        logger.debug("social_monitor:trends_failed err=%s", str(exc)[:120])
        return []
    crypto = [t for t in trends if is_crypto_trend(t)]
    logger.info("social_monitor:trends_fetched total=%s crypto=%s", len(trends), len(crypto))
    return crypto


async def fetch_mentions(username: str = SOCIAL_MONITOR_USERNAME) -> list[dict[str, Any]]:
    """Fetch recent tweets mentioning one X account (default @cryptorugmuncher).

    Calls GetXAPI ``GET /user/mentions?username={username}&count=20`` (via
    ``getxapi.get_user_mentions``, which caches in Redis 15 min — a cached hit
    costs ZERO calls). Returns normalized tweet dicts (fail-open ``[]``). The
    caller owns dedup / RAG feed.
    """
    try:
        from rugmunchbot.tools import getxapi
        from rugmunchbot.tools.getxapi_budget import can_afford_call
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.debug("social_monitor:getxapi_import_failed err=%s", str(exc)[:120])
        return []
    if not getxapi.api_key_configured():
        logger.debug("social_monitor:mentions_skipped_no_key")
        return []
    if not can_afford_call():
        logger.debug("social_monitor:mentions_budget_exhausted")
        return []
    try:
        # ``getxapi.get_user_mentions`` calls /user/mentions, normalizes the
        # hits, caches in Redis (cached hit = zero calls), and meters the call
        # count for budget tracking.
        tweets = await getxapi.get_user_mentions(username, count=20)
    except Exception as exc:  # fail-open by contract
        logger.debug("social_monitor:mentions_failed err=%s", str(exc)[:120])
        return []
    logger.info("social_monitor:mentions_fetched username=%s tweets=%s", username, len(tweets))
    return [t for t in tweets if isinstance(t, dict)]


async def run_mentions(username: str = SOCIAL_MONITOR_USERNAME) -> dict[str, Any]:
    """Mentions → normalize → dedupe → feed RAG for one X account.

    Returns ``{"username", "fetched", "articles", "fresh", "skipped", "rag"}``.
    Fails open: no key, empty input, or a RAG outage all return counts
    without raising — the scheduler keeps running and the other rails still
    work.

    Pipeline (mirrors the TG news sweep + X-ingest + security-search):
      1. ``fetch_mentions`` — ONE GetXAPI /user/mentions call.
      2. ``tweet_to_article`` — normalize each mention into a ``NewsArticle``.
      3. ``dedupe_articles`` — in-batch dedup (URL / title / content hash).
      4. ``filter_seen_articles`` — cross-run Redis dedup (fail-open).
      5. ``ingest_into_rag`` — cluster + embed into ``news_v2``.
      6. ``mark_articles_ingested`` — record dedup keys ONLY after RAG feed
         succeeds, so a transient RAG outage never hides an un-embedded tweet.
    """
    counts: dict[str, Any] = {
        "username": username,
        "fetched": 0,
        "articles": 0,
        "fresh": 0,
        "skipped": 0,
        "rag": {},
    }
    tweets = await fetch_mentions(username)
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

    # In-batch dedup (URL / title / content hash) — the same mention surfaced
    # multiple times collapses to one article.
    articles = dedupe_articles(articles)
    # Cross-run Redis dedup — content seen in a previous tick (or by the RAG
    # pipeline) is never re-read/re-embedded. Fail-open: Redis down → fresh.
    articles, seen_skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)
    counts["skipped"] = seen_skipped
    counts["fresh"] = len(articles)
    if not articles:
        logger.debug("social_monitor:mentions_all_seen username=%s", username)
        return counts

    # Feed RAG (the same function the TG news sweep + X-ingest +
    # security-search use) — cluster into stories and embed into ``news_v2``.
    # Fail-open: a RAG outage never blocks the rest of the pipeline.
    try:
        from rugmunchbot.channels.telegram_news import ingest_into_rag

        rag_counts = await ingest_into_rag(articles)
    except Exception as exc:  # fail-open by contract
        logger.warning("social_monitor:rag_feed_failed err=%s", str(exc)[:200])
        rag_counts = {"errors": len(articles)}
    counts["rag"] = rag_counts

    # Mark ingested ONLY after the RAG feed — a transient RAG outage must not
    # permanently hide an un-embedded mention (the next tick re-reads it).
    if not (rag_counts.get("errors") or 0):
        marked = mark_articles_ingested(articles, DEFAULT_DEDUP_COLLECTION)
        logger.info(
            "social_monitor:mentions_done username=%s fetched=%s articles=%s fresh=%s skipped=%s marked=%s rag=%s",
            username,
            counts["fetched"],
            counts["articles"],
            counts["fresh"],
            counts["skipped"],
            marked,
            rag_counts,
        )
    else:
        logger.info(
            "social_monitor:mentions_rag_failed_not_marked username=%s fetched=%s fresh=%s errors=%s",
            username,
            counts["fetched"],
            counts["fresh"],
            rag_counts.get("errors", 0),
        )
    return counts


def _is_suspended(status: dict[str, Any] | None) -> bool:
    """True when a GetXAPI /user/status payload indicates a suspended account.

    Tolerates the flat ``{suspended: true}`` shape and a nested ``user`` /
    ``data`` object. Fail-open: a missing / malformed payload is NOT treated
    as suspended (we only warn on an explicit signal).
    """
    if not isinstance(status, dict):
        return False
    # Direct boolean flag.
    if status.get("suspended") is True:
        return True
    if status.get("is_suspended") is True:
        return True
    # Nested user object.
    user = status.get("user")
    if isinstance(user, dict) and (
        user.get("suspended") is True or user.get("is_suspended") is True
    ):
        return True
    # Nested data object.
    data = status.get("data")
    if isinstance(data, dict) and (
        data.get("suspended") is True or data.get("is_suspended") is True
    ):
        return True
    # String status field.
    for key in ("status", "state", "account_status"):
        value = status.get(key)
        if isinstance(value, str) and value.strip().lower() in {
            "suspended",
            "deactivated",
            "deleted",
            "not_found",
            "not found",
            "nonexistent",
        }:
            return True
    return False


async def fetch_account_status(username: str) -> dict[str, Any] | None:
    """Fetch the account status for one X account (GET /user/status).

    Logs a WARNING when the account is suspended / deactivated / not found.
    Fail-open ``None`` (no key, budget exhausted, API error, or malformed
    payload all return None without raising).
    """
    try:
        from rugmunchbot.tools import getxapi
        from rugmunchbot.tools.getxapi_budget import can_afford_call
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.debug("social_monitor:getxapi_import_failed err=%s", str(exc)[:120])
        return None
    if not getxapi.api_key_configured():
        logger.debug("social_monitor:status_skipped_no_key username=%s", username)
        return None
    if not can_afford_call():
        logger.debug("social_monitor:status_budget_exhausted username=%s", username)
        return None
    try:
        # ``getxapi.get_user_status`` calls /user/status and meters the call
        # count for budget tracking.
        status = await getxapi.get_user_status(username)
    except Exception as exc:  # fail-open by contract
        logger.debug("social_monitor:status_failed username=%s err=%s", username, str(exc)[:120])
        return None
    if _is_suspended(status):
        logger.warning(
            "social_monitor:account_suspended username=%s status=%s",
            username,
            str(status)[:200],
        )
    else:
        logger.debug("social_monitor:status_ok username=%s", username)
    return status


async def run_account_status_sweep(
    accounts: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Check account status for every monitored account (ONE call each).

    Returns ``{"checked", "suspended"}``. Fails open: no key, budget
    exhausted, or an API error on any account all log and are skipped without
    raising. ``accounts`` defaults to ``getxapi_config.GETXAPI_SECURITY_ACCOUNTS``
    (the 15 monitored security accounts) — one call per account per day.
    """
    if accounts is None:
        try:
            from rugmunchbot.tools.getxapi_config import GETXAPI_SECURITY_ACCOUNTS

            accounts = GETXAPI_SECURITY_ACCOUNTS
        except Exception as exc:  # pragma: no cover — import failure fails open
            logger.debug("social_monitor:accounts_import_failed err=%s", str(exc)[:120])
            accounts = ()
    counts: dict[str, Any] = {"checked": 0, "suspended": 0}
    for username in accounts:
        status = await fetch_account_status(str(username))
        counts["checked"] += 1
        if _is_suspended(status):
            counts["suspended"] += 1
    logger.info(
        "social_monitor:status_sweep checked=%s suspended=%s",
        counts["checked"],
        counts["suspended"],
    )
    return counts


__all__ = [
    "CRYPTO_TREND_KEYWORDS",
    "SOCIAL_MONITOR_MIN_TWEET_CHARS",
    "SOCIAL_MONITOR_RAG_COLLECTION",
    "SOCIAL_MONITOR_USERNAME",
    "fetch_account_status",
    "fetch_crypto_trends",
    "fetch_mentions",
    "is_crypto_trend",
    "run_account_status_sweep",
    "run_mentions",
    "tweet_to_article",
]

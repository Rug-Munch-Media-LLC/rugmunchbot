"""X/Twitter content ingestion — crypto security accounts → news + RAG pipeline.

Why this exists
---------------
The bot previously had NO first-party X content rail: security intel from
@zachxbt, @peckshield, @blockaid, @certikalert, @slowmist, @beosin,
@chainalysis, @ledger, @metamask (and our own @rugmunchbot) only reached the
news channel when a Telegram channel happened to forward it. This module is
the dedicated X ingestion lane: it pulls real tweets from those accounts via
the GetXAPI source (``tools.getxapi``), normalizes them into the shared
``NewsArticle`` shape, dedupes through the same in-batch + cross-run Redis
sets the TG news sweep uses, and feeds the survivors into the RAG ``news_v2``
collection (``channels.telegram_news.ingest_into_rag``) so RAG "benefits
from" the X lane exactly like it does the TG news lane.

Budget doctrine (inherited from ``getxapi``)
--------------------------------------------
* GetXAPI is a flat $0.001/call (~20 tweets per call, no endpoint quotas).
  $5 = 4,500 calls must last 30+ days → max 150 calls/day. This module
  fetches at most ``X_INGEST_MAX_ACCOUNTS_PER_TICK`` accounts per tick, and
  each account costs ONE GetXAPI call (``get_user_tweets`` takes the handle
  directly — no separate handle→id resolve). The caller's scheduler cadence
  (see ``channels.x_ingest``) is the real budget governor.
* Fail-open everywhere: no key, rate-limited, API error, or malformed
  payload → ``[]`` / zero counts with a debug log. A dead GetXAPI never
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

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    dedupe_articles,
    filter_seen_articles,
    mark_articles_ingested,
)
from rugmunchbot._vendor.news_ingest.normalize import extract_entities
from rugmunchbot.tools.getxapi_config import GETXAPI_MONITORED_ACCOUNTS

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

logger = logging.getLogger(__name__)

#: Crypto security / wallet-security accounts whose tweets are security intel.
#: Each is a first-party source of exploit / hack / scam / wallet-security
#: news — exactly the content the news + RAG lanes should learn from.
#:
#: Single source of truth: sourced from ``getxapi_config.GETXAPI_MONITORED_ACCOUNTS``
#: (30 accounts across security / news / DeFi-MEV categories) so the user-tweets
#: rotation lane and the config never drift. Kept as a module-level tuple for
#: backward compatibility with callers that import ``X_SECURITY_ACCOUNTS``.
X_SECURITY_ACCOUNTS: tuple[str, ...] = GETXAPI_MONITORED_ACCOUNTS

#: Max accounts fetched per tick (each costs ONE GetXAPI call). Default 3 —
#: a modest governor so the $5 envelope lasts; env-overridable.
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

# ══════════════════════════════════════════════════════════════════════════
# PRY SCRAPING — FREE fallback for X content (saves the $5 GetXAPI budget)
# ══════════════════════════════════════════════════════════════════════════

#: Pry API base URL (self-hosted, healthy on the host loopback). Same default
#: the RSS lane uses (``news_ingest.ingest._PRY_BASE_URL``) so one service
#: serves both rails. Env-overridable via ``PRYSCRAPER_URL``.
PRY_BASE_URL: str = os.getenv("PRYSCRAPER_URL", "http://127.0.0.1:8005").rstrip("/")
#: Per-request timeout (seconds) for Pry scrapes. X pages are JS-heavy and can
#: take a while to render through FlareSolverr, so this is generous.
PRY_SCRAPE_TIMEOUT_S: float = float(os.getenv("PRY_SCRAPE_TIMEOUT_S", "45"))
#: Redis cache TTL (seconds) for Pry-scraped tweet content — 15 min, matching
#: the GetXAPI tweet cache so a cached hit costs ZERO (no Pry call, no budget).
PRY_CACHE_TWEETS_S: float = float(os.getenv("PRY_CACHE_TWEETS_S", "900"))
#: Redis cache TTL (seconds) for Pry-scraped profile content — 1 hour.
PRY_CACHE_PROFILE_S: float = float(os.getenv("PRY_CACHE_PROFILE_S", "3600"))

#: TIER 1 accounts (30) — the highest-value security/news/DeFi-MEV sources.
#: Polled via GetXAPI (paid, reliable) every 6h. Sourced from the config's
#: security + news + DeFi/MEV categories (the first 30 of the monitored set).
TIER1_ACCOUNTS: tuple[str, ...] = GETXAPI_MONITORED_ACCOUNTS[:30]
#: TIER 2 accounts (100) — KOLs / analysts / funds. Polled via GetXAPI every
#: 24h. These are the next 100 of the monitored set.
TIER2_ACCOUNTS: tuple[str, ...] = GETXAPI_MONITORED_ACCOUNTS[30:130]
#: TIER 3+4 accounts (700+) — long-tail / shiller / emerging. NOT polled
#: individually via GetXAPI; their tweets are caught by keyword search, then
#: the FULL content is scraped via Pry (FREE). This is the budget lever: it
#: saves ~400 GetXAPI calls/day.
TIER3_4_ACCOUNTS: tuple[str, ...] = GETXAPI_MONITORED_ACCOUNTS[130:]


def _pry_cache_key(prefix: str, *parts: str) -> str:
    """Build a namespaced Redis cache key for Pry results."""
    joined = ":".join(str(p).strip().lower() for p in parts if str(p).strip())
    return f"pry:{prefix}:{joined}"


async def _pry_cache_get(key: str) -> Any | None:
    """Read a JSON value from Redis; returns the parsed value or None.

    Fail-open: Redis down / missing / malformed → None (caller hits Pry).
    """
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        client = get_redis_async()
        if client is None:
            return None
        raw = await client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:  # fail-open by contract
        logger.debug("x_ingest:pry_cache_read_failed key=%s", key)
        return None


async def _pry_cache_set(key: str, value: Any, ttl: float) -> None:
    """Write a JSON value to Redis with a TTL (fail-open)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        client = get_redis_async()
        if client is None:
            return
        await client.set(key, json.dumps(value), ex=int(ttl))
    except Exception:  # fail-open by contract
        logger.debug("x_ingest:pry_cache_write_failed key=%s", key)


async def _pry_scrape(url: str) -> dict[str, Any] | None:
    """POST *url* to Pry ``/v1/scrape`` with Cloudflare bypass + JS render.

    Returns the Pry ``data`` dict (``markdown`` / ``metadata``) on success,
    else ``None``. Fail-open by contract: any network / HTTP / parse error
    returns ``None`` so a Pry outage never breaks a tick.

    Uses ``jsRender: true`` because X/Twitter is a JS-heavy SPA — the tweet
    content only exists after client-side rendering (which FlareSolverr
    provides). Mirrors the RSS lane's ``scrape_with_pry`` but with JS render
    enabled for X.
    """
    if not url:
        return None
    try:
        import httpx

        from rugmunchbot.support.pry_auth import pry_headers

        async with httpx.AsyncClient(timeout=PRY_SCRAPE_TIMEOUT_S) as client:
            response = await client.post(
                f"{PRY_BASE_URL}/v1/scrape",
                headers=pry_headers(),
                json={"url": url, "bypassCloudflare": True, "jsRender": True},
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # fail-open by contract
        logger.debug("x_ingest:pry_scrape_failed url=%s err=%s", url, str(exc)[:160])
        return None

    if not isinstance(body, dict):
        return None
    # Pry returns the payload under ``data`` on success; tolerate both shapes.
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return data if isinstance(data, dict) else None


def _tweet_id_from_url(tweet_url: str) -> str:
    """Extract the numeric tweet id from an x.com/twitter status URL."""
    m = re.search(r"/status/(\d+)", tweet_url or "")
    return m.group(1) if m else ""


def _handle_from_url(tweet_url: str) -> str:
    """Extract the @handle from an x.com/<handle>/status/<id> URL."""
    m = re.search(r"x\.com/([^/]+)/status/", tweet_url or "")
    return m.group(1).strip().lstrip("@") if m else ""


def _parse_int(value: Any) -> int:
    """Best-effort int parse (strips commas / K / M suffixes). Fail-open 0."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return 0
    mult = 1
    if text[-1:].lower() == "k":
        mult, text = 1000, text[:-1]
    elif text[-1:].lower() == "m":
        mult, text = 1_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except (TypeError, ValueError):
        return 0


def _extract_engagement(markdown: str) -> dict[str, int]:
    """Pull like / retweet / reply counts out of a rendered X page.

    X pages rendered to markdown surface engagement as labelled numbers
    (e.g. ``1,234 Likes``, ``56 Reposts``, ``12 Replies``). We scan for the
    first number preceding each label. Tolerant: any missing metric → 0.
    """
    text = markdown or ""
    out = {"like_count": 0, "retweet_count": 0, "reply_count": 0}

    def _find(labels: tuple[str, ...]) -> int:
        for label in labels:
            # Number immediately before the label, e.g. "1,234 Likes".
            m = re.search(r"([\d.,]+[kKmM]?)\s*" + re.escape(label), text, re.IGNORECASE)
            if m:
                return _parse_int(m.group(1))
        return 0

    out["like_count"] = _find(("likes", "like", "hearts", "favorites"))
    out["retweet_count"] = _find(("reposts", "retweets", "retweet", "reposts"))
    out["reply_count"] = _find(("replies", "reply", "comments"))
    return out


def _extract_tweet_text(markdown: str, tweet_url: str) -> str:
    """Best-effort extraction of the tweet body from a rendered X page.

    X tweet pages render the post body as the first substantial paragraph of
    markdown. We strip the leading author line (``@handle`` / ``Name``) and
    any trailing engagement / metadata lines, returning the clean tweet text.
    """
    text = (markdown or "").strip()
    if not text:
        return ""
    # Drop the first line if it's just an @handle or a bare name (author line).
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and re.fullmatch(r"@?[\w.]+", lines[0]):
        lines = lines[1:]
    # Join the remaining lines up to a reasonable tweet length.
    body = " ".join(lines)
    # Cut at common trailing markers (engagement / metadata / "Show more").
    for marker in ("Show more", "·", "Replying to", "Retweeted"):
        idx = body.find(marker)
        if idx > 0:
            body = body[:idx]
    # Cut at a trailing engagement label (e.g. "1,234 Likes").
    m = re.search(
        r"\s[\d.,]+[kKmM]?\s*(?:Likes?|Reposts?|Retweets?|Replies?|Comments?)\s*$",
        body,
        re.IGNORECASE,
    )
    if m:
        body = body[: m.start()]
    return body.strip()[:500]


def _extract_created_at(markdown: str) -> str:
    """Best-effort extraction of a tweet timestamp from a rendered X page.

    X renders timestamps like ``2:30 PM · Sep 2, 2026`` or ISO strings. We
    return the raw matched string (the pipeline's ``_parse_created_at``
    normalizes it); empty string when not found (fail-open).
    """
    text = markdown or ""
    # ISO-8601 first.
    m = re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?", text)
    if m:
        return m.group(0)
    # X native: "2:30 PM · Sep 2, 2026".
    m = re.search(r"\d{1,2}:\d{2}\s*[APap][Mm]\s*[·•]\s*[A-Za-z]{3}\s+\d{1,2},\s+\d{4}", text)
    if m:
        return m.group(0)
    return ""


async def scrape_tweet_with_pry(tweet_url: str) -> dict[str, Any] | None:
    """Scrape one X tweet via Pry (FREE) and return it in GetXAPI format.

    POSTs *tweet_url* to Pry ``/v1/scrape`` (Cloudflare bypass + JS render),
    parses the rendered markdown for tweet text / author / date / engagement,
    and returns a dict matching the GetXAPI normalized tweet shape so the
    downstream pipeline (``tweet_to_article``) works unchanged.

    Fail-open: any error (network, HTTP, parse, no text) returns ``None`` so a
    Pry outage never breaks a tick. Results are Redis-cached 15 min (a cached
    hit costs ZERO — no Pry call, no budget).

    Returned shape (matches ``getxapi.normalize_tweet``):
    ``{tweet_id, text, handle, author_name, created_at, retweet_count,
    like_count, reply_count, quote_count, url, source}`` with ``source="pry"``.
    """
    tweet_url = str(tweet_url or "").strip()
    if not tweet_url:
        return None
    tweet_id = _tweet_id_from_url(tweet_url)
    if not tweet_id:
        logger.debug("x_ingest:pry_tweet_no_id url=%s", tweet_url)
        return None

    cache_key = _pry_cache_key("tweet", tweet_id)
    cached = await _pry_cache_get(cache_key)
    if cached is not None:
        return cached

    data = await _pry_scrape(tweet_url)
    if not data:
        return None
    markdown = str(data.get("markdown") or data.get("content") or "").strip()
    if not markdown:
        logger.debug("x_ingest:pry_tweet_empty_markdown url=%s", tweet_url)
        return None

    text = _extract_tweet_text(markdown, tweet_url)
    if len(text) < X_INGEST_MIN_TWEET_CHARS:
        logger.debug("x_ingest:pry_tweet_too_short url=%s", tweet_url)
        return None

    handle = _handle_from_url(tweet_url)
    author_name = str(data.get("metadata", {}).get("title") or "").strip() or handle
    engagement = _extract_engagement(markdown)
    result: dict[str, Any] = {
        "tweet_id": tweet_id,
        "text": text,
        "handle": handle,
        "author_name": author_name,
        "created_at": _extract_created_at(markdown),
        "retweet_count": engagement["retweet_count"],
        "like_count": engagement["like_count"],
        "reply_count": engagement["reply_count"],
        "quote_count": 0,
        "url": tweet_url,
        "source": "pry",
    }
    await _pry_cache_set(cache_key, result, PRY_CACHE_TWEETS_S)
    return result


async def scrape_x_profile_with_pry(username: str) -> dict[str, Any] | None:
    """Scrape one X profile via Pry (FREE) and return it in GetXAPI format.

    Scrapes ``https://x.com/{username}`` via Pry (Cloudflare bypass + JS
    render) and extracts the bio, follower count, and recent tweets. Returns a
    dict shaped like the GetXAPI user-info payload so callers that expect a
    profile dict work unchanged.

    Fail-open: any error returns ``None``. Results are Redis-cached 1 hour.

    Returned shape:
    ``{username, name, bio, followers_count, following_count, tweets_count,
    recent_tweets: [<GetXAPI tweet dicts>], source}`` with ``source="pry"``.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        return None

    cache_key = _pry_cache_key("profile", username)
    cached = await _pry_cache_get(cache_key)
    if cached is not None:
        return cached

    profile_url = f"https://x.com/{username}"
    data = await _pry_scrape(profile_url)
    if not data:
        return None
    markdown = str(data.get("markdown") or data.get("content") or "").strip()
    if not markdown:
        logger.debug("x_ingest:pry_profile_empty_markdown user=%s", username)
        return None

    # Bio: the first substantial paragraph after the name/@handle header.
    bio = ""
    lines = [ln.strip() for ln in markdown.splitlines() if ln.strip()]
    for ln in lines:
        if re.fullmatch(r"@?[\w.]+", ln):
            continue
        if len(ln) >= 8:
            bio = ln[:300]
            break

    # Follower count: first number before "Followers" / "followers".
    followers = 0
    m = re.search(r"([\d.,]+[kKmM]?)\s*[Ff]ollowers", markdown)
    if m:
        followers = _parse_int(m.group(1))

    # Recent tweets: pull status URLs from the page and scrape the first few.
    recent_tweets: list[dict[str, Any]] = []
    status_ids = re.findall(rf"x\.com/{re.escape(username)}/status/(\d+)", markdown)
    seen: set[str] = set()
    for tid in status_ids:
        if tid in seen:
            continue
        seen.add(tid)
        tweet = await scrape_tweet_with_pry(f"https://x.com/{username}/status/{tid}")
        if tweet is not None:
            recent_tweets.append(tweet)
        if len(recent_tweets) >= 5:
            break

    result: dict[str, Any] = {
        "username": username,
        "name": str(data.get("metadata", {}).get("title") or "").strip() or username,
        "bio": bio,
        "followers_count": followers,
        "following_count": 0,
        "tweets_count": 0,
        "recent_tweets": recent_tweets,
        "source": "pry",
    }
    await _pry_cache_set(cache_key, result, PRY_CACHE_PROFILE_S)
    return result


def route_account_to_source(handle: str) -> str:
    """Route one monitored account to its data source (``getxapi`` or ``pry``).

    TIER 1 (first 30) and TIER 2 (next 100) use GetXAPI (paid, reliable);
    TIER 3+4 (the long tail) use Pry scraping (FREE). This is the budget lever
    that keeps the $5 envelope alive: the 700+ long-tail accounts never cost a
    GetXAPI call — their tweets are found by keyword search and the full
    content is scraped via Pry.
    """
    handle = str(handle or "").strip().lstrip("@")
    if handle in TIER1_ACCOUNTS or handle in TIER2_ACCOUNTS:
        return "getxapi"
    return "pry"


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
    except ValueError as e:
        logger.debug("ts_parse:iso_failed text=%r err=%s", text, e)
    # X native format: "Wed Aug 29 10:00:00 +0000 2026".
    try:
        dt = datetime.strptime(text, "%a %b %d %H:%M:%S %z %Y")
        return dt.astimezone(UTC)
    except ValueError:
        return None


def tweet_to_article(tweet: dict[str, Any], *, account: str) -> NewsArticle | None:
    """Convert one normalized GetXAPI tweet dict into a ``NewsArticle``.

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


#: Redis key holding the advancing round-robin offset for the user-tweets lane.
_X_INGEST_ROTATION_KEY = "getxapi:x_ingest_rotation_offset"


def _next_rotation_offset() -> int:
    """Return the next round-robin start offset, advancing it in Redis.

    Each tick INCRs a Redis counter (modulo the account count) so the
    user-tweets lane starts at a different account every run and eventually
    sweeps all 30 monitored accounts. Redis down → fall back to the static
    ``RMI_X_INGEST_ACCOUNT_INDEX`` env override (default 0) so the lane still
    works (fail-open, no rotation persistence).
    """
    import os

    try:
        from rugmunchbot._vendor.redis_client import get_redis

        r = get_redis()
        if r is not None:
            n = int(r.incr(_X_INGEST_ROTATION_KEY) or 0)
            return n % len(X_SECURITY_ACCOUNTS)
    except Exception as exc:  # pragma: no cover — fail-open
        logger.debug("x_ingest:rotation_redis_failed err=%s", str(exc)[:120])
    try:
        return int(os.getenv("RMI_X_INGEST_ACCOUNT_INDEX", "0")) % len(X_SECURITY_ACCOUNTS)
    except (TypeError, ValueError):  # pragma: no cover — malformed env
        return 0


async def fetch_security_tweets() -> list[dict[str, Any]]:
    """Fetch recent tweets from the monitored security accounts (budget-bounded).

    Round-robins over ``X_SECURITY_ACCOUNTS`` — at most
    ``X_INGEST_MAX_ACCOUNTS_PER_TICK`` accounts per tick, and never more
    GetXAPI calls than the remaining $5 budget allows (1 call per account:
    ``get_user_tweets`` takes the handle directly). Returns normalized tweet
    dicts (fail-open ``[]``). The caller owns dedup / RAG feed.
    """
    try:
        from rugmunchbot.tools import getxapi
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.debug("x_ingest:getxapi_import_failed err=%s", str(exc)[:120])
        return []
    if not getxapi.api_key_configured():
        logger.debug("x_ingest:skipped_no_key")
        return []
    # 1 GetXAPI call per account; clamp to the remaining $5 budget.
    budget_accounts = max(0, getxapi.budget_remaining().get("calls_remaining", 0))
    count = min(X_INGEST_MAX_ACCOUNTS_PER_TICK, budget_accounts)
    if count <= 0:
        logger.debug("x_ingest:budget_exhausted")
        return []

    # Round-robin: start at a rotating offset so every account is eventually
    # swept even when the per-tick cap is small. The offset is advanced in
    # Redis (survives restarts) so each tick starts at a different account and
    # all 30 monitored accounts are covered over time. Falls back to a static
    # env override (``RMI_X_INGEST_ACCOUNT_INDEX``) for tests / manual control.
    offset = _next_rotation_offset()
    accounts = (X_SECURITY_ACCOUNTS[offset:] + X_SECURITY_ACCOUNTS[:offset])[:count]

    tweets: list[dict[str, Any]] = []
    for handle in accounts:
        try:
            batch = await getxapi.get_user_tweets(handle, count=X_INGEST_TWEETS_PER_ACCOUNT)
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
    "PRY_BASE_URL",
    "TIER1_ACCOUNTS",
    "TIER2_ACCOUNTS",
    "TIER3_4_ACCOUNTS",
    "X_INGEST_MAX_ACCOUNTS_PER_TICK",
    "X_INGEST_MIN_TWEET_CHARS",
    "X_INGEST_RAG_COLLECTION",
    "X_INGEST_TWEETS_PER_ACCOUNT",
    "X_SECURITY_ACCOUNTS",
    "fetch_security_tweets",
    "ingest_x_news",
    "route_account_to_source",
    "scrape_tweet_with_pry",
    "scrape_x_profile_with_pry",
    "tweet_to_article",
]

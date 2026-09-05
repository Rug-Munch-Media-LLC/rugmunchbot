"""Cross-edition content dedup (2026-09-05).

The Morning Rug and the Midday Market Update both pull ranked stories from
the rugbrain reranker. Without cross-referencing, the same stories appear
in both editions. After the morning edition posts, its story hashes are
recorded in the Redis set ``rag:posted:morning:{YYYY-MM-DD}`` (48h TTL);
the midday fetch excludes anything already in that set.

Fail-open everywhere: a Redis outage must never block an edition.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("rmi_bot.channels.edition_dedup")

#: Redis set key for stories already posted by the morning edition.
MORNING_POSTED_KEY_FMT = "rag:posted:morning:{date}"
#: TTL: two days — covers timezone drift between editions, then self-cleans.
MORNING_POSTED_TTL = 86400 * 2


def story_hash(story: dict[str, Any]) -> str:
    """Stable content hash for a story (URL preferred, title fallback)."""
    basis = str(story.get("url") or story.get("title") or "").strip()
    return hashlib.sha256(basis.encode()).hexdigest()


def morning_posted_key() -> str:
    """Today's morning-posted Redis set key (UTC date)."""
    return MORNING_POSTED_KEY_FMT.format(date=datetime.now(UTC).strftime("%Y-%m-%d"))


def mark_morning_stories(stories: list[dict[str, Any]]) -> None:
    """Record stories used by the morning edition (fail-open, best-effort)."""
    if not stories:
        return
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        r = get_redis()
        if r is None:
            logger.warning("edition_dedup:mark_skipped redis_unavailable")
            return
        key = morning_posted_key()
        with r.pipeline() as pipe:
            pipe.sadd(key, *(story_hash(s) for s in stories))
            pipe.expire(key, MORNING_POSTED_TTL)
            pipe.execute()
        logger.info("edition_dedup:morning_marked count=%d key=%s", len(stories), key)
    except Exception as exc:  # fail-open — never block the edition
        logger.warning("edition_dedup:mark_failed err=%s", str(exc)[:140])


def exclude_morning_stories(
    stories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop stories already posted by the morning edition (fail-open)."""
    if not stories:
        return stories
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        r = get_redis()
        if r is None:
            logger.warning("edition_dedup:filter_skipped redis_unavailable")
            return stories
        key = morning_posted_key()
        if not r.exists(key):
            return stories
        posted = {h.decode() if isinstance(h, bytes) else str(h) for h in r.smembers(key)}
        kept = [s for s in stories if story_hash(s) not in posted]
        dropped = len(stories) - len(kept)
        if dropped:
            logger.info("edition_dedup:midday_excluded dropped=%d kept=%d", dropped, len(kept))
        return kept
    except Exception as exc:  # fail-open — never block the edition
        logger.warning("edition_dedup:filter_failed err=%s", str(exc)[:140])
        return stories

"""Cross-edition content dedup — THREE-WAY (2026-10-17).

The three daily reports (Morning Brief → Midday Market Report → Evening
Wrap) all pull ranked stories from the rugbrain reranker. Without
cross-referencing, the same story appears in two or three editions.

Contract: each edition, after it posts, records its story hashes in a
per-edition Redis set ``rag:posted:{edition}:{YYYY-MM-DD}`` (48h TTL).
The NEXT edition excludes anything already in ANY earlier edition's set
for the same day — so a story appears in AT MOST ONE of the three.

Editions (ordered): ``morning`` → ``midday`` → ``evening``.

Backward compatibility: ``mark_morning_stories`` / ``exclude_morning_stories``
remain as thin aliases over the generic API (the Morning Brief and the
legacy Midday Update call them).

Fail-open everywhere: a Redis outage must never block an edition.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("rmi_bot.channels.edition_dedup")

#: The three editions, in publication order. A story may appear in ONE only.
EDITIONS: tuple[str, ...] = ("morning", "midday", "evening")

#: Redis set key for stories already posted by one edition.
POSTED_KEY_FMT = "rag:posted:{edition}:{date}"
#: TTL: two days — covers timezone drift between editions, then self-cleans.
POSTED_TTL = 86400 * 2

#: Legacy alias (kept for callers/tests that reference the old name).
MORNING_POSTED_KEY_FMT = POSTED_KEY_FMT
MORNING_POSTED_TTL = POSTED_TTL


def story_hash(story: dict[str, Any]) -> str:
    """Stable content hash for a story (URL preferred, title fallback)."""
    basis = str(story.get("url") or story.get("title") or "").strip()
    return hashlib.sha256(basis.encode()).hexdigest()


def _today() -> str:
    """UTC calendar date (YYYY-MM-DD) — the edition's day identity."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def posted_key(edition: str) -> str:
    """Today's Redis set key for *edition* (UTC date)."""
    return POSTED_KEY_FMT.format(edition=str(edition).strip().lower(), date=_today())


def morning_posted_key() -> str:
    """Legacy alias — today's morning-posted Redis set key."""
    return posted_key("morning")


def _client() -> object | None:
    """Lazy sync Redis client (fail-open via None)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis

        return get_redis()
    except Exception as exc:  # fail-open by contract
        logger.debug("edition_dedup:redis_client_failed err=%s", str(exc)[:100])
        return None


def mark_edition_stories(edition: str, stories: list[dict[str, Any]]) -> None:
    """Record stories used by *edition* (fail-open, best-effort)."""
    if not stories:
        return
    r = _client()
    if r is None:
        logger.warning("edition_dedup:mark_skipped redis_unavailable")
        return
    try:
        key = posted_key(edition)
        with r.pipeline() as pipe:
            pipe.sadd(key, *(story_hash(s) for s in stories))
            pipe.expire(key, POSTED_TTL)
            pipe.execute()
        logger.info("edition_dedup:marked edition=%s count=%d key=%s", edition, len(stories), key)
    except Exception as exc:  # fail-open — never block the edition
        logger.warning("edition_dedup:mark_failed err=%s", str(exc)[:140])


def exclude_prior_editions(
    stories: list[dict[str, Any]],
    *,
    before: str,
) -> list[dict[str, Any]]:
    """Drop stories already posted by any edition BEFORE *before* (fail-open).

    ``before`` is the current edition name; every edition earlier in
    :data:`EDITIONS` is consulted. A story already used by an earlier
    edition today is removed, so no story appears in two of the three.
    """
    if not stories:
        return stories
    try:
        order = list(EDITIONS)
        if before not in order:
            return stories
        prior = order[: order.index(before)]
        if not prior:
            return stories
        r = _client()
        if r is None:
            logger.warning("edition_dedup:filter_skipped redis_unavailable")
            return stories
        posted: set[str] = set()
        for edition in prior:
            key = posted_key(edition)
            if not r.exists(key):
                continue
            posted |= {h.decode() if isinstance(h, bytes) else str(h) for h in r.smembers(key)}
        if not posted:
            return stories
        kept = [s for s in stories if story_hash(s) not in posted]
        dropped = len(stories) - len(kept)
        if dropped:
            logger.info(
                "edition_dedup:excluded edition=%s dropped=%d kept=%d",
                before,
                dropped,
                len(kept),
            )
        return kept
    except Exception as exc:  # fail-open — never block the edition
        logger.warning("edition_dedup:filter_failed err=%s", str(exc)[:140])
        return stories


# ── Legacy aliases (Morning Brief + legacy Midday Update call these) ────────


def mark_morning_stories(stories: list[dict[str, Any]]) -> None:
    """Record stories used by the morning edition (fail-open, best-effort)."""
    mark_edition_stories("morning", stories)


def exclude_morning_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop stories already posted by the morning edition (fail-open)."""
    return exclude_prior_editions(stories, before="midday")


__all__ = [
    "EDITIONS",
    "MORNING_POSTED_KEY_FMT",
    "MORNING_POSTED_TTL",
    "POSTED_KEY_FMT",
    "POSTED_TTL",
    "exclude_morning_stories",
    "exclude_prior_editions",
    "mark_edition_stories",
    "mark_morning_stories",
    "morning_posted_key",
    "posted_key",
    "story_hash",
]

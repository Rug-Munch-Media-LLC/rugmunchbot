"""Daily quality-content job — fills the main channel's 8 reranked slots.

Channel-quality overhaul (2026-08-29), deliverable 3: @cryptorugmuncher runs
a 10-post/day editorial budget — 2 fixed market briefs (morning/midday, their
own jobs) + up to 8 reranked quality-content posts from the RAG
content-distribution lane. This module is the quality-content half:

* ``post_daily_quality`` — the JobQueue job. Every run it fetches fresh RAG
  ``main`` drafts (already past the quality/recency/source gates), runs the
  schedule builder (stable-key dedupe → qwen3-4B embed dedupe → deep 4B
  rerank, all fail-open), checks the shared main-channel budget
  (``main_cap``), and posts the single best remaining item — one per run at
  ``RMI_DAILY_QUALITY_INTERVAL_MIN`` (default 120 min → ≤8/day), so the
  channel gets rhythm, never bursts. Successful main posts mirror to
  feed.rugmunch.io (mandatory W30 mirror).
* ``dry_run_daily_schedule`` — the operator/dry-run entry used by
  ``scripts/dry_run_daily_schedule.py``: builds and renders the full
  10-post day (2 market slots + quality slots) WITHOUT sending anything.

Shared dedupe: the same ``rag:`` namespace as the RAG poster, so a story
this job posts is never re-posted by the slow lane, and vice-versa. The
main-cap admission + record pair means failed sends never consume budget
and the total stays ≤ 10/day across every lane.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.formatters._primitives import NewsPost, enforce_site_link
from rugmunchbot.channels.quality_schedule import (
    QUALITY_SLOTS_PER_DAY,
    build_daily_quality_schedule,
    render_schedule,
)
from rugmunchbot.channels.scheduler._core import (
    _chat_id,
    _post_photo_aware,
    _release,
    _remember,
    _safe_send,
    logger,
)
from rugmunchbot.channels.scheduler._posts_rag import (
    RMI_RAG_POST_MIN_SCORE,
    gather_main_drafts,
    mirror_post_to_feed,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

#: Candidates fetched per run (gates discard most of them).
DAILY_QUALITY_CANDIDATES = int(os.getenv("RMI_DAILY_QUALITY_CANDIDATES", "6"))
#: Interval minutes between quality posts (default 120 → max 8/day).
DAILY_QUALITY_INTERVAL_MIN = int(os.getenv("RMI_DAILY_QUALITY_INTERVAL_MIN", "120"))
#: Enable the embed-dedup + rerank steps (fail-open either way).
DAILY_QUALITY_EMBED = os.getenv("RMI_DAILY_QUALITY_EMBED", "true").lower() in ("1", "true", "yes")
DAILY_QUALITY_RERANK = os.getenv("RMI_DAILY_QUALITY_RERANK", "true").lower() in ("1", "true", "yes")

#: In-memory quality-post counter per day (the job's own rhythm tracker;
#: the durable budget gate is ``main_cap``). Restart resets it — main_cap's
#: Redis zset still bounds the day.
_QUALITY_POSTED_TODAY: dict[str, int] = {}


def _quality_posted_today() -> int:
    today = datetime.now(UTC).strftime("%Y%m%d")
    return _QUALITY_POSTED_TODAY.get(today, 0)


def _bump_quality_posted() -> None:
    today = datetime.now(UTC).strftime("%Y%m%d")
    _QUALITY_POSTED_TODAY[today] = _QUALITY_POSTED_TODAY.get(today, 0) + 1
    for stale in [k for k in _QUALITY_POSTED_TODAY if k < today]:
        del _QUALITY_POSTED_TODAY[stale]


async def _fresh_main_drafts() -> list[dict[str, Any]]:
    """RAG main drafts that pass the lane gates, with a bot-owned fallback.

    Fail-open → []: the RAG content-distribution lane is the primary source
    (drafts already past rugbrain's quality/recency/source selection). But if
    the lane is empty, disabled (no ``RMI_RAG_API_KEY``), or its drafts all
    fail the bot-side gates, we fall back to the bot's OWN curated content.

    2026-08-30 unify (task 1): no longer a parallel lane — this delegates to
    ``_posts_rag.gather_main_drafts``, the SINGLE gather→score→gate path
    shared with ``channel_rag``. The fallback (``_fallback_main_drafts``) is
    folded in there and routed through the reranker so the 0.30 importance
    floor applies uniformly (no unscored bypass). Silence is never the
    default.
    """
    return await gather_main_drafts(limit=DAILY_QUALITY_CANDIDATES)


async def post_daily_quality(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post one reranked quality item to main (budget-gated, mirrored).

    Returns 1 when a post shipped, 0 otherwise. Ordering follows the RAG
    poster's P0 fix: the dedupe mark is taken only after the chat id and
    budget resolve, and a failed send releases the mark so the next run
    retries instead of black-holing the draft.
    """
    main_chat_id = _chat_id("main")
    if not main_chat_id:
        logger.debug("daily_quality:skipped_no_main_chat")
        return 0

    # Local rhythm guard: already posted our 8 quality slots today.
    if _quality_posted_today() >= QUALITY_SLOTS_PER_DAY:
        logger.info("daily_quality:budget_exhausted posted_today=%s", _quality_posted_today())
        return 0

    candidates = await _fresh_main_drafts()
    if not candidates:
        logger.debug("daily_quality:no_fresh_candidates")
        return 0

    schedule = await build_daily_quality_schedule(
        candidates,
        posted_today=_quality_posted_today(),
        run_embed_dedup=DAILY_QUALITY_EMBED,
        run_rerank=DAILY_QUALITY_RERANK,
    )
    quality_entries = [e for e in schedule if e.kind == "quality" and e.item is not None]
    if not quality_entries:
        logger.debug("daily_quality:no_quality_entries")
        return 0
    entry = quality_entries[0]
    item = entry.item or {}
    ref = item.get("story_ref") or item.get("text", "")[:60]

    # 2026-08-30 unify (task 2): the 0.30 importance floor applies HERE too —
    # after the score (rerank) step, before the post. ``gather_main_drafts``
    # already floors its output, but the schedule re-reranks, so a candidate
    # re-scored below the floor here must never post. Gate-after-score, not
    # the old gate-before-score bypass.
    if entry.importance < RMI_RAG_POST_MIN_SCORE:
        logger.info(
            "daily_quality:below_floor importance=%.3f min=%.3f ref=%s",
            entry.importance,
            RMI_RAG_POST_MIN_SCORE,
            ref[:60],
        )
        return 0

    # WO-2 hard cap: shared with every main lane — overflow skips this run
    # (the draft stays unposted, retried next run).
    from rugmunchbot.channels.main_cap import main_cap

    allowed, count = await main_cap.admission(importance=entry.importance)
    if not allowed:
        logger.info(
            "daily_quality:main_capped count=%d cap=%d ref=%s", count, main_cap.cap, ref[:60]
        )
        return 0

    dedupe_key = f"daily_quality:{ref}"
    if not _remember("daily_quality", dedupe_key):
        return 0  # already posted by this lane today

    # Site-link contract (2026-08-29): every main post carries the exact
    # feed.rugmunch.io link (the mirror below), and the story's image rides
    # the NewsPost when the draft provides one — quality on main, never a
    # linkless bare-text card.
    text = enforce_site_link(str(item.get("text", "")), site="feed")
    image_url = str(item.get("image_url") or item.get("image") or "").strip()
    post: NewsPost | str = text
    if image_url.startswith(("http://", "https://")):
        news_post = NewsPost(text)
        news_post.image_url = image_url
        post = news_post

    ok = await _post_photo_aware(ctx.bot, main_chat_id, post, _safe_send, content_type="market")
    if not ok:
        _release("daily_quality", dedupe_key)
        return 0

    await main_cap.record()
    _bump_quality_posted()
    logger.info(
        "daily_quality:posted slot=%d importance=%.3f ref=%s",
        entry.slot_index,
        entry.importance,
        ref[:60],
    )

    # Mandatory W30 mirror: every main post lands on feed.rugmunch.io.
    slug = mirror_post_to_feed(
        {
            "text": item.get("text", ""),
            "story_ref": item.get("story_ref", ""),
            "source": item.get("source", item.get("story_ref", "")),
            "kind": "quality",
        }
    )
    if slug:
        logger.info("daily_quality:mirrored slug=%s", slug)
    return 1


async def dry_run_daily_schedule(
    items: list[dict[str, Any]],
    *,
    posted_today: int = 0,
    run_embed_dedup: bool = True,
    run_rerank: bool = True,
) -> str:
    """Build + render the 10-post day WITHOUT sending anything.

    Operator/dry-run entry: dedupes and reranks *items* exactly like the
    live job, then renders the full schedule (2 market slots + quality
    slots, with dedupe notes). Used by ``scripts/dry_run_daily_schedule.py``.
    """
    schedule = await build_daily_quality_schedule(
        items,
        posted_today=posted_today,
        run_embed_dedup=run_embed_dedup,
        run_rerank=run_rerank,
    )
    return render_schedule(schedule)


__all__ = [
    "DAILY_QUALITY_CANDIDATES",
    "DAILY_QUALITY_EMBED",
    "DAILY_QUALITY_INTERVAL_MIN",
    "DAILY_QUALITY_RERANK",
    "dry_run_daily_schedule",
    "post_daily_quality",
]

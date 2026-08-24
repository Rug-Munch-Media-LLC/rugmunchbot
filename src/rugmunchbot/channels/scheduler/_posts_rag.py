"""RAG-drafts posting — intelligence-ranked stories to all channels.

Pulls ready-to-post drafts from rugbrain's content-distribution API
(``channels/content.py::fetch_rag_drafts``) and routes them:

* alerts  → @rmialerts          (critical/high intel only)
* news    → @rmicryptonews      (solid news-value items)
* main    → @cryptorugmuncher   (top stories, full analysis) — every main
  post with ``mirror: true`` is ALSO written to the feed.rugmunch.io
  static site (same machine; atomic file append under /var/www).

Dedup via ``_remember("rag", story_ref)`` — a story never posts twice.
Fail-open everywhere: RAG outage = zero posts, never a crash.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.content import fetch_rag_drafts

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from rugmunchbot.channels.scheduler._core import (
    _chat_id,
    _release,
    _remember,
    _safe_send,
    logger,
)

FEED_DIR = os.getenv("RMI_FEED_DIR", "/var/www/feed.rugmunch.io")
FEED_DATA = os.path.join(FEED_DIR, "data")
MAX_PER_CHANNEL_PER_TICK = int(os.getenv("RMI_RAG_POSTS_PER_TICK", "2"))


def _mirror_to_feed(item: dict[str, Any]) -> str | None:
    """Append a main-channel post to the feed site's JSON store. Returns slug."""
    try:
        os.makedirs(FEED_DATA, exist_ok=True)
        path = os.path.join(FEED_DATA, "posts.json")
        posts: list[dict[str, Any]] = []
        if os.path.exists(path):
            with open(path) as f:
                try:
                    posts = json.load(f)
                except json.JSONDecodeError:
                    posts = []
        slug = f"post-{int(time.time())}"
        posts.insert(
            0,
            {
                "slug": slug,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "text": item.get("text", ""),
                "source": item.get("story_ref", ""),
                "kind": item.get("kind", "story"),
            },
        )
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(posts[:500], f, indent=1)
        os.replace(tmp, path)
        return slug
    except Exception as e:
        logger.debug("feed_mirror_failed err=%s", str(e)[:100])
        return None


async def post_rag_drafts(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Route RAG-ranked drafts to their channels. Returns posted count.

    2026-08-25 P0 fix — ordering: the dedupe mark (``_remember``) used to be
    taken BEFORE the chat-id check and BEFORE the send, so any draft that hit
    a missing chat id or a single failed send was permanently burned (marked
    seen, never posted, never retried — a silent blackhole). Now: chat id is
    resolved first, the send must succeed, and only then is the story marked.
    Cost: a send that fails in two overlapping ticks could double-post; at a
    45-min interval with in-tick sequencing that risk is negligible next to
    losing drafts forever.
    """
    posted_total = 0

    # main FIRST: when rugbrain queues the same story for several channels,
    # the global dedupe (_remember strips one lane prefix) keeps only the
    # first lane to claim it — the flagship channel + feed mirror must win.
    for channel in ("main", "news", "alerts"):
        drafts = await fetch_rag_drafts(channel, limit=MAX_PER_CHANNEL_PER_TICK + 2)
        chat_id = _chat_id(channel)
        if not chat_id:
            logger.debug("rag_drafts_skipped channel=%s reason=no_chat_id", channel)
            continue
        sent_this_channel = 0
        for item in drafts:
            if sent_this_channel >= MAX_PER_CHANNEL_PER_TICK:
                break
            ref = item.get("story_ref") or item.get("text", "")[:60]
            # SINGLE lane prefix ("rag:") — _remember's global namespace
            # strips exactly one "x:" segment, so "rag:<ref>" dedupes the
            # bare <ref> across channels. The old "rag:<channel>:<ref>" left
            # the channel inside the bare key → cross-channel dedupe never
            # fired and one story could post to every channel.
            dedupe_key = f"rag:{ref}"
            if not _remember(f"rag_{channel}", dedupe_key):
                continue

            ok = await _safe_send(ctx.bot, chat_id, item.get("text", ""))
            if not ok:
                # Release the dedupe mark so the next tick retries this draft
                # instead of black-holing it (see docstring).
                _release(f"rag_{channel}", dedupe_key)
                continue

            sent_this_channel += 1
            posted_total += 1

            if channel == "main" and item.get("mirror"):
                slug = _mirror_to_feed(item)
                if slug:
                    logger.info("rag_post_mirrored_to_feed slug=%s", slug)

    if posted_total:
        logger.info("rag_drafts_posted count=%d", posted_total)
    return posted_total


__all__ = ["post_rag_drafts"]

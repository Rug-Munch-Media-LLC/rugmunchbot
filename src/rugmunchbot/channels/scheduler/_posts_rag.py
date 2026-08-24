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
    """Route RAG-ranked drafts to their channels. Returns posted count."""
    posted_total = 0

    for channel in ("alerts", "news", "main"):
        drafts = await fetch_rag_drafts(channel, limit=MAX_PER_CHANNEL_PER_TICK + 2)
        sent_this_channel = 0
        for item in drafts:
            if sent_this_channel >= MAX_PER_CHANNEL_PER_TICK:
                break
            ref = item.get("story_ref") or item.get("text", "")[:60]
            dedupe_key = f"rag:{channel}:{ref}"
            if not _remember(f"rag_{channel}", dedupe_key):
                continue

            chat_id = _chat_id(channel)
            if not chat_id:
                continue
            ok = await _safe_send(ctx.bot, chat_id, item.get("text", ""))
            if not ok:
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

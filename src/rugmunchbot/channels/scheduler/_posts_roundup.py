"""Channel roundup — periodic SUMMARY post linking recent individual posts.

Owner content doctrine (2026-08-31): the main channel mixes standalone
one-off posts for breaking items with periodic SUMMARY posts that link
several recent individual posts. This module is the SUMMARY poster:

* Reads the recent mirrored posts from the feed.rugmunch.io static store
  (``scheduler._posts_rag.FEED_DATA/posts.json`` — the same store every
  main-channel post mirrors into).
* Composes a deterministic roundup: title + 3-6 bullet deep links
  (``feed.rugmunch.io/p/{slug}``) in the style-guide voice.
* ONE LINK PER SOURCE: each bullet links its own feed deep link; a source
  that already appeared is skipped, so no URL ever appears twice in the
  post. Bullets whose text trips a style-guide banned phrase are dropped
  (silence beats slop).

Fail-open everywhere: unreadable/empty store → no post this tick. Dedupe
per roundup window so an accidental double-fire cannot spam the channel.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.formatters._primitives import SITE_FEED_POST_URL
from rugmunchbot.channels.scheduler._core import (
    _chat_id,
    _post_photo_aware,
    _release,
    _remember,
    _safe_send,
    logger,
)
from rugmunchbot.support.content_lint import BANNED_PHRASES
from rugmunchbot.support.image_policy import ensure_post_image

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

#: Bullets per roundup (owner spec: 3-6).
ROUNDUP_MIN_ITEMS = 3
ROUNDUP_MAX_ITEMS = 6

#: Kinds that are themselves summaries — a roundup never links a roundup
#: (or a market brief, which carries its own edition links).
_SUMMARY_KINDS = frozenset({"roundup", "market-summary"})

#: Banned phrases — canonical list lives in ``support.content_lint`` (the
#: CI/audit lint enforces the same list; a single source of truth so the
#: roundup and the linter can never drift apart).
_BANNED_PHRASES: tuple[str, ...] = BANNED_PHRASES

#: Max characters of post text surfaced per bullet (title-style lead line).
_BULLET_MAX_CHARS = 110


def _clean_bullet_text(text: str) -> str:
    """First line of a mirrored post, HTML-stripped, bounded, ban-filtered.

    Returns "" when nothing usable survives (empty, or a banned phrase).
    Pure — never raises.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    # First non-empty line, tags stripped, links dropped (the bullet's own
    # deep link is the only link — one link per source).
    line = raw.splitlines()[0].strip()
    line = re.sub(r"<a\s+href=\"[^\"]*\">(.*?)</a>", r"\1", line)
    line = re.sub(r"<[^>]+>", "", line).strip()
    if len(line) > _BULLET_MAX_CHARS:
        cut = line[:_BULLET_MAX_CHARS]
        space = cut.rfind(" ")
        line = (cut[:space] if space > _BULLET_MAX_CHARS * 0.5 else cut).rstrip() + "…"
    lowered = line.lower()
    if any(banned in lowered for banned in _BANNED_PHRASES):
        return ""
    return line


def collect_roundup_items(
    posts: list[dict[str, Any]], *, max_items: int = ROUNDUP_MAX_ITEMS
) -> list[dict[str, Any]]:
    """Pick 3-6 recent, distinct, ban-clean posts for the roundup.

    ``posts`` is the feed store (newest first, as ``mirror_post_to_feed``
    writes it). Selection: summary kinds are skipped, a source URL may
    appear at most once (one-link-per-source), and bullets without usable
    text are dropped. Pure — deterministic from the input list.
    """
    items: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for post in posts:
        if len(items) >= max_items:
            break
        if str(post.get("kind") or "") in _SUMMARY_KINDS:
            continue
        slug = str(post.get("slug") or "").strip()
        label = _clean_bullet_text(str(post.get("text") or ""))
        if not slug or not label:
            continue
        source = str(post.get("source") or "").strip()
        if source and source in seen_sources:
            continue
        if source:
            seen_sources.add(source)
        items.append({"slug": slug, "label": label, "source": source})
    return items


def compose_roundup(items: list[dict[str, Any]], *, now: datetime | None = None) -> str:
    """Render the roundup post text (Telegram HTML).

    Shape: ruled title banner + one bullet per recent post (deep link) +
    a single closing feed link. Returns "" when fewer than
    ``ROUNDUP_MIN_ITEMS`` items survive — a thin roundup is silence, not
    filler. Pure + deterministic.
    """
    if len(items) < ROUNDUP_MIN_ITEMS:
        return ""
    lines = ["<b>━━━ THE RUG MUNCH ROUNDUP ━━━</b>", ""]
    for item in items:
        url = SITE_FEED_POST_URL.format(slug=item["slug"])
        lines.append(f'• <a href="{url}">{item["label"]}</a>')
    lines += [
        "",
        "Everything above is live on the feed — one link per story, no repeats.",
        '📖 <a href="https://feed.rugmunch.io">feed.rugmunch.io</a> — full archive',
        "🤖 Live token intel, scans &amp; alerts: @rugmunchbot",
    ]
    return "\n".join(lines)


def _load_feed_posts() -> list[dict[str, Any]]:
    """Recent posts from the feed store (fail-open → [])."""
    from rugmunchbot.channels.scheduler._posts_rag import FEED_DATA

    path = os.path.join(FEED_DATA, "posts.json")
    try:
        with open(path) as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(loaded, dict):
        loaded = loaded.get("posts") or []
    if not isinstance(loaded, list):
        return []
    return [p for p in loaded if isinstance(p, dict)]


async def post_roundup(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the periodic roundup to the main channel. Returns 1 when posted.

    Dedupe key is the UTC 8-hour window so the 3x-daily schedule cannot
    double-fire within a window; a failed send releases the mark.
    """
    chat_id = _chat_id("main")
    if not chat_id:
        logger.info("roundup:no_main_chat_id_skipped")
        return 0
    window = int(datetime.now(UTC).timestamp() // (8 * 3600))
    dedupe_key = f"roundup:{window}"
    if not _remember("roundup", dedupe_key):
        return 0
    items = collect_roundup_items(_load_feed_posts())
    text = compose_roundup(items)
    if not text:
        logger.info("roundup:thin_store_skipped items=%d", len(items))
        return 0
    # HARD quality gate (2026-08-31): lint → autofix → skip.
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    gate = await pre_post_quality_gate(text, source="roundup", kind="roundup", logger=logger)
    if not gate.ok:
        return 0
    text = gate.text
    # Image doctrine (2026-08-31): the roundup ships photo + caption like
    # every other main-channel post — never a naked text message. The gate
    # resolved the placeholder; _post_photo_aware attaches it.
    post = ensure_post_image(text)
    post.image_url = gate.image_url or post.image_url
    if not await _post_photo_aware(ctx.bot, chat_id, post, _safe_send):
        _release("roundup", dedupe_key)
        return 0
    logger.info("roundup:posted items=%d", len(items))
    return 1


__all__ = [
    "collect_roundup_items",
    "compose_roundup",
    "post_roundup",
]

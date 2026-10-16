"""Channel roundup — the WEEKLY recap (folded into the Daily Rug).

DECISION (2026-10-17, operator doctrine): the standalone 3x-daily roundup
is RETIRED. It duplicated the flagship "The Daily Rug — Morning Market
Brief" (``channels/daily_rug``), which already carries "the day's best
stories, ranked" every morning. Two link-list posts a day read as filler.

What survives: this module is kept as the WEEKLY recap generator — a
distinct, once-a-week "what you missed" post that the Daily Rug does NOT
cover (a 7-day arc, not a 24-hour one). It is NOT registered on the
scheduler by default (see ``_register.py``); re-enable with
``RMI_ROUNDUP_ENABLED=true`` if the operator wants the weekly recap back.

Copy doctrine (re-authored 2026-10-17): the old "━━━ THE RUG MUNCH
ROUNDUP ━━━" banner + "Everything above is live on the feed" closer was
generic template filler. The new copy leads with the week's through-line
and closes with a specific, on-brand call — no "here is a token", no
"stay tuned", no banner-for-banner's-sake.

* Reads the recent mirrored posts from the feed.rugmunch.io static store
  (``scheduler._posts_rag.FEED_DATA/posts.json``).
* Composes a deterministic recap: a dated masthead + 3-6 bullet deep links
  (``rugmunch.io/blog/{slug}``) in the style-guide voice.
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
from rugmunchbot.support.post_graphics import graphic_for

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
        # 2026-09-01 cross-window dedupe: a story shown in a previous
        # roundup must not reappear in the next window — the user saw the
        # "same links multiple times today". PEEK only (read-only): stories
        # are marked as shown AFTER a successful post (see post_roundup),
        # so a failed/thin roundup never burns the candidates.
        if _roundup_story_seen(slug):
            continue
        items.append({"slug": slug, "label": label, "source": source})
    return items


def _roundup_story_seen(slug: str) -> bool:
    """True when this roundup slug was already shown in a previous window."""
    from rugmunchbot.channels.scheduler._core import _posted_keys

    return f"roundup-story:{slug}" in _posted_keys.get("__global__", set())


def mark_roundup_stories_shown(items: list[dict[str, Any]]) -> None:
    """Mark roundup slugs as shown (durable via the tick state save)."""
    for item in items:
        _remember("roundup", f"roundup-story:{item['slug']}")


def compose_roundup(items: list[dict[str, Any]], *, now: datetime | None = None) -> str:
    """Render the weekly recap post text (Telegram HTML).

    Shape: a dated masthead (the week's through-line, not a generic banner)
    + one bullet per recent post (deep link) + a specific closing call.
    Returns "" when fewer than ``ROUNDUP_MIN_ITEMS`` items survive — a thin
    recap is silence, not filler. Pure + deterministic.

    Copy doctrine (2026-10-17): the masthead names the window and the desk;
    the closer points at the live feed as the archive, not as a vague
    "everything above". No "here is a token", no "stay tuned".
    """
    if len(items) < ROUNDUP_MIN_ITEMS:
        return ""
    stamp = (now or datetime.now(UTC)).strftime("%b %d")
    lines = [
        "<b>━━━ THE WEEK IN RUGS ━━━</b>",
        f"<i>Rug Munch Intelligence — what moved, what rugged, {stamp}</i>",
        "",
    ]
    for item in items:
        url = SITE_FEED_POST_URL.format(slug=item["slug"])
        lines.append(f'• <a href="{url}">{item["label"]}</a>')
    lines += [
        "",
        "Each link is the full story — one source apiece, no repeats.",
        '📖 <a href="https://feed.rugmunch.io">feed.rugmunch.io</a> — the running archive',
        "🤖 Scan any token before you ape: @rugmunchbot",
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
    """Post the weekly recap to the main channel. Returns 1 when posted.

    RETIRED by default (2026-10-17): the standalone roundup duplicated the
    Daily Rug. Re-enable with ``RMI_ROUNDUP_ENABLED=true``. Dedupe key is
    the UTC 8-hour window so the schedule cannot double-fire within a
    window; a failed send releases the mark.
    """
    if os.getenv("RMI_ROUNDUP_ENABLED", "false").strip().lower() not in ("1", "true", "yes"):
        logger.info("roundup:disabled_skipped")
        return 0
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
    # Image doctrine (2026-08-31): the recap ships photo + caption like every
    # other main-channel post. The graphic comes from POST_TYPE_GRAPHICS
    # (operator's originals) — the gate's resolved image wins when it has one.
    post = ensure_post_image(text)
    post.image_url = gate.image_url or graphic_for("roundup")
    if not await _post_photo_aware(ctx.bot, chat_id, post, _safe_send, content_type="roundup"):
        _release("roundup", dedupe_key)
        return 0
    # Cross-window dedupe: only NOW are the stories marked as shown —
    # a failed send never burns the candidates for the next window.
    mark_roundup_stories_shown(items)
    logger.info("roundup:posted items=%d", len(items))
    return 1


__all__ = [
    "collect_roundup_items",
    "compose_roundup",
    "post_roundup",
]

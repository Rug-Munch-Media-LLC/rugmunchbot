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
import re
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

#: WO-3 quality gate (CONTENT-POSTING-SPEC.md): a draft whose rerank score
#: sits below this floor is DISCARDED — never posted anyway. Only drafts that
#: actually carry a positive score are gated; a missing/zero score means "no
#: signal" and passes (the rugbrain importance metadata is sparse today).
RMI_RAG_POST_MIN_SCORE = float(os.getenv("RMI_RAG_POST_MIN_SCORE", "0.30"))

#: Signature of the rugbrain ``_template_post`` fallback writer: "[SEV] Title"
#: first line + "Source: …" footer. Template output = DO NOT POST (spec law):
#: the LLM writer being down must degrade to silence, not to slop.
_TEMPLATE_SIGNATURE_RE = re.compile(
    r"^\[(?:CRITICAL|HIGH|MEDIUM|LOW)\]\s+.+\n\nSource:\s+", re.DOTALL
)


def _quality_gate(item: dict[str, Any]) -> str:
    """WO-3 draft gate. Returns "" when the draft may post, else the reason.

    Checks (in order): explicit ``writer == "template"`` marker (rugbrain
    2026-08-25), the template signature heuristic (covers containers built
    before the marker shipped), and the rerank-score floor. Pure — never
    raises; unparsable scores pass.
    """
    writer = str(item.get("writer") or "").strip().lower()
    if writer == "template":
        return "template_writer"
    text = str(item.get("text") or "")
    if not writer and _TEMPLATE_SIGNATURE_RE.match(text.strip()):
        return "template_signature"
    raw_score = item.get("score", item.get("rerank_score"))
    if raw_score is not None:
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return ""
        if score > 0 and score < RMI_RAG_POST_MIN_SCORE:
            return f"below_threshold({score:.2f})"
    return ""


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
        # W30: mirror is MANDATORY (CONTENT-STRATEGY-CANONICAL v2) — a
        # failure must be visible, not a debug whisper. The operator alert
        # itself fires from post_rag_drafts (needs the bot handle); here we
        # only escalate the log level.
        logger.warning("feed_mirror_failed err=%s", str(e)[:200])
        return None


#: Consecutive main→feed mirror failures (W30 alerting state). Alert fires
#: once per failure streak; a success resets it (recovery logged once).
_mirror_fail_streak = 0


async def _alert_mirror_failure(bot: Any, err_hint: str) -> None:
    """Notify the operator channel that the mandatory feed mirror broke.

    CONTENT-STRATEGY-CANONICAL v2 / W30: every main post MUST land on
    feed.rugmunch.io; a silent mirror gap breaks the operator-social
    contract. Alerts fire to the ``updates`` role (@rmiupdates) once per
    failure streak — never per tick. Fail-open: any error here is logged
    and swallowed; alerting must never break posting.
    """
    global _mirror_fail_streak
    _mirror_fail_streak += 1
    if _mirror_fail_streak > 1:
        return  # already alerted for this streak
    logger.warning("feed_mirror_down streak_started=%d hint=%s", _mirror_fail_streak, err_hint[:80])
    try:
        from rugmunchbot.config import _resolve_channel_id

        ops_chat = _resolve_channel_id("updates")
        if not ops_chat:
            return
        await bot.send_message(
            ops_chat,
            "\U0001f6a8 <b>Feed mirror DOWN</b>\n"
            "Main-channel posts are NOT reaching feed.rugmunch.io "
            "(mandatory mirror, strategy v2/W30).\n"
            f"hint: <code>{err_hint[:120]}</code>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("feed_mirror_alert_failed err=%s", str(e)[:120])


def _note_mirror_success() -> None:
    """Reset the W30 failure streak; log recovery exactly once."""
    global _mirror_fail_streak
    if _mirror_fail_streak:
        logger.info("feed_mirror_recovered after=%d failures", _mirror_fail_streak)
    _mirror_fail_streak = 0


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
            # WO-3 quality gate BEFORE the dedupe mark: a gated draft must
            # stay retryable (the writer may be healthy next cycle), so it
            # is skipped without burning its story_ref.
            gate_reason = _quality_gate(item)
            if gate_reason:
                logger.warning(
                    "rag_draft_gated channel=%s reason=%s ref=%s",
                    channel,
                    gate_reason,
                    str(item.get("story_ref") or "")[:60],
                )
                continue
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
                    _note_mirror_success()
                    logger.info("rag_post_mirrored_to_feed slug=%s", slug)
                else:
                    # W30: mandatory mirror failed — operator must know.
                    await _alert_mirror_failure(ctx.bot, str(item.get("story_ref") or "")[:120])

    if posted_total:
        logger.info("rag_drafts_posted count=%d", posted_total)
    return posted_total


__all__ = [
    "_alert_mirror_failure",
    "_note_mirror_success",
    "_quality_gate",
    "post_rag_drafts",
]

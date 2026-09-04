"""RAG-drafts — main gather → score → gate → post path.

References the package namespace (``_pkg``) for shared config, the
patchable imports (``fetch_rag_drafts``, ``rerank_quality``, the ``_core``
helpers) and the gate/mirror helpers so monkeypatching the package
propagates.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import rugmunchbot.channels.scheduler._posts_rag as _pkg

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def _fallback_main_drafts(limit: int = 6) -> list[dict[str, Any]]:
    """Bot-owned content candidates for the main lane (fail-open → []).

    Uses our own ``fetch_trending_news`` (real backend headlines with writer
    + source URL) so the main lane posts even when rugbrain's
    content-distribution lane is down, disabled, or silent. Each item is shaped
    into a draft the lane gates accept (a ``ts`` so the recency gate passes,
    an http(s) link in the text so the source gate passes), and carries a
    stable ``story_ref`` so the global dedupe can never double-post it.

    2026-08-30 unify: moved here from ``_posts_daily`` so it is a SINGLE
    source of truth for the folded-in daily-quality fallback shared by BOTH
    the channel_rag lane and the daily_quality job.
    """
    try:
        from rugmunchbot.channels.content import fetch_trending_news

        items = await fetch_trending_news(limit=limit)
    except Exception as exc:  # fail-open by contract
        _pkg.logger.debug("main_gather:fallback_fetch_failed err=%s", str(exc)[:120])
        return []
    now = datetime.now(UTC).isoformat()
    drafts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        url = str(it.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        text = f"{it.get('title') or it.get('summary') or ''} {url}".strip()
        if not text:
            continue
        drafts.append(
            {
                "story_ref": url,
                "title": str(it.get("title") or url)[:120],
                "text": text,
                "source": str(it.get("source") or "rugmunch")[:80],
                "ts": now,
                "kind": "quality",
                "url": url,
                # 2026-09-02 feed-mirror gap fix: fallback main posts must
                # mirror to feed.rugmunch.io exactly like primary RAG drafts.
                # Without this flag the poster's ``item.get("mirror")`` check
                # skipped the mandatory W30 feed mirror whenever the RAG lane
                # was down and the bot fell back to its own curated headlines.
                "mirror": True,
            }
        )
    return drafts


async def _regenerate_draft_text(text: str, violations: list[Any]) -> str:
    """Stricter-prompt LLM rewrite for a draft that failed the quality gate.

    Used as the gate's ONE regeneration retry on the prose lanes (main/news):
    a headline+link stub is rewritten into a real ≥20-word commentary block.
    Fail-open: any error returns "" (the gate then blocks the post).
    """
    from rugmunchbot.llm_config import chat_with_fallback

    detail = "; ".join(f"{v.rule}: {v.phrase}" for v in violations[:5])
    url_match = re.search(r"https?://\S+", text or "")
    url = url_match.group(0) if url_match else ""
    title = re.sub(r"https?://\S+", "", text or "").strip()[:200]
    messages = [
        {
            "role": "system",
            "content": (
                "You write Telegram channel posts for a crypto intelligence "
                "desk. Rules: at least 20 words of original commentary, no "
                "banned slop phrases (NFA, DYOR, HODL, 'stay safe', 'delve "
                "into', 'ever-evolving'), the source URL appears EXACTLY "
                "once, no '[SEV] Title / Source:' template shape. Output "
                "ONLY the post text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Headline: {title}\nSource URL: {url}\n"
                f"Lint failures to fix: {detail[:300]}\n\n"
                "Write the full post now (commentary + the source link once)."
            ),
        },
    ]
    return (await chat_with_fallback(messages, "snappy")).strip()


async def gather_main_drafts(limit: int = 8) -> list[dict[str, Any]]:
    """Single unified main-content gather: RAG primary → scored fallback → gate.

    2026-08-30 unify (task 1): the ``channel_rag`` lane and the ``daily_quality``
    job now share ONE gather→generate→score→gate→post path instead of two
    divergent lanes. This is that shared gather:

    * **Primary** — channel_rag's drafts (rugbrain ``/content/drafts`` on the
      ``main`` channel) via ``fetch_rag_drafts``.
    * **Fallback** — when the RAG lane returns nothing (writer down / lagging /
      disabled), we fold in the bot's own curated headlines.
    * **Score** — the fallback is routed through ``rerank_quality`` so every
      fallback draft *carries* a real ``importance`` (task 2: no unscored
      bypass).
    * **Gate** — each surviving candidate is checked by the unified gates
      (quality / recency / source), which now apply the 0.30 importance floor
      uniformly to scored drafts.

    Returns drafts already scored + past every main gate. Fail-open → [] on
    any error (silence beats slop, never a crash).
    """
    try:
        primary = await _pkg.fetch_rag_drafts("main", limit=max(1, limit))
    except Exception as exc:  # fail-open by contract
        _pkg.logger.warning("main_gather:rag_fetch_failed channel=main err=%s", str(exc)[:120])
        primary = []
    if primary:
        drafts = [dict(d) for d in primary]
    else:
        # 2026-08-30 unify (task 3): a silent RAG lane must be VISIBLE — WARN
        # and fall back into the scored bot-sourced candidates, never no-op.
        _pkg.logger.warning("main_gather:rag_lane_empty channel=main falling_back_to_bot_sources")
        drafts = await _fallback_main_drafts(limit)
        if drafts:
            drafts = await _pkg.rerank_quality(drafts)
    kept: list[dict[str, Any]] = []
    for item in drafts:
        gate = _pkg._quality_gate(item) or _pkg._recency_gate(item) or _pkg._source_gate(item)
        if gate:
            _pkg.logger.info(
                "main_gather:gated channel=main reason=%s ref=%s",
                gate,
                str(item.get("story_ref") or "")[:60],
            )
            continue
        kept.append(item)
    return kept


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
    # 2026-08-30 unify: the main channel sources through the SINGLE
    # gather→score→gate path (``gather_main_drafts``) shared with
    # channel_daily_quality — so RAG-empty WARNs and folds in the scored
    # fallback instead of silently posting zero.
    for channel in ("main", "news", "alerts", "scans"):
        if channel == "main":
            drafts = await _pkg.gather_main_drafts(limit=_pkg.MAX_PER_CHANNEL_PER_TICK + 2)
        else:
            drafts = await _pkg.fetch_rag_drafts(channel, limit=_pkg.MAX_PER_CHANNEL_PER_TICK + 2)
        chat_id = _pkg._chat_id(channel)
        if not chat_id:
            _pkg.logger.debug("rag_drafts_skipped channel=%s reason=no_chat_id", channel)
            continue
        sent_this_channel = 0
        for item in drafts:
            if sent_this_channel >= _pkg.MAX_PER_CHANNEL_PER_TICK:
                break
            # WO-3 quality gate BEFORE the dedupe mark: a gated draft must
            # stay retryable (the writer may be healthy next cycle), so it
            # is skipped without burning its story_ref.
            gate_reason = _pkg._quality_gate(item)
            if not gate_reason:
                # 2026-08-26 stale-news incident: quality alone is not
                # freshness — a well-written 2022 exploit still must not
                # post. Undated drafts are rejected too (fail-closed).
                gate_reason = _pkg._recency_gate(item)
            if not gate_reason:
                # 2026-08-26 no-source incident: an unsourced post is
                # unverifiable — rejected regardless of how well written.
                gate_reason = _pkg._source_gate(item)
            if not gate_reason and channel == "news":
                # 2026-09-03 channel doctrine: @rmicryptonews is breaking
                # crypto news ONLY. Whale alerts, scan results, system
                # notifications, product updates, shill/alpha intel and
                # roundup/digest summaries are blocked from the news channel
                # no matter how well written or fresh they are.
                gate_reason = _pkg._news_content_type_gate(item)
            if gate_reason:
                _pkg.logger.warning(
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
            if not _pkg._remember(f"rag_{channel}", dedupe_key, title=item.get("title")):
                continue

            # Site-link contract (2026-08-29): main posts carry the exact
            # feed.rugmunch.io link (the mirror they land on), news posts the
            # rugmunch.io/news link — never a linkless flagship post.
            text = str(item.get("text", ""))
            if channel in ("main", "news"):
                text = _pkg.enforce_site_link(text, site="feed" if channel == "main" else "news")

            # HARD quality gate (2026-08-31): lint → autofix → skip. A draft
            # that still fails after autofix does not ship and stays
            # retryable (dedupe mark not yet burned — the mark below is
            # taken only after a successful send).
            # Image doctrine (2026-08-31): EVERY channel post carries an
            # image. Resolve before the gate — carried image → og:image from
            # the source URL (news lanes) → branded placeholder — so the
            # gate's require_image check can never block on a missing image
            # and _post_photo_aware always attaches a photo.
            from rugmunchbot.support.image_policy import (
                ensure_post_image,
                resolve_post_image,
            )
            from rugmunchbot.support.quality_gate import pre_post_quality_gate

            # 2026-09-01 FINAL: image_url from the story doc is IGNORED —
            # only the article's own og:image (via source_url) is legitimate.
            resolved_image = await resolve_post_image(
                channel,
                image_url="",
                source_url=item.get("url") or item.get("source_url") or "",
                logger=_pkg.logger,
            )
            gate = await pre_post_quality_gate(
                text,
                image_url=resolved_image,
                source=str(item.get("story_ref") or "")[:80],
                kind=f"rag:{channel}",
                # Structured scan/alert formats are terse by design; only
                # prose lanes (main/news) enforce the full 20-word floor.
                # Image doctrine is ALL channels: every post ships a photo
                # (carried, og:image, or branded placeholder).
                min_words=_pkg.BLAND_MIN_WORDS if channel in ("main", "news") else 8,
                require_image=True,
                allow_placeholder_image=True,
                regenerate=_regenerate_draft_text if channel in ("main", "news") else None,
                logger=_pkg.logger,
            )
            if not gate.ok:
                continue
            text = gate.text
            item = {**item, "image_url": gate.image_url}

            # Content-type contract: a RAG draft is chartered for its native
            # channel's content type (news→news, alerts→alert, scans→scan;
            # main accepts everything). Enforced at the send chokepoint.
            rag_content_type = {
                "news": "news",
                "alerts": "alert",
                "scans": "scan",
                "main": "news",
            }.get(channel)

            ok = await _pkg._post_photo_aware(
                ctx.bot,
                chat_id,
                ensure_post_image(_pkg._rag_post_with_image(text, item)),
                _pkg._safe_send,
                content_type=rag_content_type,
            )
            if not ok:
                # Release the dedupe mark so the next tick retries this draft
                # instead of black-holing it (see docstring).
                _pkg._release(f"rag_{channel}", dedupe_key)
                continue

            sent_this_channel += 1
            posted_total += 1

            if channel == "main" and item.get("mirror"):
                slug = _pkg._mirror_to_feed(item)
                if slug:
                    _pkg._note_mirror_success()
                    _pkg.logger.info("rag_post_mirrored_to_feed slug=%s", slug)
                else:
                    # W30: mandatory mirror failed — operator must know.
                    await _pkg._alert_mirror_failure(
                        ctx.bot, str(item.get("story_ref") or "")[:120]
                    )

    if posted_total:
        _pkg.logger.info("rag_drafts_posted count=%d", posted_total)
    return posted_total

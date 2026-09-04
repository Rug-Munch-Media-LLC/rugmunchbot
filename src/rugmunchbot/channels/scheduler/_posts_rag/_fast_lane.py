"""RAG-drafts — breaking-news fast lane + security-tweet escalation.

References the package namespace (``_pkg``) for shared config, the
patchable imports (``fetch_rag_drafts``, the ``_core`` helpers) and the
gate helpers so monkeypatching the package propagates.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING, Any

import rugmunchbot.channels.scheduler._posts_rag as _pkg

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def post_rag_fast_lane(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Breaking-news fast lane → @rmialerts, critical+fresh only.

    Returns posted count (0 or 1). All slow-lane gates apply (quality,
    recency, source) PLUS severity=="critical" and a tighter age cap. The
    shared dedupe key means the slow tick skips anything this already
    delivered; a failed send releases the mark for either lane to retry.
    """
    chat_id = _pkg._chat_id("alerts")
    if not chat_id:
        return 0
    drafts = await _pkg.fetch_rag_drafts("alerts", limit=3)
    for item in drafts:
        if str(item.get("severity") or "").lower() != "critical":
            continue
        gate_reason = _pkg._quality_gate(item)
        if not gate_reason:
            gate_reason = _pkg._recency_gate(item)
        if not gate_reason:
            gate_reason = _pkg._source_gate(item)
        age_h, probe = _pkg._draft_age_hours(item)
        if not gate_reason and age_h is not None and age_h > _pkg.RAG_FAST_LANE_MAX_AGE_H:
            gate_reason = f"fast_lane_stale({age_h:.0f}h via {probe})"
        if gate_reason:
            _pkg.logger.debug(
                "rag_fast_lane_skipped reason=%s ref=%s",
                gate_reason,
                str(item.get("story_ref") or "")[:60],
            )
            continue
        dedupe_key = f"rag:{item.get('story_ref') or item.get('text', '')[:60]}"
        if not _pkg._remember(
            "rag_alerts",
            dedupe_key,
            title=item.get("title"),
        ):
            return 0  # slow lane already claimed it
        # HARD quality gate (2026-08-31) — same contract as the slow lane,
        # including the all-channel image doctrine (resolve → gate → photo).
        from rugmunchbot.support.image_policy import ensure_post_image, resolve_post_image
        from rugmunchbot.support.quality_gate import pre_post_quality_gate

        resolved_image = await resolve_post_image(
            "alerts",
            image_url=item.get("image_url") or item.get("image") or "",
            source_url=item.get("url") or item.get("source_url") or "",
            logger=_pkg.logger,
        )
        gate = await pre_post_quality_gate(
            str(item.get("text", "")),
            image_url=resolved_image,
            source=str(item.get("story_ref") or "")[:80],
            kind="rag:fast_lane",
            logger=_pkg.logger,
        )
        if not gate.ok:
            _pkg._release("rag_alerts", dedupe_key)
            continue
        # Photo-aware send (2026-08-31): the fast lane is a channel post too —
        # it must never ship as a naked text message.
        if await _pkg._post_photo_aware(
            ctx.bot,
            chat_id,
            ensure_post_image(
                _pkg._rag_post_with_image(gate.text, {**item, "image_url": gate.image_url})
            ),
            _pkg._safe_send,
            content_type="alert",
        ):
            _pkg.logger.info(
                "rag_fast_lane_posted ref=%s age_h=%.1f",
                str(item.get("story_ref") or "")[:60],
                age_h if age_h is not None else -1.0,
            )
            return 1
        _pkg._release("rag_alerts", dedupe_key)
        return 0
    return 0


def _esc(value: object) -> str:
    """HTML-escape for Telegram parse_mode=html."""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tweet_int(tweet: dict[str, Any], key: str) -> int:
    """Best-effort int of a tweet metric (like/retweet count). 0 on any failure."""
    try:
        return int(tweet.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _security_tweet_matches(tweet: dict[str, Any]) -> bool:
    """True when a tweet qualifies for breaking-news escalation. Pure.

    A tweet escalates when ALL of:
      * it comes from a monitored security account (SECURITY_TWEET_ACCOUNTS),
      * its text mentions a security keyword (SECURITY_TWEET_KEYWORDS),
      * it has high engagement — >50 likes OR >20 retweets.

    Never raises; malformed/missing fields simply fail the gate.
    """
    handle = str(tweet.get("handle") or "").strip().lstrip("@").lower()
    if handle not in _pkg.SECURITY_TWEET_ACCOUNTS:
        return False
    text = str(tweet.get("text") or "").lower()
    if not any(kw in text for kw in _pkg.SECURITY_TWEET_KEYWORDS):
        return False
    likes = _tweet_int(tweet, "like_count")
    retweets = _tweet_int(tweet, "retweet_count")
    return likes > _pkg.SECURITY_TWEET_MIN_LIKES or retweets > _pkg.SECURITY_TWEET_MIN_RETWEETS


def format_security_tweet_escalation(tweet: dict[str, Any]) -> str:
    """Build the breaking-news HTML post for a qualifying security tweet.

    Names the researcher ("🚨 ZachXBT flagged a potential exploit…"), links
    the original tweet, includes the key details from the tweet, and tags it
    as breaking security news. Pure — never raises.
    """
    handle = str(tweet.get("handle") or "").strip().lstrip("@")
    author_name = str(tweet.get("author_name") or "").strip() or f"@{handle}"
    text = str(tweet.get("text") or "").strip()
    url = str(tweet.get("url") or "").strip()
    likes = _tweet_int(tweet, "like_count")
    retweets = _tweet_int(tweet, "retweet_count")

    details = _esc(text)[:200]
    if len(text) > 200:
        details += "…"

    parts = [
        f"🚨 <b>{_esc(author_name)}</b> flagged a potential exploit",
        "",
        details,
        "",
        f"👍 {likes} likes • 🔁 {retweets} retweets",
    ]
    if url:
        parts.append(f'🔗 <a href="{html.escape(url, quote=True)}">View original tweet</a>')
    return "\n".join(parts)


async def post_security_tweet_escalation(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Escalate high-engagement security tweets to main as breaking news.

    Runs on a short tick (independent of the daily roundup): a tweet from a
    monitored security account that mentions a security keyword AND has high
    engagement (>50 likes or >20 retweets) posts to @cryptorugmuncher
    immediately — it does not wait for the daily roundup. Dedupes via the
    shared global namespace so a tweet never posts twice. Fail-open: no main
    chat id, empty sweep, or a failed send logs and returns 0 — never crashes
    the scheduler.
    """
    chat_id = _pkg._chat_id("main")
    if not chat_id:
        return 0
    try:
        from rugmunchbot.tools.x_ingest import fetch_security_tweets

        tweets = await fetch_security_tweets()
    except Exception as exc:  # fail-open by contract
        _pkg.logger.warning("security_tweet_escalation:fetch_failed err=%s", str(exc)[:120])
        return 0
    posted = 0
    for tweet in tweets:
        if not _security_tweet_matches(tweet):
            continue
        url = str(tweet.get("url") or "").strip()
        tweet_id = str(tweet.get("tweet_id") or "").strip()
        ref = url or tweet_id or str(tweet.get("text") or "")[:60]
        dedupe_key = f"rag:sec:{ref}"
        if not _pkg._remember("rag_security_tweet", dedupe_key, title=tweet.get("text")):
            continue
        text = format_security_tweet_escalation(tweet)
        if await _pkg._safe_send(ctx.bot, chat_id, text, content_type="alert"):
            posted += 1
            _pkg.logger.info(
                "security_tweet_escalated handle=%s ref=%s",
                str(tweet.get("handle") or "")[:40],
                ref[:60],
            )
        else:
            # Release the dedupe mark (bare key + title hash) so the next
            # tick retries this tweet instead of black-holing it (same
            # contract as the RAG lanes).
            _pkg._release("rag_security_tweet", dedupe_key, title=tweet.get("text"))
    return posted

"""The Daily Rug — pipeline: gather → write → gate → feed → post.

``post_daily_rug`` is the once-daily entry point. Order of operations:

1. Date guard — claim today's slot (idempotent; a re-run no-ops).
2. Gather the previous day's best stories (ranked reranker + RAG + alerts).
3. LLM-write the longform brief (cryptorugmuncher voice).
4. Deterministic quality gate (``support.quality_gate``) + content-hash
   ledger (near-duplicate guard).
5. Mirror to the feed site (``feed.rugmunch.io``) + Ghost, deep-linking
   the exact post.
6. Post to the main channel with the branded Daily Rug logo.

Fails open at every step — a broken lane logs and the run no-ops; the
Telegram post is never blocked by the feed. The date guard is released
when the run aborts BEFORE a successful send, so a retry can still post.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.channels._story_cta import attach_story_cta, slug_from_url
from rugmunchbot.channels.daily_rug._config import (
    DAILY_RUG_FEED_ENABLED,
    DAILY_RUG_MAX_CHARS,
)
from rugmunchbot.channels.daily_rug._fetch import (
    gather_daily_rug_data,
    pick_daily_rug_image,
)
from rugmunchbot.channels.daily_rug._guard import (
    already_posted_today,
    claim_today,
    release_today,
)
from rugmunchbot.channels.daily_rug._prompt import EDITION, build_messages
from rugmunchbot.support.post_graphics import graphic_for_type

logger = logging.getLogger("rmi_bot.channels.daily_rug")

#: feed.rugmunch.io deep-link base for a mirrored post slug.
FEED_POST_URL_FMT = "https://rugmunch.io/blog/{slug}"

#: Prompt-echo markers stripped from LLM output before the gate.
_ECHO_MARKERS = (
    "AI DISCLOSURE:",
    "OUTPUT FORMAT",
    "MASTHEAD (",
    "MASTHEAD DONE",
    "NOW THE BODY",
    "cryptonews_rss",
    "rag_intel",
    "ranked_stories",
    "HARD RULES",
    "SECTION HEADERS",
    "## THE RUG TOOLKIT",
    "El informe",
    "The user just asked",
    "No further action needed",
)


def cleanup_html(text: str, *, max_chars: int = DAILY_RUG_MAX_CHARS) -> str:
    """Normalize LLM output to safe Telegram HTML (shared cleaner)."""
    from rugmunchbot.channels.daily_market_summary._publish import cleanup_html as _clean

    return _clean(text, max_chars=max_chars)


def _strip_echo(raw: str) -> str:
    """Truncate *raw* at the first prompt-echo marker (guarded for ##)."""
    from rugmunchbot.channels.daily_market_summary import _publish as _ms

    for marker in _ECHO_MARKERS:
        raw = _ms._strip_prompt_marker(raw, marker, guarded=marker.startswith("## "))
    return raw


def _has_min_stories(text: str, *, min_links: int = 3) -> bool:
    """True when the brief carries at least ``min_links`` distinct http links."""
    links = set(re.findall(r"https?://[^\s)>\"']+", text))
    return len(links) >= min_links


def _is_language_leak(text: str) -> bool:
    """True when >20% of words are common Spanish/French/German stopwords."""
    from rugmunchbot.channels.daily_rug._prompt import SYSTEM_PROMPT  # noqa: F401

    plain = re.sub(r"<[^>]+>", " ", text)
    words = re.findall(r"[A-Za-zÀ-ÿ']+", plain.lower())
    if not words:
        return False
    from rugmunchbot.channels.daily_rug._lang import NON_ENGLISH_WORDS

    non_english = sum(1 for w in words if w in NON_ENGLISH_WORDS)
    return (non_english / len(words)) > 0.20


class _ImagePost(str):
    """A report part carrying an ``image_url`` — duck-types ``NewsPost``."""

    image_url: str = ""

    def __new__(cls, text: str, image_url: str) -> _ImagePost:
        obj = super().__new__(cls, text)
        obj.image_url = image_url
        return obj


def _split_for_telegram(report: str, max_chars: int = DAILY_RUG_MAX_CHARS) -> list[str]:
    """Split an HTML post into Telegram-sized parts at line boundaries."""
    if len(report) <= max_chars:
        return [report]
    parts: list[str] = []
    remaining = report
    while len(remaining) > max_chars:
        cut = remaining[:max_chars]
        newline = cut.rfind("\n")
        if newline > max_chars * 0.5:
            cut = cut[:newline]
        cut = cut.rstrip()
        if cut.endswith("<"):
            cut = cut[:-1]
        parts.append(cut)
        remaining = remaining[len(cut) :].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


async def _write_brief(context: dict[str, Any]) -> str:
    """LLM-write the brief, with one length-retry. Returns "" on failure."""
    from rugmunchbot.llm_config import chat_with_fallback

    messages = build_messages(context)
    try:
        raw = await chat_with_fallback(messages, "long")
    except Exception as exc:
        logger.warning("daily_rug:llm_failed err=%s", str(exc)[:160])
        return ""
    raw = _strip_echo(raw)
    if len(raw.strip()) < 1200:
        logger.warning("daily_rug:llm_short retrying chars=%s", len(raw.strip()))
        retry = build_messages(context)
        retry[1]["content"] += (
            "\n\nIMPORTANT: your previous attempt was too short. Write the FULL "
            "3000-4000 character brief now — every section, every item, with links."
        )
        try:
            raw2 = await chat_with_fallback(retry, "long")
            if len(raw2.strip()) > len(raw.strip()):
                raw = _strip_echo(raw2)
        except Exception as exc:
            logger.warning("daily_rug:llm_retry_failed err=%s", str(exc)[:120])
    return raw


async def _mirror_to_feed(report: str, image_url: str) -> str:
    """Mirror the brief to the feed site + Ghost. Returns the deep-link URL."""
    feed_url = ""
    slug = ""
    if DAILY_RUG_FEED_ENABLED:
        try:
            from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

            slug = (
                mirror_post_to_feed(
                    {
                        "text": report[:3900],
                        "story_ref": f"daily-rug-{datetime.now(UTC).strftime('%Y%m%d')}",
                        "source": "rugmunch.io",
                        "kind": "briefing",
                        "title": f"{EDITION} — {datetime.now(UTC).strftime('%B %d, %Y')}",
                        "tags": ["briefing", "daily-rug", "market-brief"],
                        "image_url": image_url,
                    }
                )
                or ""
            )
        except Exception as exc:
            logger.warning("daily_rug:feed_mirror_failed err=%s", str(exc)[:120])
        if slug:
            feed_url = FEED_POST_URL_FMT.format(slug=slug)
        try:
            from rugmunchbot._vendor.content_platform import create_post
            from rugmunchbot.channels._longform import to_markdown

            # Ghost must receive longform markdown (H1/H2/H3, real links,
            # no Telegram <b>/<a>/━━━ markup). Passing the raw report shipped
            # Telegram HTML into the lexical converter — the same contract
            # violation ``_report_mirror`` fixes for the Midday/Evening
            # editions (2026-10-17).
            ghost = await create_post(
                content=to_markdown(report),
                post_type="briefing",
                title=f"{EDITION} — {datetime.now(UTC).strftime('%B %d, %Y')}",
                tags=["daily", "daily-rug"],
                feature_image=image_url,
            )
            ghost_url = str(ghost.get("url") or "") if isinstance(ghost, dict) else ""
            if ghost_url and not feed_url:
                feed_url = ghost_url
        except Exception as exc:
            logger.warning("daily_rug:ghost_publish_failed err=%s", str(exc)[:120])
    return feed_url


async def post_daily_rug(ctx: Any) -> int:
    """Once-daily pipeline. Returns 1 when the Telegram post shipped, else 0."""
    from rugmunchbot.channels.scheduler._core import _chat_id, _post_photo_aware, _safe_send

    chat_id = _chat_id("main")  # registry ``main`` → @cryptorugmuncher
    if not chat_id:
        logger.info("daily_rug:no_main_chat_id_skipped")
        return 0

    # 1. Date guard — exactly one post per day.
    if already_posted_today():
        logger.info("daily_rug:already_posted_today_skipped")
        return 0
    if not claim_today():
        logger.info("daily_rug:day_claim_lost_skipped")
        return 0

    # 2. Gather.
    context = await gather_daily_rug_data()
    if not context.get("sources"):
        logger.info("daily_rug:no_data_sources_skipped")
        release_today()
        return 0

    # 3. Write.
    raw = await _write_brief(context)
    if not raw or not raw.strip():
        logger.info("daily_rug:llm_empty_skipped sources=%s", context.get("sources"))
        release_today()
        return 0

    report = cleanup_html(raw)
    if not report:
        logger.info("daily_rug:cleaned_empty_skipped")
        release_today()
        return 0

    # 4. Post-generation guards.
    word_count = len(re.findall(r"\S+", report))
    if word_count < 200:
        logger.warning("daily_rug:too_short_skipped words=%d min=200", word_count)
        release_today()
        return 0
    if not _has_min_stories(report):
        logger.warning(
            "daily_rug:too_few_stories_skipped links=%d min=3",
            len(set(re.findall(r"https?://[^\s)>\"']+", report))),
        )
        release_today()
        return 0
    if _is_language_leak(report):
        logger.warning("daily_rug:language_leak_skipped")
        release_today()
        return 0

    # 5. Hard quality gate (lint → autofix → one regeneration → block).
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    async def _regenerate(bad_text: str, violations: list[Any]) -> str:
        del bad_text
        detail = "; ".join(f"{v.rule}: {v.phrase}" for v in violations[:6])
        retry = build_messages(context)
        retry[1]["content"] += (
            "\n\nSTRICT REWRITE REQUIRED — your previous draft failed the "
            f"content quality lint ({detail[:300]}). Rules: no banned phrases, "
            "each URL appears AT MOST ONCE, at least 20 words of original "
            "commentary. Rewrite the FULL brief now."
        )
        from rugmunchbot.llm_config import chat_with_fallback

        return await chat_with_fallback(retry, "long")

    gate = await pre_post_quality_gate(
        report,
        image_url=pick_daily_rug_image(context),
        source=EDITION,
        kind="daily_rug",
        regenerate=_regenerate,
        logger=logger,
    )
    if not gate.ok:
        logger.warning("daily_rug:quality_gate_blocked violations=%d", len(gate.violations))
        release_today()
        return 0
    report = gate.text
    if gate.regenerated:
        report = cleanup_html(report) or report

    # 6. Content-hash ledger (near-duplicate guard).
    from rugmunchbot.channels.telegram_news._ledger import evaluate_report, record_publication

    ledger_eval = evaluate_report(report, namespace="daily_rug", min_words=200, log=logger)
    if not ledger_eval.allowed:
        logger.warning("daily_rug:publication_skipped reason=%s", ledger_eval.reason)
        release_today()
        return 0

    # 7. Image — story og:image, else the branded Daily Rug logo.
    image_url = pick_daily_rug_image(context) or graphic_for_type("daily_rug")

    # 8. Feed mirror FIRST so the Telegram closer can deep-link the post.
    feed_url = await _mirror_to_feed(report, image_url)

    # 9. Post to the main channel.
    from rugmunchbot.support.content_lint import canonical_url

    already_linked = bool(feed_url) and canonical_url(feed_url) in {
        canonical_url(u) for u in re.findall(r"https?://[^\s)>\"']+", report)
    }
    closer = (
        f"📖 <b>{EDITION}</b> — full brief on rugmunch.io"
        + (f' — <a href="{feed_url}">read it here</a>.' if feed_url and not already_linked else ".")
        + "\n🤖 Live token intel, scans &amp; alerts: @rugmunchbot"
    )
    parts = _split_for_telegram(report)
    parts[-1] = parts[-1].rstrip() + "\n\n" + closer
    # Story CTA: the mirrored Ghost slug when we have one, else /feed.
    parts[-1] = attach_story_cta(parts[-1], {"story_slug": slug_from_url(feed_url)}, "morning")

    sent = 0
    for idx, part in enumerate(parts, 1):
        if idx == 1 and image_url:
            ok = await _post_photo_aware(
                ctx.bot, chat_id, _ImagePost(part, image_url), _safe_send, content_type="market"
            )
        else:
            ok = await _safe_send(ctx.bot, chat_id, part, content_type="market")
        if ok:
            sent += 1
        else:
            break
    if sent == 0:
        release_today()
        return 0

    record_publication("daily_rug", ledger_eval.content_hash, log=logger)
    logger.info("daily_rug:posted parts=%d/%d feed=%s", sent, len(parts), feed_url)
    return 1

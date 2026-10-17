"""Midday Market Report — pipeline: gather → write → gate → feed → post.

``post_midday_report`` is the once-daily entry point. Order of operations:

1. Date guard — claim today's midday slot (idempotent; a re-run no-ops).
2. Gather the macro tape + crypto effect (macro intel + reranker + tape).
3. LLM-write the macro report (Qwythos lane, ``market_brief`` task class).
4. Guardrails — fact-recency gate + link validation.
5. Deterministic quality gate + content-hash ledger.
6. Mirror to the feed site + Ghost longform (H2/H3, proper links).
7. Post to the main channel with the branded Midday graphic.

Fails open at every step. The date guard is released when the run aborts
BEFORE a successful send, so a retry can still post.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.channels.midday_market_report._config import (
    MIDDAY_REPORT_FEED_ENABLED,
    MIDDAY_REPORT_GRAPHIC,
)
from rugmunchbot.channels.midday_market_report._fetch import (
    gather_midday_report_data,
    pick_midday_image,
)
from rugmunchbot.channels.midday_market_report._pipeline import (
    ImagePost,
    cleanup_html,
    has_min_stories,
    is_language_leak,
    split_for_telegram,
    strip_echo,
)
from rugmunchbot.channels.midday_market_report._prompt import EDITION, build_messages

logger = logging.getLogger("rmi_bot.channels.midday_market_report")

#: feed.rugmunch.io deep-link base for a mirrored post slug.
FEED_POST_URL_FMT = "https://rugmunch.io/blog/{slug}"

#: Task class for the Qwythos lane (see llm_config.LOCAL_FIRST_TASKS).
TASK_CLASS = "market_brief"


async def _write_report(context: dict[str, Any]) -> str:
    """LLM-write the report, with one length-retry. Returns "" on failure."""
    from rugmunchbot.llm_config import chat_with_fallback

    messages = build_messages(context)
    try:
        raw = await chat_with_fallback(messages, "long", task_class=TASK_CLASS)
    except Exception as exc:
        logger.warning("midday_report:llm_failed err=%s", str(exc)[:160])
        return ""
    raw = strip_echo(raw)
    if len(raw.strip()) < 1200:
        logger.warning("midday_report:llm_short retrying chars=%s", len(raw.strip()))
        retry = build_messages(context)
        retry[1]["content"] += (
            "\n\nIMPORTANT: your previous attempt was too short. Write the FULL "
            "3000-4000 character report now — every section, every item, with links."
        )
        try:
            raw2 = await chat_with_fallback(retry, "long", task_class=TASK_CLASS)
            if len(raw2.strip()) > len(raw.strip()):
                raw = strip_echo(raw2)
        except Exception as exc:
            logger.warning("midday_report:llm_retry_failed err=%s", str(exc)[:120])
    return raw


async def _mirror_to_feed(report: str, image_url: str) -> str:
    """Mirror the report to the feed site + Ghost longform. Returns deep-link.

    The Ghost step receives the report transformed to longform markdown
    (H1/H2/H3, real links, no Telegram markup) via ``channels._report_mirror``.
    """
    from rugmunchbot.channels._report_mirror import mirror_report

    return await mirror_report(
        report,
        image_url,
        edition=EDITION,
        story_ref=f"midday-report-{datetime.now(UTC).strftime('%Y%m%d')}",
        tags=["briefing", "midday-report", "macro"],
        enabled=MIDDAY_REPORT_FEED_ENABLED,
    )


def _regenerate_hook(context: dict[str, Any]) -> Any:
    """The ONE allowed LLM retry hook for the quality gate."""

    async def _regenerate(bad_text: str, violations: list[Any]) -> str:
        del bad_text
        detail = "; ".join(f"{v.rule}: {v.phrase}" for v in violations[:6])
        retry = build_messages(context)
        retry[1]["content"] += (
            "\n\nSTRICT REWRITE REQUIRED — your previous draft failed the "
            f"content quality lint ({detail[:300]}). Rules: no banned phrases, "
            "each URL appears AT MOST ONCE, at least 20 words of original "
            "commentary. Rewrite the FULL report now."
        )
        from rugmunchbot.llm_config import chat_with_fallback

        return await chat_with_fallback(retry, "long", task_class=TASK_CLASS)

    return _regenerate


async def _send_parts(ctx: Any, chat_id: Any, report: str, image_url: str, feed_url: str) -> int:
    """Post the report to the main channel. Returns the number of parts sent."""
    from rugmunchbot.channels.scheduler._core import _post_photo_aware, _safe_send
    from rugmunchbot.support.content_lint import canonical_url

    already_linked = bool(feed_url) and canonical_url(feed_url) in {
        canonical_url(u) for u in re.findall(r"https?://[^\s)>\"']+", report)
    }
    closer = (
        f"📖 <b>{EDITION}</b> — full report on rugmunch.io"
        + (f' — <a href="{feed_url}">read it here</a>.' if feed_url and not already_linked else ".")
        + "\n🤖 Live token intel, scans &amp; alerts: @rugmunchbot"
    )
    parts = split_for_telegram(report)
    parts[-1] = parts[-1].rstrip() + "\n\n" + closer

    sent = 0
    for idx, part in enumerate(parts, 1):
        if idx == 1 and image_url:
            ok = await _post_photo_aware(
                ctx.bot, chat_id, ImagePost(part, image_url), _safe_send, content_type="market"
            )
        else:
            ok = await _safe_send(ctx.bot, chat_id, part, content_type="market")
        if ok:
            sent += 1
        else:
            break
    return sent


async def post_midday_report(ctx: Any) -> int:
    """Once-daily pipeline. Returns 1 when the Telegram post shipped, else 0."""
    from rugmunchbot.channels.daily_rug._guard import (
        already_posted_today,
        claim_today,
        release_today,
    )
    from rugmunchbot.channels.scheduler._core import _chat_id

    chat_id = _chat_id("main")  # registry ``main`` → @cryptorugmuncher
    if not chat_id:
        logger.info("midday_report:no_main_chat_id_skipped")
        return 0

    # 1. Date guard — exactly one midday post per day.
    if already_posted_today():
        logger.info("midday_report:already_posted_today_skipped")
        return 0
    if not claim_today():
        logger.info("midday_report:day_claim_lost_skipped")
        return 0

    # 2. Gather.
    context = await gather_midday_report_data()
    if not context.get("sources"):
        logger.info("midday_report:no_data_sources_skipped")
        release_today()
        return 0

    # 3. Write.
    raw = await _write_report(context)
    if not raw or not raw.strip():
        logger.info("midday_report:llm_empty_skipped sources=%s", context.get("sources"))
        release_today()
        return 0

    report = cleanup_html(raw)
    if not report:
        logger.info("midday_report:cleaned_empty_skipped")
        release_today()
        return 0

    # 4. Post-generation guards.
    word_count = len(re.findall(r"\S+", report))
    if word_count < 200:
        logger.warning("midday_report:too_short_skipped words=%d min=200", word_count)
        release_today()
        return 0
    if not has_min_stories(report):
        logger.warning(
            "midday_report:too_few_stories_skipped links=%d min=3",
            len(set(re.findall(r"https?://[^\s)>\"']+", report))),
        )
        release_today()
        return 0
    if is_language_leak(report):
        logger.warning("midday_report:language_leak_skipped")
        release_today()
        return 0

    # 4b. Guardrails — fact-recency gate + link validation.
    from rugmunchbot.channels.report_guardrails import report_guardrails

    ok, reasons = await report_guardrails(report)
    if not ok:
        logger.warning("midday_report:guardrails_blocked reasons=%s", ";".join(reasons))
        release_today()
        return 0

    # 5. Hard quality gate (lint → autofix → one regeneration → block).
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    gate = await pre_post_quality_gate(
        report,
        image_url=pick_midday_image(context),
        source=EDITION,
        kind="midday_report",
        regenerate=_regenerate_hook(context),
        logger=logger,
    )
    if not gate.ok:
        logger.warning("midday_report:quality_gate_blocked violations=%d", len(gate.violations))
        release_today()
        return 0
    report = gate.text
    if gate.regenerated:
        report = cleanup_html(report) or report

    # 6. Content-hash ledger (near-duplicate guard).
    from rugmunchbot.channels.telegram_news._ledger import evaluate_report, record_publication

    ledger_eval = evaluate_report(report, namespace="midday_report", min_words=200, log=logger)
    if not ledger_eval.allowed:
        logger.warning("midday_report:publication_skipped reason=%s", ledger_eval.reason)
        release_today()
        return 0

    # 7. Image — story og:image, else the branded Midday graphic.
    from rugmunchbot.support.post_graphics import graphic_for_type

    image_url = pick_midday_image(context) or graphic_for_type(
        "midday_report", MIDDAY_REPORT_GRAPHIC
    )

    # 8. Feed mirror FIRST so the Telegram closer can deep-link the post.
    feed_url = await _mirror_to_feed(report, image_url)

    # 9. Post to the main channel.
    sent = await _send_parts(ctx, chat_id, report, image_url, feed_url)
    if sent == 0:
        release_today()
        return 0

    # 10. Record the stories used so the Evening Wrap excludes them.
    from rugmunchbot.channels._edition_dedup import mark_edition_stories

    mark_edition_stories("midday", context.get("macro_stories") or [])

    record_publication("midday_report", ledger_eval.content_hash, log=logger)
    logger.info("midday_report:posted parts=%d feed=%s", sent, feed_url)
    return 1


__all__ = [
    "EDITION",
    "FEED_POST_URL_FMT",
    "TASK_CLASS",
    "cleanup_html",
    "post_midday_report",
]

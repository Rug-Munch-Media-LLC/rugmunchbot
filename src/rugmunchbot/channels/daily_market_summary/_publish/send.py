"""Sending/publishing the daily market briefs: Telegram delivery and the
rugmunch.io feed mirror."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.daily_market_summary._assemble import gather_summary_data
from rugmunchbot.channels.daily_market_summary._config import (
    DAILY_MARKET_SUMMARY_FEED_ENABLED,
    logger,
)
from rugmunchbot.channels.daily_market_summary._publish.assemble import (
    _has_min_stories,
    _is_language_leak,
    _strip_prompt_marker,
)
from rugmunchbot.channels.daily_market_summary._publish.fetch import _pick_report_image
from rugmunchbot.channels.daily_market_summary._publish.format import (
    MORNING_EDITION,
    _edition_name,
    _edition_slug,
    _resolve_edition,
    _split_for_telegram,
    build_messages,
    cleanup_html,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def publish_to_feed(
    report: str,
    *,
    title: str | None = None,
    edition: str | None = None,
    feature_image: str = "",
) -> dict[str, Any]:
    """Publish the brief to the rugmunch.io feed (Ghost content platform).

    The rugmunch.io social feed is backed by the Ghost content platform
    (``app.content_platform.py`` — "@CryptoRugMunch Feed engine"); the same
    ``create_post`` path the Morning Brief uses (``post_type="briefing"``).
    ``feature_image`` is the resolved article image; when empty ``create_post``
    falls back to the branded placeholder so the feed post always carries one.
    Fail-open: missing session cookie, network errors, and Ghost HTTP errors
    all log and return — the Telegram post is never blocked.
    """
    stamp = datetime.now(UTC).strftime("%B %d, %Y")
    try:
        from rugmunchbot._vendor.content_platform import create_post

        return await create_post(
            content=report,
            post_type="briefing",
            title=title or f"{edition or _edition_name()} — {stamp}",
            tags=["daily", "market-summary"],
            feature_image=feature_image,
        )
    except Exception as exc:  # fail-open — feed publish is additive
        logger.warning("daily_market_summary:feed_publish_failed err=%s", str(exc)[:200])
        return {"error": str(exc)}


class _ImagePost(str):
    """A report part carrying an ``image_url`` — duck-types ``NewsPost`` so
    the shared ``_post_photo_aware`` sender attaches the photo."""

    image_url: str = ""

    def __new__(cls, text: str, image_url: str) -> _ImagePost:
        obj = super().__new__(cls, text)
        obj.image_url = image_url
        return obj


async def post_daily_market_summary(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Gather → LLM-write → clean → post to @cryptorugmuncher + feed.
    Returns 1 when the Telegram post shipped, 0 otherwise. Fails open at every
    step: missing chat id, no data, LLM failure, Telegram send error, or feed
    publish error all log and return without raising.
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from rugmunchbot.channels.scheduler import _chat_id, _safe_send
    from rugmunchbot.channels.scheduler._core import _post_photo_aware

    chat_id = _chat_id("main")  # registry ``main`` → @cryptorugmuncher
    if not chat_id:
        logger.info("daily_market_summary:no_main_chat_id_skipped")
        return 0

    context = await gather_summary_data()
    if not context.get("sources"):
        logger.info("daily_market_summary:no_data_sources_skipped")
        return 0

    edition = _resolve_edition(ctx)
    messages = build_messages(context, edition=edition)
    try:
        from rugmunchbot.llm_config import chat_with_fallback

        raw = await chat_with_fallback(messages, "long")
    except Exception as exc:  # fail-open — a broken LLM never crashes the tick
        logger.warning("daily_market_summary:llm_failed err=%s", str(exc)[:160])
        raw = ""
    # Strip prompt scaffolding the model may echo verbatim (meta-instruction
    # headers like "AI DISCLOSURE:" are voice guidance, never output).
    for marker in (
        "AI DISCLOSURE:",
        "OUTPUT FORMAT",
        "MASTHEAD (",
        "MASTHEAD DONE",
        "NOW THE BODY",
        "MARKET DRIVERS",
        "cryptonews_rss",
        "rag_intel",
        "ranked_stories",
        "HARD RULES",
        "SECTION HEADERS",
        "TYPOGRAPHY",
        "## MARKET",
        "## NARRATIVE",
        "## TRENDING",
        "## LIQUIDITY",
        "## WHALE",
        "## REGULATORS",
        "## TOP NEWS",
        "## RISK",
        "## ON-CHAIN",
        "## SOCIAL",
        "## SENTIMENT",
        "## KOL",
        "## THE RUG TOOLKIT",
        "El informe",
        "The user just asked",
        "No further action needed",
        "ha sido generado",
        "cumpliendo todas",
        "Sin filler",
        "Colores y",
        "Masthead con la forma",
    ):
        raw = _strip_prompt_marker(
            raw, marker, guarded=marker.startswith("## ") or marker == "MARKET DRIVERS"
        )
    # Stub guard (2026-08-26): the morning run posted a 154-char stub
    # because the LLM chain returned a degenerate one-off. If the model
    # under-delivers, retry once with an explicit length demand; if it
    # STILL under-delivers, assemble a deterministic report from the raw
    # context — the market report never ships empty again.
    min_chars = 1500
    if len(raw.strip()) < min_chars:
        logger.warning("daily_market_summary:llm_short retrying chars=%s", len(raw.strip()))
        retry_msgs = build_messages(context, edition=edition)
        retry_msgs[1]["content"] += (
            "\n\nIMPORTANT: your previous attempt was too short. Write the FULL "
            "7000-8000 character report now — every section, every item, with links."
        )
        try:
            from rugmunchbot.llm_config import chat_with_fallback

            raw2 = await chat_with_fallback(retry_msgs, "long")
            if len(raw2.strip()) > len(raw.strip()):
                raw = raw2
        except Exception as exc:
            logger.warning("daily_market_summary:llm_retry_failed err=%s", str(exc)[:120])
    if not raw or not raw.strip():
        logger.info("daily_market_summary:llm_empty_skipped sources=%s", context.get("sources"))
        return 0

    report = cleanup_html(raw)
    if not report:
        logger.info("daily_market_summary:cleaned_empty_skipped")
        return 0

    # Post-generation guard (2026-09-04): never ship an empty brief or a
    # language leak. A report with <100 words or fewer than 3 distinct links
    # is an empty/placeholder draft — skip. A report whose word share of
    # common Spanish/French/German stopwords exceeds 20% is a language
    # drift — skip.
    word_count = len(re.findall(r"\S+", report))
    if word_count < 100:
        logger.warning("daily_market_summary:too_short_skipped words=%d min=100", word_count)
        return 0
    if not _has_min_stories(report):
        logger.warning(
            "daily_market_summary:too_few_stories_skipped links=%d min=3",
            len(set(re.findall(r"https?://[^\s)>\"']+", report))),
        )
        return 0
    if _is_language_leak(report):
        logger.warning("daily_market_summary:language_leak_skipped")
        return 0

    # HARD quality gate (2026-08-31): the flagship report must pass the
    # deterministic content lint before it touches the channel. Autofix
    # (dedupe links, placeholder image) runs first; a still-dirty report
    # gets ONE stricter-prompt LLM regeneration; if it STILL fails the
    # edition is skipped — crap never ships to @cryptorugmuncher.
    from rugmunchbot.support.content_lint import canonical_url
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    async def _regenerate_report(bad_text: str, violations: list[Any]) -> str:
        del bad_text
        detail = "; ".join(f"{v.rule}: {v.phrase}" for v in violations[:6])
        retry_msgs = build_messages(context, edition=edition)
        retry_msgs[1]["content"] += (
            "\n\nSTRICT REWRITE REQUIRED — your previous draft failed the "
            f"content quality lint ({detail[:300]}). Rules: no banned phrases, "
            "each URL appears AT MOST ONCE, at least 20 words of original "
            "commentary per story, no '[SEV] Title / Source:' template shape. "
            "Rewrite the FULL report now."
        )
        from rugmunchbot.llm_config import chat_with_fallback

        return await chat_with_fallback(retry_msgs, "long")

    gate = await pre_post_quality_gate(
        report,
        image_url=_pick_report_image(context),
        source=edition,
        kind="market_summary",
        regenerate=_regenerate_report,
        logger=logger,
    )
    if not gate.ok:
        logger.warning(
            "daily_market_summary:quality_gate_blocked edition=%s violations=%d",
            edition,
            len(gate.violations),
        )
        return 0
    report = gate.text
    if gate.regenerated:
        report = cleanup_html(report) or report
    logger.info(
        "daily_market_summary:report_ready chars=%s sources=%s",
        len(report),
        context.get("sources"),
    )

    # Publication ledger + content-hash near-dup guard (2026-09): the flagship
    # report must be NEW — block a byte-identical re-run (same content hash in
    # the last N morning editions) and never ship a blank/degenerate body.
    # Fail-open: a Redis outage degrades to "ok" and posts.
    from rugmunchbot.channels.telegram_news._ledger import (
        evaluate_report,
        record_publication,
        select_edition_image,
    )

    ledger_eval = evaluate_report(
        report, namespace="morning_rug", min_words=100, log=logger
    )
    if not ledger_eval.allowed:
        logger.warning(
            "daily_market_summary:publication_skipped reason=%s", ledger_eval.reason
        )
        return 0
    record_publication("morning_rug", ledger_eval.content_hash, log=logger)
    # Explicit image default: resolved story image (og:image) or the RugMunch
    # Intelligence logo (app favicon) — NEVER the @cryptorugmuncher X/Telegram
    # profile pic, which is_valid_image rejects.
    report_image = select_edition_image(
        candidate=gate.image_url or _pick_report_image(context)
    )

    # Market-report delivery doctrine (2026-08-26): Telegram readers get the
    # FULL report — split at line boundaries into ≤3900-char parts (Telegram's
    # 4096 limit minus formatting headroom). No trimming: free-for-all long
    # form on TG; feed.rugmunch.io carries the most detailed edition.
    #
    # Feed publishes FIRST so the TG closing block can link the live article.
    feed_url = ""
    if DAILY_MARKET_SUMMARY_FEED_ENABLED:
        feed_result = await publish_to_feed(
            raw,
            edition=edition,
            feature_image=report_image,
        )
        feed_url = str(feed_result.get("url") or "") if isinstance(feed_result, dict) else ""
        if feed_url:
            logger.info("daily_market_summary:feed_published url=%s", feed_url)

    parts = _split_for_telegram(report)
    # One-link-per-source: only append the feed link when the report body
    # does not already link it (the LLM is instructed to link each source
    # once; the closer must never duplicate an existing link). Comparison is
    # canonical-URL (http/https, trailing slash, tracking params) so a
    # differently-formatted copy of the same link still counts as linked.
    already_linked = bool(feed_url) and canonical_url(feed_url) in {
        canonical_url(u) for u in re.findall(r"https?://[^\s)>\"']+", report)
    }
    closer = (
        f"📖 <b>{edition}</b> — full write-up on feed.rugmunch.io"
        + (f' — <a href="{feed_url}">read it here</a>.' if feed_url and not already_linked else ".")
        + "\n🤖 Live token intel, scans &amp; alerts: @rugmunchbot"
    )
    parts[-1] = parts[-1].rstrip() + "\n\n" + closer

    # Photo-aware delivery (2026-09): the flagship report leads with an image
    # when the gathered context carries one (top news story's og:image). The
    # first part goes through the shared photo-aware path — a caption-too-long
    # or bad-URL failure falls back to plain text inside send_news_photo, so
    # delivery is never blocked by the image.
    image_url = report_image

    sent = 0
    for idx, part in enumerate(parts, 1):
        if idx == 1 and image_url:
            sent_ok = await _post_photo_aware(
                ctx.bot, chat_id, _ImagePost(part, image_url), _safe_send, content_type="market"
            )
        else:
            sent_ok = await _safe_send(ctx.bot, chat_id, part, content_type="market")
        if sent_ok:
            sent += 1
            logger.info(
                "daily_market_summary:posted_part %d/%d chars=%s",
                idx,
                len(parts),
                len(part),
            )
        else:
            break
    if sent == 0:
        return 0
    # Cross-edition dedup (2026-09-05): once the morning edition has shipped,
    # record its ranked stories so the Midday Market Update excludes them.
    # Morning-only: the midday edition is the day's last brief, nothing
    # downstream needs its list. Fail-open — never blocks the tick.
    if edition == MORNING_EDITION:
        try:
            from rugmunchbot.channels._edition_dedup import mark_morning_stories

            mark_morning_stories(context.get("ranked_stories") or [])
        except Exception as exc:
            logger.warning("daily_market_summary:dedup_mark_failed err=%s", str(exc)[:120])
    # W30 top-of-food-chain mirror: a main-channel post lands on
    # feed.rugmunch.io too (best-effort, never blocks the tick). The Ghost
    # publish above covers rugmunch.io; this covers the static feed store.
    try:
        from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

        mirror_post_to_feed(
            {
                "text": report[:3900],
                "story_ref": feed_url
                or f"{_edition_slug(edition)}-{datetime.now(UTC).strftime('%Y%m%d')}",
                "source": feed_url or "rugmunch.io",
                # 2026-09-05: updates are FEED briefings, not blog posts —
                # kind drives the Ghost tag + feed store.
                "kind": "briefing",
                "title": f"{edition} — {datetime.now(UTC).strftime('%B %d, %Y')}",
                "tags": ["briefing", "market-update", edition.lower().replace(" ", "-")],
                "image_url": report_image,
            }
        )
    except Exception as exc:
        logger.warning("daily_market_summary:feed_mirror_failed err=%s", str(exc)[:120])
    return 1

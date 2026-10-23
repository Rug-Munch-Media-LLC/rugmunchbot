"""Scheduled TG-news broadcast job (``post_tg_news_update``).

Submodule of ``channels/telegram_news.py`` — owns the **scheduled-broadcast**
logic: sweep → persist → importance-score → route digests to the news/alpha
channels, plus the additive feed/market-awareness side-effects.

Pipeline-order and patch-point contract: every stage that tests patch on the
shim (``sweep_telegram_news``, ``persist_articles``, ``score_importance``,
``inject_into_news_cache``, ``publish_market_awareness``) is resolved through
``channels/telegram_news.py`` at call time; the scheduler helpers
(``_chat_id`` / ``_remember`` / ``_dedupe_key`` / ``_safe_send``) are
re-imported lazily exactly like the original module so
``monkeypatch.setattr(channels_scheduler, "_chat_id", ...)`` keeps working.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels._media import send_news_photo
from rugmunchbot.channels.formatters import (
    NewsPost,
    format_breaking_post,
    format_feature_post,
    format_news_post,
    format_scam_digest,
)
from rugmunchbot.channels.importance import select_important
from rugmunchbot.channels.telegram_news import _coverage
from rugmunchbot.channels.telegram_news._digest import (
    _apply_commentaries,
    _article_to_post_dict,
    _news_post_for,
)
from rugmunchbot.channels.telegram_news._sources import TG_NEWS_POST_LIMIT
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")


async def _publish_allowed(dest: str, article: Any) -> bool:
    """Durable no-duplication gate — checked BEFORE any publish.

    Two layers, both must pass:

    1. **Published-id ledger** (``_ledger``): keyed on the source item
       identity (canonical URL hash), Redis-backed with an in-process
       fallback. Survives restarts, so a redeploy never re-posts a story.
    2. ``_remember`` — the existing per-process freshness check (namespaced
       ``tgnews`` / ``tgmain`` / ``tgalpha`` / ``tgscam``), unchanged.

    The ledger entry is *recorded* by :func:`_note_published` only after a
    successful send, so a blocked or failed post never poisons the ledger.

    Fail-open like the rest of this pipeline: a ledger outage returns True
    (allow), never blocks the channel.
    """
    from rugmunchbot.channels.scheduler import _dedupe_key, _remember
    from rugmunchbot.channels.telegram_news._ledger import is_published, item_key

    key = item_key(article)
    if await is_published(dest, key):
        logger.info(
            "tg_news:ledger_blocked dest=%s title=%s",
            dest,
            str(getattr(article, "title", "") or "")[:80],
        )
        return False
    return _remember(dest, _dedupe_key(dest, _article_to_post_dict(article)))


async def _note_published(dest: str, article: Any) -> None:
    """Record a successful publish in the ledger. Never raises."""
    from rugmunchbot.channels.telegram_news._ledger import item_key, mark_published

    try:
        await mark_published(dest, item_key(article))
    except Exception as exc:  # fail-open — a ledger write failure posts nothing twice
        logger.debug("tg_news:ledger_mark_failed dest=%s err=%s", dest, str(exc)[:80])


def _filter_anchor_dupes(scored: list[Any]) -> list[Any]:
    """Drop scored articles whose story the daily anchor already carried.

    The three daily reports are the day's canonical coverage; a story they
    covered must not re-post as breaking news on main. Delegates to
    :func:`rugmunchbot.channels.report_anchor.filter_scored_articles`
    (fail-open: any error keeps the full list).
    """
    from rugmunchbot.channels.report_anchor import filter_scored_articles

    return filter_scored_articles(scored, _article_to_post_dict)


async def _post_backend_trending_fallback(
    ctx: ContextTypes.DEFAULT_TYPE,
    news_chat_id: str | None,
    main_chat_id: str | None,
    safe_send: Any,
) -> int:
    """News fallback rail: post RugBrain news → backend trending when sweep is quiet.

    Fallback chain (each tier fail-open, graceful downgrade):
    ``fetch_rag_news_items`` (RugBrain ``news_v2``, rerank=True so the reranker
    IS the ranking) → ``fetch_trending_news`` (our own ``/api/v1/news/trending``
    — real headlines with source links, never synthesized) → no-post.

    The top rugbrain-reranked story goes to the news channel (and the top one
    to main when its importance clears the main gate). Dedupes through the
    ``tgnews`` namespace so a story can never post twice across the sweep +
    fallback rails. Returns the number of messages posted (0..2). Fail-open:
    both rails empty → 0.
    """
    from rugmunchbot.channels.content import fetch_rag_news_items, fetch_trending_news
    from rugmunchbot.channels.formatters import format_breaking_post
    from rugmunchbot.channels.importance import (
        score_importance as _score_importance,
    )
    from rugmunchbot.channels.router import (
        ROUTE_MAIN_MIN_IMPORTANCE,
        ROUTE_NEWS_MIN_IMPORTANCE,
    )
    from rugmunchbot.channels.scheduler import _article_to_post_dict

    if not news_chat_id and not main_chat_id:
        return 0

    # RugBrain news_v2 rail FIRST (rerank=True — the reranker IS the ranking),
    # then the backend trending rail, then no-post. Graceful downgrade:
    # each tier is fail-open (returns [] on outage), so a RugBrain blip
    # silently falls through to fetch_trending_news, and a total outage
    # posts nothing and waits for the next tick.
    items = await fetch_rag_news_items(limit=TG_NEWS_POST_LIMIT)
    if not items:
        logger.debug("tg_news:rag_news_empty_falling_back_to_trending")
        items = await fetch_trending_news(limit=TG_NEWS_POST_LIMIT)
    if not items:
        logger.debug("tg_news:backend_fallback_empty")
        return 0

    # Score the backend headlines with the same reranker the sweep uses so
    # the fallback respects the importance floor (fail-open: heuristic).
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

    articles = [
        NewsArticle(
            title=str(it.get("title") or "Untitled")[:200],
            body=str(it.get("summary") or it.get("title") or "")[:500],
            source_channel=str(it.get("source") or "backend"),
            source_url=str(it.get("url") or "https://rugmunch.io/news"),
            category="crypto_news",
        )
        for it in items
        if str(it.get("title") or "").strip()
    ]
    scored = await _score_importance(articles)
    posted = 0

    if news_chat_id:
        # Keep stories at/above the news floor; order best-first. (The
        # reranker may assign low scores to short backend blurbs — that is
        # fine: the fallback posts the top of the window, never junk.)
        keep = sorted(
            ((a, s) for a, s in zip(articles, scored, strict=False)),
            key=lambda pair: pair[1].importance,
            reverse=True,
        )
        keep = [pair for pair in keep if pair[1].importance >= ROUTE_NEWS_MIN_IMPORTANCE]
        fresh: list[Any] = []
        for a in keep[:TG_NEWS_POST_LIMIT]:
            if await _publish_allowed("tgnews", a):
                fresh.append(a)
        if fresh:
            post_items = [_article_to_post_dict(a) for a in fresh[:TG_NEWS_POST_LIMIT]]
            commentaries = await _tg.attach_commentaries(post_items)
            ok = await _post_formatted(
                ctx.bot,
                news_chat_id,
                _news_post_for(fresh[:TG_NEWS_POST_LIMIT], commentaries=commentaries),
                safe_send,
                content_type="news",
            )
            if ok:
                for a in fresh[:TG_NEWS_POST_LIMIT]:
                    await _note_published("tgnews", a)
                posted += 1
                logger.info(
                    "tg_news:posted_backend_fallback count=%s",
                    min(len(fresh), TG_NEWS_POST_LIMIT),
                )

    if main_chat_id and scored:
        top = max(scored, key=lambda s: s.importance)
        if top.importance >= ROUTE_MAIN_MIN_IMPORTANCE:
            top_article = articles[scored.index(top)]
            post = _article_to_post_dict(top_article)
            if await _publish_allowed("tgmain", top_article):
                formatted = (
                    format_breaking_post(post)
                    if _tg.is_breaking_article(top_article)
                    else format_feature_post(post)
                )
                from rugmunchbot.channels.formatters._primitives import (
                    enforce_main_link_contract,
                )

                formatted = enforce_main_link_contract(formatted, post)
                if formatted is not None:
                    ok = await _post_formatted(
                        ctx.bot, main_chat_id, formatted, safe_send, content_type="news"
                    )
                    if ok:
                        await _note_published("tgmain", top_article)
                        posted += 1
                        logger.info("tg_news:posted_backend_main title=%s", top_article.title[:80])
    logger.info("tg_news:backend_fallback_posted count=%s", posted)
    return posted


async def _post_formatted(
    bot: Any, chat_id: str, post: NewsPost, safe_send: Any, *, content_type: str | None = None
) -> bool:
    # LIVE GATE (2026-10-23): per-channel dry-run, before the photo path.
    from rugmunchbot._vendor.channel_gate import is_live
    from rugmunchbot.channels.scheduler import _channel_role

    role = _channel_role.role_for_chat_id(chat_id)
    if role is not None and not is_live(role):
        logger.warning("channels:suppressed role=%s reason=dry", role)
        return False
    from rugmunchbot.channels.scheduler._core import _enforce_content_contract

    if not _enforce_content_contract(chat_id, content_type):
        return False
    # HARD quality gate (2026-08-31): no post ships without passing the
    # deterministic content lint. Auto-fix (dedupe links, placeholder
    # image) runs first; a still-dirty post is logged and skipped.
    from rugmunchbot.support.quality_gate import pre_post_quality_gate

    gate = await pre_post_quality_gate(
        str(post),
        image_url=getattr(post, "image_url", "") or "",
        source="tg_news",
        kind="digest",
        logger=logger,
    )
    if not gate.ok:
        return False
    post = NewsPost(gate.text)
    post.image_url = gate.image_url
    image_url = getattr(post, "image_url", "") or ""
    # FINAL image-leak guard (support.image_policy) — text-only on reject.
    from rugmunchbot.support.image_policy import is_valid_image

    if image_url and not is_valid_image(image_url):
        logger.warning("tg_news:banned_image_dropped url=%s", image_url[:120])
        image_url = ""
    if image_url:
        return await send_news_photo(bot, chat_id, caption=post, photo_url=image_url)
    return await safe_send(bot, chat_id, post, content_type=content_type)


async def post_tg_news_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Sweep TG channels → persist to website → score importance → route digests.

    Returns 1 when a digest was posted (news or alpha), 0 otherwise. Fails
    open at every step: missing chat ids, no client, empty sweep, Postgres
    outage, or a Telegram send error all log and return without raising.

    Pipeline order (efficiency contract):
    ``read (bounded) → normalize → dedupe (multi-key: canonical URL / t.me
    URL / normalized title / content hash, in-batch + Redis cross-run) →
    store (news_items + Redis dedup mark) → reranker importance → route``.

    Routing: **news**-categorized articles are persisted and posted to the
    news channel; **alpha**-categorized articles are persisted and posted to
    the alpha channel (when ``_chat_id("alpha")`` resolves); **scam**-
    categorized articles (scam-call channels swept since 2026-08-29) are
    persisted and posted to the alerts channel (when ``_chat_id("alerts")``
    resolves) — danger intel, never news headlines. Every swept article is
    persisted (store everything); only the articles ``select_important``
    keeps (rerank importance >= ``RMI_NEWS_IMPORTANCE_THRESHOLD``, or the
    top-N under the heuristic fallback when qwen3 /rerank is down) are
    posted — per category.
    Dedup (multi-key, canonical-URL-first) + rerank importance happen BEFORE
    any post — the post-time ``_remember`` guard is a final freshness check,
    so only fresh important stories ship.

    Post variety on the news channel (deterministic — see
    ``_digest._news_post_for``): a breaking top story (🚨/BREAKING/URGENT/
    JUST IN/FLASH, ``is_breaking_article``) posts **solo immediately** with its
    official link (``format_breaking_post``); a single story posts as a **solo
    top-story card** (``format_feature_post``); stories sharing a ``$TOKEN``
    form a **themed roundup** (``format_themed_post``); otherwise the batch
    alternates **top-3 hybrid** (``format_hybrid_post``) and full **numbered
    digest** (``format_news_post``) by batch parity. Selection is ordered by
    rerank importance, then ``lightweight_importance``.

    Main-channel solo (importance ≥ 0.7): the top story posts solo — 🚨
    ``format_breaking_post`` when it carries a breaking marker, else the ⭐
    ``format_feature_post`` card with its image. High-importance stories also
    surface on the news channel (dual-post) and, after a successful main post,
    publish to the rugmunch.io (Ghost) feed best-effort (fail-open).

    AI commentary: every posted story gets a one-line "why it matters" hook
    (``attach_commentaries`` → ``generate_commentary``), bounded per call,
    parallel per batch, fail-open to title-only.

    Additive side-effects (all fail-open): ``inject_into_news_cache`` (website
    feed cache), ``publish_market_awareness`` (``rmi:news:market_awareness``),
    and ``ingest_into_rag`` (cluster → embed into ``news_v2``).
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from rugmunchbot.channels.router import ROUTE_ALPHA_CATEGORY, ROUTE_SCAM_CATEGORY, route_news
    from rugmunchbot.channels.scheduler import (
        _chat_id,
        _safe_send,
    )

    news_chat_id = _chat_id("news")
    alpha_chat_id = _chat_id("alpha")
    main_chat_id = _chat_id("main")
    alerts_chat_id = _chat_id("alerts")
    if not news_chat_id and not alpha_chat_id and not main_chat_id and not alerts_chat_id:
        return 0

    articles = await _tg.sweep_telegram_news()
    if not articles:
        # 2026-08-29 news-content fix: the Telegram sweep is frequently
        # empty (headless client unavailable, junk channels, cooldowns), so
        # @rmicryptonews went silent. Fall back to the backend trending
        # news rail (``fetch_trending_news`` — our own /api/v1/news/trending)
        # so the news channel still posts top stories. Fail-open: if that is
        # empty too, post nothing and let the next tick try again.
        logger.debug("tg_news:no_fresh_articles")
        return await _post_backend_trending_fallback(ctx, news_chat_id, main_chat_id, _safe_send)

    counts = await _tg.persist_articles(articles)
    logger.info(
        "tg_news:sweep_done articles=%s ingested=%s duplicate=%s errors=%s",
        len(articles),
        counts["ingested"],
        counts["duplicate"],
        counts["errors"],
    )

    scored = await _tg.score_importance(articles)  # type: ignore[attr-defined]
    if not scored:
        logger.info("tg_news:importance_scoring_empty articles=%s", len(articles))
        return 0

    posted = 0

    # Unified routing: partition scored articles by destination via route_news.
    # High-importance (≥0.7) stories route to ``main`` AND also surface on the
    # news channel (requirement: important stories auto-post to news + main).
    # Alpha-category stories keep the design invariant — they never land on
    # the news channel (main-only above the gate, alpha lane below it).
    # Scam-category stories (scam-call channels) route to the ALERTS channel
    # below the main gate — danger intel, never news headlines.
    news_scored: list[Any] = []
    alpha_scored: list[Any] = []
    main_scored: list[Any] = []
    alerts_scored: list[Any] = []
    routed: dict[str, str] = {}
    posted_keys: set[str] = set()
    for s in scored:
        # fallback=True when the reranker was down: heuristic scores are not on
        # the cross-encoder scale, so the 0.4 news floor must not be applied.
        dest = route_news(s.importance, s.article.category, fallback=s.fallback)
        routed[_coverage.article_key(s.article)] = dest
        if dest == "main":
            main_scored.append(s)
            if s.article.category not in (ROUTE_ALPHA_CATEGORY, ROUTE_SCAM_CATEGORY):
                news_scored.append(s)
        elif dest == "alpha":
            alpha_scored.append(s)
        elif dest == "alerts":
            alerts_scored.append(s)
        elif dest == "news":
            news_scored.append(s)

    # Route: news → news channel
    if news_chat_id and news_scored:
        selected = select_important(news_scored, top_n=TG_NEWS_POST_LIMIT)
        # Secondary sort by the deterministic lightweight importance score
        # (stable) so the digest is ordered best-first even when the rerank
        # scores tie (e.g. heuristic fallback or stub tests).
        selected = sorted(selected, key=lambda s: s.importance, reverse=True)

        # Post variety: deterministic format rotation (see _digest._news_post_for):
        # a top story carrying a breaking marker (🚨/BREAKING/URGENT/JUST IN/
        # FLASH in its title) is posted SOLO immediately with its official
        # link; otherwise the fresh batch is shaped by story count / shared
        # topic / parity into a feature card, themed roundup, hybrid, or
        # numbered digest so the channel isn't the same digest every tick.
        if selected and _tg.is_breaking_article(selected[0].article):
            top = selected[0].article
            post = _article_to_post_dict(top)
            if await _publish_allowed("tgnews", top):
                # AI "why it matters" one-liner (bounded, fail-open) — the post
                # ships with just the headline when the LLM call fails/empties.
                _apply_commentaries([post], await _tg.attach_commentaries([post]))
                ok = await _post_formatted(
                    ctx.bot,
                    news_chat_id,
                    format_breaking_post(post),
                    _safe_send,
                    content_type="news",
                )
                if ok:
                    await _note_published("tgnews", top)
                    posted_keys.add(_coverage.article_key(top))
                    logger.info("tg_news:posted_breaking title=%s", top.title[:80])
                    posted += 1
        else:
            fresh: list[Any] = []
            for scored_article in selected:
                if await _publish_allowed("tgnews", scored_article.article):
                    fresh.append(scored_article.article)
            if fresh:
                fresh_articles = fresh[:TG_NEWS_POST_LIMIT]
                post_items = [_article_to_post_dict(a) for a in fresh_articles]
                # One bounded AI commentary per story (parallel, fail-open);
                # ``_news_post_for`` attaches them onto the post dicts.
                commentaries = await _tg.attach_commentaries(post_items)
                ok = await _post_formatted(
                    ctx.bot,
                    news_chat_id,
                    _news_post_for(fresh_articles, commentaries=commentaries),
                    _safe_send,
                    content_type="news",
                )
                if ok:
                    for a in fresh_articles:
                        await _note_published("tgnews", a)
                        posted_keys.add(_coverage.article_key(a))
                    logger.info("tg_news:posted_news count=%s", min(len(fresh), TG_NEWS_POST_LIMIT))
                    posted += 1

    # Route: main (importance ≥ 0.7) → @cryptorugmuncher channel, solo.
    # The high-importance solo path: breaking markers keep the 🚨 shape,
    # otherwise the compelling ⭐ feature card (rewritten title + one-line
    # hook) — never a bare link; the story's image rides the NewsPost so
    # _post_formatted attaches it as a photo.
    if main_chat_id and main_scored:
        # Report-anchor dedup (2026-10-17): the three daily reports are the
        # day's canonical coverage. A story the anchor already carried must
        # NOT re-post as breaking news on main — the anchor wins the story.
        # Fail-open: a Redis outage leaves the list unchanged.

        main_scored = _filter_anchor_dupes(main_scored)
        selected_main = select_important(main_scored, top_n=TG_NEWS_POST_LIMIT)
        selected_main = sorted(selected_main, key=lambda s: s.importance, reverse=True)
        if selected_main:
            top = selected_main[0].article
            post = _article_to_post_dict(top)
            if await _publish_allowed("tgmain", top):
                # WO-2 hard cap: ≤9/day on main. Overflow demotes to the
                # news lane — the story already posts there natively, so a
                # capped main copy is skipped without losing the story.
                from rugmunchbot.channels.scheduler import _main_admission

                if await _main_admission("news") != "main":
                    logger.info("tg_news:main_capped_demoted title=%s", top.title[:80])
                else:
                    # AI "why it matters" one-liner for the main-channel card
                    # (bounded, fail-open — ships title-only when the LLM fails).
                    _apply_commentaries([post], await _tg.attach_commentaries([post]))
                    formatted = (
                        format_breaking_post(post)
                        if _tg.is_breaking_article(top)
                        else format_feature_post(post)
                    )
                    # W19 link contract: main posts MUST carry a link.
                    from rugmunchbot.channels.formatters._primitives import (
                        enforce_main_link_contract,
                    )

                    formatted = enforce_main_link_contract(formatted, post)
                    if formatted is not None:
                        ok = await _post_formatted(
                            ctx.bot, main_chat_id, formatted, _safe_send, content_type="news"
                        )
                    else:
                        ok = False
                        logger.warning(
                            "tg_news:main_link_contract_blocked title=%s", top.title[:80]
                        )
                    if ok:
                        from rugmunchbot.channels.scheduler import _record_main_post

                        await _record_main_post()
                        await _note_published("tgmain", top)
                        posted_keys.add(_coverage.article_key(top))
                        logger.info("tg_news:posted_main title=%s", top.title[:80])
                        posted += 1
                        # rugmunch.io (Ghost) feed publish — best-effort + fail-open:
                        # the Telegram posts already went out; a missing Ghost API
                        # key / session cookie never blocks them (requirement: ≥0.7
                        # stories auto-post to news + main + ghost feed).
                        # NO-DUPLICATION: the ledger is checked BEFORE the publish
                        # so a re-posted story never lands on the feed page twice.
                        if await _publish_allowed("feed", top):
                            await _tg.publish_story_to_feed(
                                str(formatted), title=top.post_title or top.title
                            )
                            await _note_published("feed", top)
                        # W30 top-of-food-chain mirror: feed.rugmunch.io static
                        # store too (same atomic writer as the RAG/exploit lanes).
                        if await _publish_allowed("mirror", top):
                            try:
                                from rugmunchbot.channels.scheduler._posts_rag import (
                                    mirror_post_to_feed,
                                )

                                mirror_post_to_feed(
                                    {
                                        "text": str(formatted),
                                        "story_ref": str(
                                            post.get("canonical_url") or post.get("url") or ""
                                        )
                                        or (top.post_title or top.title),
                                        "source": str(post.get("source") or "tg_news"),
                                        "kind": "story",
                                    }
                                )
                                await _note_published("mirror", top)
                            except Exception as exc:
                                logger.warning("tg_news:feed_mirror_failed err=%s", str(exc)[:120])

    # Route: alpha → alpha channel (additive; no alpha chat id → skip)
    if alpha_chat_id and alpha_scored:
        selected_alpha = select_important(alpha_scored, top_n=TG_NEWS_POST_LIMIT)
        fresh_alpha: list[Any] = []
        for scored_article in selected_alpha:
            if await _publish_allowed("tgalpha", scored_article.article):
                fresh_alpha.append(scored_article.article)
        if fresh_alpha:
            ok = await _post_formatted(
                ctx.bot,
                alpha_chat_id,
                format_news_post(
                    [_article_to_post_dict(a) for a in fresh_alpha[:TG_NEWS_POST_LIMIT]]
                ),
                _safe_send,
                content_type="alpha",
            )
            if ok:
                for a in fresh_alpha[:TG_NEWS_POST_LIMIT]:
                    await _note_published("tgalpha", a)
                    posted_keys.add(_coverage.article_key(a))
                logger.info(
                    "tg_news:posted_alpha count=%s", min(len(fresh_alpha), TG_NEWS_POST_LIMIT)
                )
                posted += 1

    # Route: scam-call intel → alerts channel (@rmialerts). Scam-call
    # content cleared the news floor but sits below the main gate — it is
    # danger intel, so it posts to the alerts channel as a compact digest
    # (never the news channel). Deduped per URL via the global namespace
    # (prefix ``tgscam``), exactly like the news/alpha lanes.
    if alerts_chat_id and alerts_scored:
        selected_alerts = select_important(alerts_scored, top_n=TG_NEWS_POST_LIMIT)
        fresh_alerts: list[Any] = []
        for scored_article in selected_alerts:
            if await _publish_allowed("tgscam", scored_article.article):
                fresh_alerts.append(scored_article.article)
        if fresh_alerts:
            ok = await _post_formatted(
                ctx.bot,
                alerts_chat_id,
                format_scam_digest(
                    [_article_to_post_dict(a) for a in fresh_alerts[:TG_NEWS_POST_LIMIT]]
                ),
                _safe_send,
                content_type="alert",
            )
            if ok:
                for a in fresh_alerts[:TG_NEWS_POST_LIMIT]:
                    await _note_published("tgscam", a)
                    posted_keys.add(_coverage.article_key(a))
                logger.info(
                    "tg_news:posted_scam_alerts count=%s",
                    min(len(fresh_alerts), TG_NEWS_POST_LIMIT),
                )
                posted += 1

    # Per-source coverage: swept/scored/routed/posted for EVERY channel, so a
    # channel that never reaches content is visible instead of silent.
    _coverage.log_news_coverage(articles, scored, routed, posted_keys)

    if not posted:
        logger.debug("tg_news:all_already_posted")

    # Additive, fail-open side-effects.
    await _tg.inject_into_news_cache(articles)
    await _tg.publish_market_awareness(articles, scored)
    # Feed RAG: cluster the swept articles into stories and embed into
    # ``news_v2`` (the collection the get_news RAG tool searches) so RAG
    # "benefits from" the news lane. Fail-open — a RAG outage never blocks
    # the website/Telegram halves.
    rag_counts = await _tg.ingest_into_rag(articles)
    if rag_counts.get("ingested") or rag_counts.get("errors"):
        logger.info(
            "tg_news:rag_side_effect stories=%s ingested=%s duplicates=%s errors=%s",
            rag_counts.get("stories", 0),
            rag_counts.get("ingested", 0),
            rag_counts.get("duplicates", 0),
            rag_counts.get("errors", 0),
        )

    return 1 if posted else 0

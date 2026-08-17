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

from app.domains.telegram.rugmunchbot.channels._media import send_news_photo
from app.domains.telegram.rugmunchbot.channels.formatters import (
    NewsPost,
    format_breaking_post,
    format_feature_post,
    format_news_post,
)
from app.domains.telegram.rugmunchbot.channels.importance import select_important
from app.domains.telegram.rugmunchbot.channels.telegram_news._digest import (
    _apply_commentaries,
    _article_to_post_dict,
    _news_post_for,
)
from app.domains.telegram.rugmunchbot.channels.telegram_news._sources import TG_NEWS_POST_LIMIT
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")


async def _post_formatted(bot: Any, chat_id: str, post: NewsPost, safe_send: Any) -> bool:
    """Post a formatter result, attaching its image when one is available.

    ``post`` is the ``NewsPost`` from ``format_news_post`` /
    ``format_breaking_post`` (a ``str`` carrying an optional ``.image_url``).
    With a usable http(s) image the post goes through ``send_news_photo``
    (photo + caption; falls back to text internally). Without one the plain
    text ``safe_send`` path (the caller's lazily-imported ``_safe_send``,
    with retries + ``disable_web_page_preview=True``) is used.
    """
    image_url = getattr(post, "image_url", "") or ""
    if image_url:
        return await send_news_photo(bot, chat_id, caption=post, photo_url=image_url)
    return await safe_send(bot, chat_id, post)


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
    the alpha channel (when ``_chat_id("alpha")`` resolves). Every swept
    article is persisted (store everything); only the articles
    ``select_important`` keeps (rerank importance >=
    ``RMI_NEWS_IMPORTANCE_THRESHOLD``, or the top-N under the heuristic
    fallback when Infinity /rerank is down) are posted — per category.
    Dedup (multi-key, canonical-URL-first) + rerank importance happen BEFORE
    any post — the post-time ``_remember`` guard is a final freshness check,
    so only fresh important stories ship.

    Post variety on the news channel (deterministic, by story count / shared
    topic / batch parity — see ``_digest._news_post_for``): a breaking top
    story (🚨/BREAKING/URGENT/JUST IN/FLASH in its title,
    ``is_breaking_article``) is posted **solo immediately** with its official
    link (``format_breaking_post``); a single story is posted as a **solo
    top-story card** (image + one-line hook + lede, ``format_feature_post``);
    stories sharing a ``$TOKEN`` form a **themed roundup**
    (``format_themed_post``); otherwise the batch alternates between the
    **top-3 hybrid** (lead card + image + short list, ``format_hybrid_post``)
    and the full **numbered digest** (``format_news_post``) via a stable
    batch-parity signal, so readers don't see the same digest every tick. The
    selection is sorted by rerank importance, secondarily by the deterministic
    ``lightweight_importance`` score.

    Main-channel solo (importance ≥ 0.7): the top story is posted solo; a
    breaking marker keeps the 🚨 ``format_breaking_post`` shape, otherwise the
    compelling ⭐ ``format_feature_post`` card (rewritten title + one-line
    hook) is used with the story's image attached when one exists. High-
    importance stories ALSO surface on the news channel (dual-post) and, after
    a successful main post, are published to the rugmunch.io (Ghost) feed
    best-effort (``publish_story_to_feed`` — fail-open when Ghost is gated).

    AI commentary: every posted story gets a one-line "why it matters" hook
    written by the snappy LLM tier (``attach_commentaries`` →
    ``generate_commentary`` via ``app.llm_config.chat_with_fallback``) —
    bounded per call, parallel per batch, fail-open to title-only when the
    LLM times out/empties. Applied to news posts (all formats) and the
    main-channel escalations.

    Additive side-effects (all fail-open):
    * freshly-persisted TG items are merged into the website full-feed Redis
      cache (``inject_into_news_cache``) so ``/api/v1/news/feed`` +
      ``/headlines`` surface them with attribution;
    * a lightweight market-awareness snapshot (top headlines + trending
      tokens) is published to ``rmi:news:market_awareness`` for the bot's
      chat context (``publish_market_awareness``);
    * the swept articles are clustered into stories and embedded into the
      RAG ``news_v2`` collection (``ingest_into_rag``) so RAG "benefits
      from" the news lane.
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from app.domains.telegram.rugmunchbot.channels.router import ROUTE_ALPHA_CATEGORY, route_news
    from app.domains.telegram.rugmunchbot.channels.scheduler import (
        _chat_id,
        _dedupe_key,
        _remember,
        _safe_send,
    )

    news_chat_id = _chat_id("news")
    alpha_chat_id = _chat_id("alpha")
    main_chat_id = _chat_id("main")
    if not news_chat_id and not alpha_chat_id and not main_chat_id:
        return 0

    articles = await _tg.sweep_telegram_news()
    if not articles:
        logger.debug("tg_news:no_fresh_articles")
        return 0

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
    news_scored: list[Any] = []
    alpha_scored: list[Any] = []
    main_scored: list[Any] = []
    for s in scored:
        dest = route_news(s.importance, s.article.category)
        if dest == "main":
            main_scored.append(s)
            if s.article.category != ROUTE_ALPHA_CATEGORY:
                news_scored.append(s)
        elif dest == "alpha":
            alpha_scored.append(s)
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
            if _remember("tgnews", _dedupe_key("tgnews", post)):
                # AI "why it matters" one-liner (bounded, fail-open) — the post
                # ships with just the headline when the LLM call fails/empties.
                _apply_commentaries([post], await _tg.attach_commentaries([post]))
                ok = await _post_formatted(ctx.bot, news_chat_id, format_breaking_post(post), _safe_send)
                if ok:
                    logger.info("tg_news:posted_breaking title=%s", top.title[:80])
                    posted += 1
        else:
            fresh = [
                scored_article.article
                for scored_article in selected
                if _remember("tgnews", _dedupe_key("tgnews", _article_to_post_dict(scored_article.article)))
            ]
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
                )
                if ok:
                    logger.info("tg_news:posted_news count=%s", min(len(fresh), TG_NEWS_POST_LIMIT))
                    posted += 1

    # Route: main (importance ≥ 0.7) → @cryptorugmuncher channel, solo.
    # The high-importance solo path: breaking markers keep the 🚨 shape,
    # otherwise the compelling ⭐ feature card (rewritten title + one-line
    # hook) — never a bare link; the story's image rides the NewsPost so
    # _post_formatted attaches it as a photo.
    if main_chat_id and main_scored:
        selected_main = select_important(main_scored, top_n=TG_NEWS_POST_LIMIT)
        selected_main = sorted(selected_main, key=lambda s: s.importance, reverse=True)
        if selected_main:
            top = selected_main[0].article
            post = _article_to_post_dict(top)
            if _remember("tgmain", _dedupe_key("tgmain", post)):
                # AI "why it matters" one-liner for the main-channel card
                # (bounded, fail-open — ships title-only when the LLM fails).
                _apply_commentaries([post], await _tg.attach_commentaries([post]))
                formatted = format_breaking_post(post) if _tg.is_breaking_article(top) else format_feature_post(post)
                ok = await _post_formatted(ctx.bot, main_chat_id, formatted, _safe_send)
                if ok:
                    logger.info("tg_news:posted_main title=%s", top.title[:80])
                    posted += 1
                    # rugmunch.io (Ghost) feed publish — best-effort + fail-open:
                    # the Telegram posts already went out; a missing Ghost API
                    # key / session cookie never blocks them (requirement: ≥0.7
                    # stories auto-post to news + main + ghost feed).
                    await _tg.publish_story_to_feed(str(formatted), title=top.post_title or top.title)

    # Route: alpha → alpha channel (additive; no alpha chat id → skip)
    if alpha_chat_id and alpha_scored:
        selected_alpha = select_important(alpha_scored, top_n=TG_NEWS_POST_LIMIT)
        fresh_alpha = [
            scored_article.article
            for scored_article in selected_alpha
            if _remember("tgalpha", _dedupe_key("tgalpha", _article_to_post_dict(scored_article.article)))
        ]
        if fresh_alpha:
            ok = await _post_formatted(
                ctx.bot,
                alpha_chat_id,
                format_news_post([_article_to_post_dict(a) for a in fresh_alpha[:TG_NEWS_POST_LIMIT]]),
                _safe_send,
            )
            if ok:
                logger.info("tg_news:posted_alpha count=%s", min(len(fresh_alpha), TG_NEWS_POST_LIMIT))
                posted += 1

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

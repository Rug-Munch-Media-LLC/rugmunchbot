"""Focused sweep + runner for the shill-watch channel.

Split out of ``channels/shill_watch.py`` during the de-godding pass. Sweeps
the focused KOL/alpha channel list, detects campaigns (backend-first, local
pure fallback), scores, captures evidence, and routes the alert. Fail-open
at every stage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.channels.shill_watch._config import (
    SHILL_MAX_ALERTS,
    SHILL_MIN_ACCOUNTS,
    SHILL_MIN_MENTIONS,
    SHILL_PER_CHANNEL_LIMIT,
    SHILL_SCREENSHOT_BUDGET,
    SHILL_SCREENSHOT_ENABLED,
    SHILL_SCREENSHOT_PER_CAMPAIGN,
    SHILL_SWEEP_HOURS,
    SHILL_USE_BACKEND,
    SHILL_WATCH_CHANNELS,
    SHILL_WINDOW_HOURS,
    TG_CAPTURE_BUDGET,
    TG_CAPTURE_ENABLED,
    logger,
)
from rugmunchbot.channels.shill_watch._detect import (
    ShillCampaign,
    _article_fields,
    detect_shill_campaigns,
)
from rugmunchbot.channels.shill_watch._evidence import (
    _capture_campaign_shills,
    capture_telegram_kol_posts,
)
from rugmunchbot.channels.shill_watch._scoring import (
    _looks_like_address,
    _score_shill_campaign,
    build_scanner_evidence,
    format_shill_alert,
)

# ── focused sweep + runner ────────────────────────────────────────────────


def _article_to_dict(article: Any) -> dict[str, Any]:
    """Map a NewsArticle/object to the backend ``ArticleIn`` shape (fail-open)."""
    title, channel, url, entities = _article_fields(article)
    body = ""
    if isinstance(article, dict):
        body = str(article.get("text") or article.get("body") or "")
    else:
        body = str(getattr(article, "body", "") or getattr(article, "text", "") or "")
    posted = getattr(article, "posted_at", None)
    if isinstance(article, dict):
        posted = article.get("posted_at", posted)
    return {
        "title": title,
        "text": body[:500],
        "source_channel": channel,
        "channel": channel,
        "url": url,
        "source_url": url,
        "entities": entities,
        "posted_at": str(posted) if posted else "",
    }


def _campaign_from_dict(data: dict[str, Any]) -> ShillCampaign:
    """Build a ``ShillCampaign`` from a backend ``/campaigns/detect`` item."""
    return ShillCampaign(
        token=str(data.get("token") or ""),
        chain=str(data.get("chain") or ""),
        accounts=[str(a) for a in (data.get("accounts") or [])],
        mentions=[dict(m) for m in (data.get("mentions") or [])],
        window_start=str(data.get("window_start") or ""),
        window_end=str(data.get("window_end") or ""),
        score=float(data.get("score") or 0.0),
        importance=float(data.get("importance") or 0.0),
    )


async def _detect_via_backend(articles: list[Any]) -> list[ShillCampaign] | None:
    """Post the sweep to the backend detector; return campaigns or None.

    None means the backend is unreachable / errored — the caller falls back to
    the local pure detector. Fail-open by contract.
    """
    try:
        from rugmunchbot.services import shill_api

        items = await shill_api.detect_campaigns(
            [_article_to_dict(a) for a in articles or []],
            window_hours=SHILL_WINDOW_HOURS,
            min_accounts=SHILL_MIN_ACCOUNTS,
            min_mentions=SHILL_MIN_MENTIONS,
        )
        if items:
            return [_campaign_from_dict(d) for d in items]
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_watch:backend_detect_failed err=%s", str(exc)[:120])
    return None


async def _sweep_shill_sources(
    client: Any | None = None, lookback_hours: int | None = None
) -> list[Any]:
    """Read recent messages from the focused KOL/calls channel list (bounded).

    Returns ``NewsArticle`` objects (fail-open ``[]``). Uses the same
    news_ingest read/normalize path as the main sweep but ONLY the
    ``SHILL_WATCH_CHANNELS`` handles — the full 200-channel discovery sweep
    is left to the tg_news job, so the shill watcher stays cheap.
    """
    from rugmunchbot._vendor.news_ingest.ingest import read_channel_messages
    from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article
    from rugmunchbot.channels.telegram_news._sources._fetch import (
        _build_headless_client,
        _inside_window,
    )

    if client is None:
        try:
            client = await _build_headless_client()
        except Exception as exc:  # fail-open by contract
            logger.warning("shill_watch:no_client err=%s", str(exc)[:120])
            return []
    if client is None:
        logger.warning("shill_watch:sweep_skipped_no_client")
        return []

    lookback = max(1, lookback_hours or SHILL_SWEEP_HOURS)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)
    articles: list[Any] = []
    for username in SHILL_WATCH_CHANNELS:
        try:
            messages = await read_channel_messages(client, username, SHILL_PER_CHANNEL_LIMIT)
        except Exception as exc:  # a dead channel never blocks the sweep
            logger.debug("shill_watch:channel_failed channel=%s err=%s", username, str(exc)[:80])
            continue
        for message in messages:
            try:
                if not _inside_window(message, cutoff):
                    continue
                article = channel_message_to_article(message, "alpha")
            except Exception as exc:  # malformed row — skip, never raise
                logger.debug("shill_watch:bad_row channel=%s err=%s", username, str(exc)[:80])
                continue
            if article is not None:
                articles.append(article)
    return articles


async def run_shill_watch(ctx: Any = None) -> int:
    """Sweep KOL channels → detect campaigns → score → capture → route alert.

    Returns the number of alerts posted (0..``SHILL_MAX_ALERTS``). Dedupe is
    per token per calendar day (global ``_remember`` namespace), so a
    campaign alerts once a day, never every tick. Fail-open at every stage.

    2026-08-30 (intelligent warning): each campaign is rerank-scored
    (``channels.importance`` via the qwen3-reranker lane) and routed by
    importance rather than posted unconditionally:

      * importance >= ``ROUTE_MAIN_MIN_IMPORTANCE`` (0.7) → @cryptorugmuncher
        AND @rmialerts (a severe, actively-promoted rug setup) — mirrored to
        the feed best-effort;
      * importance >= ``ROUTE_NEWS_MIN_IMPORTANCE`` (0.4) → @rmialerts only
        (danger intel, never the news channel);
      * below the news floor → held (no spam).

    Every detected campaign also triggers a cheap X screenshot capture
    (``tools.shill_shot`` — one DeepInfra VL-4B vision call per shill mention,
    bounded) and the screenshot reference + OCR text is persisted to the DB
    (``shill_evidence``) and the Qdrant ``threat_intel_v2`` collection with
    the source link, so the evidence survives a deleted tweet.
    """
    from rugmunchbot.channels.router import (
        ROUTE_MAIN_MIN_IMPORTANCE,
        ROUTE_NEWS_MIN_IMPORTANCE,
    )
    from rugmunchbot.channels.scheduler._core import _chat_id, _release, _remember, _safe_send

    alerts_chat = _chat_id("alerts")
    main_chat = _chat_id("main")
    if not alerts_chat and not main_chat:
        logger.debug("shill_watch:skipped_no_alert_or_main_chat")
        return 0

    articles = await _sweep_shill_sources()
    if not articles:
        logger.debug("shill_watch:no_fresh_articles")
        return 0

    campaigns: list[ShillCampaign] | None = None
    if SHILL_USE_BACKEND:
        campaigns = await _detect_via_backend(articles)
    if campaigns is None:
        # Offline / backend unreachable — the local pure detector is the
        # degraded rail (kept for startup safety + island mode).
        campaigns = detect_shill_campaigns(articles)
    if not campaigns:
        logger.info("shill_watch:no_campaigns articles=%s", len(articles))
        return 0

    # Score every campaign by rerank importance (fail-open → 0.0 → held).
    for campaign in campaigns[:SHILL_MAX_ALERTS]:
        await _score_shill_campaign(campaign)
    # Capture cheap X screenshots for evidence (one VL-4B vision call per shill).
    if SHILL_SCREENSHOT_ENABLED:
        remaining = max(0, SHILL_SCREENSHOT_BUDGET)
        for campaign in campaigns[:SHILL_MAX_ALERTS]:
            if remaining <= 0:
                break
            per_campaign = min(SHILL_SCREENSHOT_PER_CAMPAIGN, remaining)
            budget = min(per_campaign, len(campaign.mentions))
            captured = await _capture_campaign_shills(campaign, budget)
            remaining -= captured
    # Scanner evidence (2026-09-01): a campaign naming a real token address
    # is run through the canonical scanner so the alert carries concrete
    # on-chain "why this is likely a scam" findings (honeypot, deployer
    # history, bundled supply, LP risk). Fail-open → {} on any failure.
    for campaign in campaigns[:SHILL_MAX_ALERTS]:
        if not campaign.scanner_evidence and _looks_like_address(campaign.token):
            campaign.scanner_evidence = await build_scanner_evidence(campaign.token, campaign.chain)

    # 2026-09-01 Telegram KOL capture: monitored KOL channel/group posts are
    # captured through the same screenshot+OCR pipeline (public t.me/s/
    # preview) and persisted with source='telegram', independent of campaign
    # detection — so KOL posts are evidenced even when no campaign fires.
    # Deleted-post detection is NOT possible in this poll-based sweep — see
    # the TODO(deleted-posts) note above ``capture_telegram_kol_posts``.
    if TG_CAPTURE_ENABLED:
        await capture_telegram_kol_posts(articles, TG_CAPTURE_BUDGET)

    today = datetime.now(UTC).strftime("%Y%m%d")
    posted = 0
    for campaign in campaigns[:SHILL_MAX_ALERTS]:
        dedupe_key = f"shill:{campaign.key}:{today}"
        if not _remember("shill_watch", dedupe_key):
            continue
        if ctx is None:
            # No bot handle (manual/cron use) — release and skip.
            _release("shill_watch", dedupe_key)
            continue

        text = format_shill_alert(campaign)
        routed = False
        # Danger intel: never the news channel. Main only at/above the gate.
        if campaign.importance >= ROUTE_MAIN_MIN_IMPORTANCE and main_chat:
            routed = await _safe_send(ctx.bot, main_chat, text) or routed
            # W30 top-of-food-chain mirror: a shill campaign that escalates
            # to main also lands on feed.rugmunch.io (same content, same
            # order, source link preserved). Best-effort, never blocks.
            if routed:
                try:
                    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

                    _shill_ref = str(
                        (campaign.mentions[0].get("url") if campaign.mentions else "")
                        or campaign.token
                        or ""
                    )
                    mirror_post_to_feed(
                        {
                            "text": str(text),
                            "story_ref": _shill_ref,
                            "source": "shill_watch",
                            "kind": "shill_watch",
                        }
                    )
                except Exception as exc:
                    logger.warning("shill_feed_mirror_failed err=%s", str(exc)[:120])
        if campaign.importance >= ROUTE_NEWS_MIN_IMPORTANCE and alerts_chat:
            routed = await _safe_send(ctx.bot, alerts_chat, text) or routed
        if not routed:
            # Held below the floor (or every send failed) — release to retry.
            _release("shill_watch", dedupe_key)
            logger.info(
                "shill_watch:held token=%s importance=%s accounts=%s",
                campaign.token[:40],
                campaign.importance,
                len(campaign.accounts),
            )
            continue
        posted += 1
        logger.info(
            "shill_watch:alerted token=%s importance=%s accounts=%s mentions=%s shots=%s",
            campaign.token[:40],
            campaign.importance,
            len(campaign.accounts),
            len(campaign.mentions),
            len(campaign.screenshots),
        )
    logger.info(
        "shill_watch:run_done articles=%s campaigns=%s posted=%s",
        len(articles),
        len(campaigns),
        posted,
    )
    return posted

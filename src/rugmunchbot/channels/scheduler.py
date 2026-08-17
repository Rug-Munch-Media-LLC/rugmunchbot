"""Scheduled channel posters — JobQueue-based, env-configurable cadence.

Registered from ``bot.py`` next to the existing background jobs. Each job
reads its channel chat id + enabled flag at tick time (so env overrides apply
without a restart after ``config.reset_channel_cache()``), fetches live
content, formats it, and sends to the channel. All jobs fail open: missing
chat id, backend outage, or Telegram errors log and skip — the JobQueue keeps
running.

Cadence (env-overridable, defaults):
* news     → every 4h  (``RMI_CHANNEL_NEWS_INTERVAL_HOURS``) — accepts
            fractional hours, so ``0.5`` = every 30min for a ~2x/hour cadence
            when operators want the trending digest that fast. DEPRECATED
            2026-08-16: the legacy ``channel_news`` job is commented out —
            the routed tg_news path now owns the news channel (see below).
* alpha    → every 2h  (``RMI_CHANNEL_ALPHA_INTERVAL_HOURS``) — whale moves
            are collected on the same cadence so whale events surface in the
            digest within one interval (plus the existing event-driven rails).
            The digest is reranker-gated (only items ≥ the news floor post;
            heuristic fallback keeps the channel flowing during a reranker
            outage) and any item ≥ the main gate (0.7) ALSO posts solo to
            @cryptorugmuncher (``format_main_escalation``) — see
            :func:`_score_and_partition`.
* alerts   → every 30m (``RMI_CHANNEL_ALERTS_INTERVAL_MINUTES``) — the
            scanner risk digest is curated (alert type, condition met vs
            current, why-it-matters, scan/chart links), reranker-gated, and
            ≥0.7 items ALSO post solo to @cryptorugmuncher.
* scans    → every 90m (``RMI_CHANNEL_SCANS_INTERVAL_MINUTES``) — the
            @rmiscans "scans of note" job: the day's most notable scanned
            tokens (launch-alert ≥70 rail + scan history, deduped per
            token), reranker-gated (skip <0.4), each posted as its own
            compact card (real risk reasons + risk score + token + chain +
            scan link, plus a witty one-liner when the LLM is up)
            — ~10/day across the cadence (per-tick cap
            ``RMI_CHANNEL_SCANS_TICK_LIMIT``, default 2). Fail-open: no
            scans today → nothing posted.
* tg_news  → every 20m (``RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES``; default
            tuned UP from 30m so stories post "often") — ingests public
            crypto-news Telegram channels via the headless client, persists
            new articles to the website news table, reranks them for
            importance (``channels/importance.py``, Infinity
            BAAI/bge-reranker-v2-m3), then routes each scored story to EXACTLY
            ONE destination via ``channels/router.py::route_news`` (news →
            news channel; main → @cryptorugmuncher; alpha → alpha channel;
            below floor → skipped) — see ``channels/telegram_news.py``
 * scam_watch → every 1h (``RMI_SCAM_WATCH_INTERVAL_MINUTES``) — sweeps
 *             scam/shill channels via the headless client, extracts token
 *             addresses, and feeds them to the scanner (submit + durable
 *             queue) — see ``channels/scam_watch.py``
 * personal  → every 3h (``RMI_PERSONAL_CHANNEL_INTERVAL_MINUTES``, default
 *             180) — the operator's @cryptorugmuncher channel: the "bigger
 *             stories" above ``RMI_PERSONAL_CHANNEL_THRESHOLD`` (default 0.7,
 *             stricter than the news channel's 0.4), posted as operator-voice
 *             captions and cross-posted to the Ghost blog (Admin API) — see
 *             ``channels/personal_channel.py``

Master switch: ``RMI_CHANNELS_ENABLED`` (default on). Per-channel switches:
``RMI_NEWS_CHANNEL_ENABLED`` / ``RMI_ALERTS_CHANNEL_ENABLED`` /
``RMI_ALPHA_CHANNEL_ENABLED`` / ``RMI_TG_NEWS_CHANNEL_ENABLED`` /
``RMI_SCANS_CHANNEL_ENABLED`` /
``RMI_SCAM_WATCH_ENABLED`` (default on) / ``RMI_PERSONAL_CHANNEL_ENABLED``
(default OFF — opt-in; see ``channels/personal_channel.py``).

Durability / retry / observability (2026-08-14 hardening)
----------------------------------------------------------
* **Durability** — the in-memory ``_remember`` dedupe is mirrored to Redis
  (``channels/scheduler_state.py``): the posted-key sets (incl. the
  ``tgnews:`` prefix and the ``alpha:*`` / ``personal`` prefixes) and the
  per-job last-run timestamps are loaded once at startup and saved on every
  tick, so a bot restart resumes cleanly instead of re-posting. Each newly
  remembered key is also persisted fire-and-forget at post time, shrinking
  the crash window from a full interval to milliseconds. scam_watch's
  seen-set was already durable (Redis ``bot:scam_watch:seen``). Fail-open:
  when Redis is down the scheduler keeps its pre-existing in-memory
  behavior and re-probes periodically.
* **Retry** — :func:`_safe_send` retries a failed post up to
  ``RMI_POST_RETRIES`` (default 2) times with exponential backoff (0.5s,
  1s, 2s, … capped at 8s) before logging and dropping — never blocking the
  JobQueue loop.
* **Observability** — every tick emits one structured JSON line
  (``channels:tick``) with ``job`` / ``items_swept`` / ``items_scored`` /
  ``items_posted`` / ``items_failed`` / ``latency_ms`` (+ ``reason`` on
  skip paths). The news/alerts/alpha jobs report full counts; the jobs
  whose source modules live elsewhere (tg_news / scam_watch / personal)
  report the envelope via :func:`_tick_wrapper` — their swept/scored are 0
  there (their own modules already log detailed counts) and
  ``items_posted`` is the job's return value (digest posted for tg_news /
  personal; addresses fed to the scanner for scam_watch).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.news_ingest.models import NewsArticle
from app.domains.telegram.rugmunchbot.channels import scheduler_state
from app.domains.telegram.rugmunchbot.channels._media import send_news_photo
from app.domains.telegram.rugmunchbot.channels._scan_commentary import attach_scan_commentary
from app.domains.telegram.rugmunchbot.channels.content import (
    fetch_alpha_content,
    fetch_risk_alerts,
    fetch_scan_notes,
    fetch_trending_news,
)
from app.domains.telegram.rugmunchbot.channels.daily_market_summary import (
    DAILY_MARKET_SUMMARY_ENABLED,
    post_daily_market_summary,
    summary_run_time,
)
from app.domains.telegram.rugmunchbot.channels.formatters import (
    NewsPost,
    format_alerts_post,
    format_alpha_post,
    format_main_escalation,
    format_news_post,
    format_scan_card,
)
from app.domains.telegram.rugmunchbot.channels.importance import score_importance
from app.domains.telegram.rugmunchbot.channels.personal_channel import (
    PERSONAL_CHANNEL_ENABLED,
    PERSONAL_CHANNEL_INTERVAL_MINUTES,
    post_personal_channel,
    resolve_personal_chat_id,
)
from app.domains.telegram.rugmunchbot.channels.router import (
    ROUTE_ALPHA_CATEGORY,
    ROUTE_NEWS_MIN_IMPORTANCE,
    route_news,
)
from app.domains.telegram.rugmunchbot.channels.scam_watch import (
    SCAM_WATCH_ENABLED,
    SCAM_WATCH_INTERVAL_MINUTES,
    run_scam_watch,
)
from app.domains.telegram.rugmunchbot.channels.telegram_news import (
    TG_NEWS_ENABLED,
    post_tg_news_update,
)
from app.domains.telegram.rugmunchbot.config import (
    ALERTS_CHANNEL_ENABLED,
    ALERTS_CHANNEL_INTERVAL_MINUTES,
    ALERTS_CHANNEL_LIMIT,
    ALPHA_CHANNEL_ENABLED,
    ALPHA_CHANNEL_INTERVAL_HOURS,
    ALPHA_CHANNEL_LIMIT,
    CHANNEL_JOBS_ENABLED,
    NEWS_CHANNEL_LIMIT,
    SCAN_CHANNEL_ENABLED,
    SCAN_CHANNEL_INTERVAL_MINUTES,
    SCAN_CHANNEL_LIMIT,
    SCAN_CHANNEL_TICK_LIMIT,
    _resolve_channel_id,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.channels")

# First-run delays so the channels go live shortly after boot (not silent for
# a full interval): news 45s, alerts 75s, alpha 105s, tg_news 135s,
# scam_watch 165s, personal 195s, scans 225s.
_FIRST_RUN_SECONDS = {
    "news": 45,
    "alerts": 75,
    "alpha": 105,
    "tg_news": 135,
    "scam_watch": 165,
    "personal": 195,
    "scans": 225,
}

# Bounded in-memory dedupe: don't re-post the same item on the next tick.
_posted_keys: dict[str, set[str]] = {}
_MAX_POSTED = 500


# Post-retry policy. ``RMI_POST_RETRIES`` (default 2) is the number of
# retries AFTER the initial attempt; backoff doubles from 0.5s capped at 8s.
def _env_retry_count(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return default


_POST_RETRIES = _env_retry_count("RMI_POST_RETRIES", 2)
_RETRY_BASE_DELAY = 0.5
_RETRY_MAX_DELAY = 8.0


def _env_interval_minutes(name: str, default: int) -> int:
    """Positive integer minutes from env (``RMI_*_INTERVAL_MINUTES``)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


#: tg_news cadence — default tuned UP to 20 min (≈3 posts/hour) so stories
#: post "often". Env-overridable via ``RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES``
#: (the same knob ``telegram_news/_sources.py`` reads; we keep the tuned
#: default here at the registration site so the routed tg_news lane is the
#: operative cadence without touching the telegram_news package).
_TG_NEWS_INTERVAL_MINUTES = _env_interval_minutes("RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES", 20)


def _chat_id(channel: str) -> str | None:
    """Resolve a channel chat id (gopass → env → fallback), None when unknown."""
    return _resolve_channel_id(channel)


def _route_chat_id(destination: str) -> str | None:
    """Chat id for a ``route_news`` destination ("news"/"main"/"alpha").

    ``main`` resolves through the registry's ``main`` role → the operator's
    @cryptorugmuncher channel. Unknown destinations (e.g. ``"skip"``) return
    None so callers simply don't post.
    """
    if destination not in ("news", "main", "alpha"):
        return None
    return _chat_id(destination)


def _remember(channel: str, key: str) -> bool:
    """Return True when ``key`` is new for ``channel`` (and record it)."""
    seen = _posted_keys.setdefault(channel, set())
    if key in seen:
        return False
    seen.add(key)
    if len(seen) > _MAX_POSTED:
        _posted_keys[channel] = set(list(seen)[-_MAX_POSTED // 2 :])
    # Durability: best-effort background persist so a restart does not
    # re-post/re-ingest this key (fail-open when Redis is down).
    if scheduler_state.is_available():
        scheduler_state.spawn_persist(channel, key)
    return True


def _dedupe_key(prefix: str, item: dict[str, Any]) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return f"{prefix}:{url}"
    title = (item.get("title") or item.get("symbol") or "").strip()
    addr = (item.get("token_address") or item.get("address") or "").strip()
    return f"{prefix}:{addr or title}"


async def _safe_send(bot: Any, chat_id: str, text: str) -> bool:
    """Send one HTML message to a channel. Returns True on success, fails open.

    Retries failed sends up to ``_POST_RETRIES`` times with exponential
    backoff (0.5s, 1s, 2s, … capped at 8s) before logging and dropping. The
    sleeps only suspend this job's coroutine — the JobQueue loop keeps
    running other jobs. A poster never crashes the JobQueue.
    """
    max_attempts = 1 + _POST_RETRIES
    attempt = 0
    while True:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return True
        except TelegramError as exc:
            logger.warning("channels:send failed chat=%s err=%s", chat_id, exc)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            logger.warning("channels:send error chat=%s err=%s", chat_id, exc)
        except Exception as exc:  # a poster must never crash the JobQueue
            logger.warning("channels:send unexpected chat=%s err=%s", chat_id, exc)
        attempt += 1
        if attempt >= max_attempts:
            logger.warning(
                "channels:send_gave_up chat=%s retries=%s",
                chat_id,
                _POST_RETRIES,
            )
            return False
        delay = min(_RETRY_BASE_DELAY * (2 ** (attempt - 1)), _RETRY_MAX_DELAY)
        logger.warning(
            "channels:send_retry chat=%s attempt=%s/%s delay=%.1fs",
            chat_id,
            attempt,
            _POST_RETRIES,
            delay,
        )
        await asyncio.sleep(delay)


def _elapsed_ms(started: float) -> float:
    """Milliseconds elapsed since ``started`` (``time.perf_counter``)."""
    return round((time.perf_counter() - started) * 1000, 1)


# ── Reranker-gated channel routing (shared by the alerts/alpha jobs) ───────


def _item_to_article(item: dict[str, Any], *, category: str, source: str) -> NewsArticle:
    """Adapt a normalized channel item dict into a ``NewsArticle`` for reranking.

    The title is the token/signal name and the body folds in every scrap of
    text the cross-encoder can match an importance query against (verdict,
    summary/body, risk flags, name/symbol). ``source_url`` is the scanner
    deep link when the item is a token; otherwise a stable placeholder so the
    reranker never sees an empty URL. Pure — never raises.
    """
    name = str(item.get("name") or item.get("title") or item.get("symbol") or "Untitled").strip()
    title = str(item.get("title") or name)[:200]
    body_parts = [
        str(item.get("verdict") or "").strip(),
        str(item.get("summary") or item.get("body") or "").strip(),
        str(item.get("condition") or "").strip(),
        " ".join(str(f) for f in (item.get("risk_flags") or []) if f),
        str(item.get("name") or "").strip(),
        str(item.get("symbol") or "").strip(),
    ]
    body = " ".join(p for p in body_parts if p)
    addr = str(item.get("address") or item.get("token_address") or "").strip()
    url = str(item.get("scanner_url") or "").strip() or (
        f"https://rugmunch.io/scanner?token={addr}&chain={item.get('chain') or ''}" if addr else ""
    )
    return NewsArticle(
        title=title,
        body=body,
        source_channel=source,
        source_url=url or "https://rugmunch.io",
        category=category,
    )


async def _post_photo_aware(bot: Any, chat_id: str, post: NewsPost, safe_send: Any) -> bool:
    """Post a ``NewsPost``, attaching its image when one is available.

    Reuses the existing photo primitive (``channels._media.send_news_photo``)
    so digest and escalated posts get the same photo-or-text fail-open
    behavior as the tg_news lane. Without an image the caller's ``safe_send``
    (retry + ``disable_web_page_preview=True``) is used.
    """
    image_url = getattr(post, "image_url", "") or ""
    if image_url:
        return await send_news_photo(bot, chat_id, caption=post, photo_url=image_url)
    return await safe_send(bot, chat_id, post)


async def _score_and_partition(
    items: list[dict[str, Any]],
    *,
    category: str,
    source: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Rerank *items* and partition into (main_items, channel_items, reranker_down).

    Reuses ``channels/importance.py::score_importance`` (the same Infinity
    cross-encoder the tg_news lane uses) and ``channels/router.py::route_news``
    for the main-channel gate:

    * ``main_items`` — importance ≥ the main gate (0.7, ``route_news`` returns
      ``"main"``) → escalate solo to @cryptorugmuncher. Escalation is
      ADDITIVE: a ≥0.7 item also stays on its native channel digest.
    * ``channel_items`` — importance ≥ the news floor (0.4) → the native
      channel digest. Items below the floor are dropped (rerank mode only).
    * ``reranker_down`` — True when the reranker was unavailable and the
      heuristic fallback scored the batch; the native channel then keeps its
      full window (mirrors ``select_important``'s fail-open fallback) while
      the ≥0.7 main gate still applies.

    Every kept item gains an ``"importance"`` float. Pure routing — the caller
    still owns dedupe and posting.
    """
    articles = [_item_to_article(item, category=category, source=source) for item in items]
    scored = await score_importance(articles)
    main_items: list[dict[str, Any]] = []
    channel_items: list[dict[str, Any]] = []
    reranker_down = bool(scored and scored[0].fallback)
    for item, s in zip(items, scored, strict=True):
        item["importance"] = round(s.importance, 4)
        is_main = route_news(s.importance, category) == "main"
        if is_main:
            main_items.append(item)
        if is_main or reranker_down or s.importance >= ROUTE_NEWS_MIN_IMPORTANCE:
            channel_items.append(item)
    return main_items, channel_items, reranker_down


def _tick_wrapper(
    job_name: str,
    callback: Callable[[Any], Coroutine[Any, Any, Any]],
    *,
    envelope: bool = False,
) -> Callable[[Any], Coroutine[Any, Any, Any]]:
    """Wrap a JobQueue callback with durability + observability.

    Always records the last-run timestamp and persists scheduler state at
    the end of the tick (fail-open). When ``envelope=True`` (jobs whose
    source modules live outside this file) it also emits the ``channels:tick``
    JSON line with the fields the return value can provide; the in-file jobs
    (news/alerts/alpha) emit their own full-count tick lines instead.
    """

    async def wrapped(ctx: Any) -> Any:
        """Run one scheduled job tick: record start, invoke callback, persist state."""
        started = time.perf_counter()
        scheduler_state.mark_last_run(job_name)
        result: Any = None
        try:
            result = await callback(ctx)
        except Exception as exc:  # a poster must never crash the JobQueue
            logger.error(
                "channels:tick",
                job=job_name,
                items_swept=0,
                items_scored=0,
                items_posted=0,
                items_failed=1,
                latency_ms=_elapsed_ms(started),
                error=str(exc)[:200],
            )
        else:
            if envelope:
                logger.info(
                    "channels:tick",
                    job=job_name,
                    items_swept=0,
                    items_scored=0,
                    items_posted=int(result or 0),
                    items_failed=0,
                    latency_ms=_elapsed_ms(started),
                )
        finally:
            try:
                await scheduler_state.save_state(_posted_keys, job=job_name)
            except Exception:  # durability must never crash the job
                logger.debug("channels:tick_save_failed job=%s", job_name, exc_info=True)
        return result

    return wrapped


async def _load_channel_state(ctx: Any = None) -> None:
    """One-time startup job: hydrate dedupe + last-run from Redis (fail-open)."""
    await scheduler_state.load_state(_posted_keys)


# ── News job ──────────────────────────────────────────────────────────────


async def post_news_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """DEPRECATED 2026-08-16 — legacy 4h news digest (job ``channel_news``).

    Superseded by the routed tg_news path (``post_tg_news_update`` +
    ``channels/router.py::route_news``). The ``channel_news`` registration in
    :func:`register_channel_jobs` is commented out so the news channel is no
    longer double-posted by two jobs with different dedupe prefixes (``news:``
    here vs ``tgnews:`` in the tg_news lane). Kept importable for
    ``channels/__init__.py`` and direct-call tests; remove in a follow-up.

    Posts the news digest to the news channel. Returns items sent (0-1).
    """
    started = time.perf_counter()
    chat_id = _chat_id("news")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_news",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    items = await fetch_trending_news(NEWS_CHANNEL_LIMIT)
    fresh = [item for item in items if _remember("news", _dedupe_key("news", item))]
    if not fresh:
        logger.info(
            "channels:tick",
            job="channel_news",
            items_swept=len(items),
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    ok = await _safe_send(ctx.bot, chat_id, format_news_post(fresh))
    if ok:
        logger.info("channels:news posted %s headline(s)", len(fresh))
    logger.info(
        "channels:tick",
        job="channel_news",
        items_swept=len(items),
        items_scored=len(fresh),
        items_posted=len(fresh) if ok else 0,
        items_failed=0 if ok else 1,
        latency_ms=_elapsed_ms(started),
    )
    return 1 if ok else 0


# ── Alerts job ────────────────────────────────────────────────────────────


async def post_alert_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the curated scanner risk digest; escalate ≥0.7 items to the main channel.

    Returns the number of messages posted (0-1 native digest + 0..N main
    escalations). Every fresh alert is reranked (``channels/importance.py``)
    and routed through ``route_news`` (category "news"): below the news floor
    it is dropped, 0.4-0.7 it posts on the alerts channel (rich card: alert
    type, condition met vs current, why-it-matters, scan/chart links), and
    ≥0.7 it ALSO posts solo to @cryptorugmuncher. Fail-open: a reranker
    outage keeps the digest flowing (heuristic fallback) and never blocks
    the JobQueue.
    """
    started = time.perf_counter()
    chat_id = _chat_id("alerts")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_alerts",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    alerts = await fetch_risk_alerts(ALERTS_CHANNEL_LIMIT)
    fresh = [item for item in alerts if _remember("alerts", _dedupe_key("alerts", item))]
    if not fresh:
        logger.info(
            "channels:tick",
            job="channel_alerts",
            items_swept=len(alerts),
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    main_items, channel_items, reranker_down = await _score_and_partition(fresh, category="news", source="scanner")
    posted = 0
    if channel_items:
        ok = await _post_photo_aware(ctx.bot, chat_id, format_alerts_post(channel_items), _safe_send)
        if ok:
            posted += 1
    main_chat_id = _chat_id("main")
    if main_chat_id and main_items:
        for item in sorted(main_items, key=lambda i: i.get("importance", 0.0), reverse=True):
            if not _remember("alertsmain", _dedupe_key("alertsmain", item)):
                continue
            ok = await _post_photo_aware(ctx.bot, main_chat_id, format_main_escalation(item), _safe_send)
            if ok:
                posted += 1
    if posted:
        logger.info(
            "channels:alerts posted=%s main=%s",
            posted,
            len(main_items),
        )
    logger.info(
        "channels:tick",
        job="channel_alerts",
        items_swept=len(alerts),
        items_scored=len(fresh),
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted


# ── Scans job ────────────────────────────────────────────────────────────


async def post_scan_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the day's notable scans to @rmiscans, one compact card each.

    Returns the number of cards posted (0..``SCAN_CHANNEL_TICK_LIMIT``). The
    day's most notable scanned tokens (launch-alert ≥70 rail + scan history,
    ``channels/content.py::fetch_scan_notes``) are deduped per token
    (``_remember("scans", …)`` so the same token is never reposted), reranked
    (``channels/importance.py``), and every item at/above the news floor
    (``route_news``, default 0.4) posts as its own compact card — real risk
    reasons (honeypot / bundle / LP removed / flagged deployer) + risk score
    + token + chain + scan link (rugmunch.io/scanner), each card topped with
    a bounded witty one-liner (``attach_scan_commentary``, fail-open to no
    line). Fail-open: an empty pool (no scans today / backend down) posts
    nothing, and a reranker outage keeps the channel flowing with the
    heuristic fallback (full window) exactly like the alerts job.
    """
    started = time.perf_counter()
    chat_id = _chat_id("scans")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_scans",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    scans = await fetch_scan_notes(SCAN_CHANNEL_LIMIT)
    fresh = [item for item in scans if _remember("scans", _dedupe_key("scans", item))]
    if not fresh:
        logger.info(
            "channels:tick",
            job="channel_scans",
            items_swept=len(scans),
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    _, keep, reranker_down = await _score_and_partition(fresh, category="news", source="scanner")
    # Post at most SCAN_CHANNEL_TICK_LIMIT cards per tick: the reranker gate
    # (skip <0.4) + per-token dedupe are the volume controls; the tick cap
    # just spreads the day's notable scans across the day (~10/day at the
    # default 90m cadence).
    posted = 0
    for item in sorted(keep, key=lambda i: i.get("importance", 0.0), reverse=True)[:SCAN_CHANNEL_TICK_LIMIT]:
        # One bounded witty one-liner per card (fail-open to no line).
        item = await attach_scan_commentary(item)
        ok = await _post_photo_aware(ctx.bot, chat_id, format_scan_card(item), _safe_send)
        if ok:
            posted += 1
    logger.info(
        "channels:tick",
        job="channel_scans",
        items_swept=len(scans),
        items_scored=len(fresh),
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted


# ── Alpha job ─────────────────────────────────────────────────────────────


async def post_alpha_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the curated alpha digest (whale/smart/launch); escalate ≥0.7 to main.

    Returns the number of messages posted (0-1 native digest + 0..N main
    escalations). Whale/smart/launch items are deduped per section, reranked
    (``channels/importance.py``), and routed through ``route_news`` (category
    "alpha"): below the news floor they are dropped (rerank mode only), the
    rest form the alpha digest (verdict + links, photo when available), and
    items ≥0.7 ALSO post solo to @cryptorugmuncher. Fail-open: a reranker
    outage keeps the digest flowing (heuristic fallback) and never blocks
    the JobQueue.
    """
    started = time.perf_counter()
    chat_id = _chat_id("alpha")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_alpha",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    content = await fetch_alpha_content(
        chain="solana",
        whale_limit=max(1, ALPHA_CHANNEL_LIMIT),
        smart_limit=ALPHA_CHANNEL_LIMIT,
        launch_limit=ALPHA_CHANNEL_LIMIT,
    )
    whale_fresh = [m for m in content["whale_moves"] if _remember("alpha:whale", _dedupe_key("whale", m))]
    smart_fresh = [w for w in content["smart_money"] if _remember("alpha:smart", _dedupe_key("smart", w))]
    launch_fresh = [item for item in content["launches"] if _remember("alpha:launch", _dedupe_key("launch", item))]
    swept = len(content["whale_moves"]) + len(content["smart_money"]) + len(content["launches"])
    scored = len(whale_fresh) + len(smart_fresh) + len(launch_fresh)
    if not scored:
        logger.info(
            "channels:tick",
            job="channel_alpha",
            items_swept=swept,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    whale_main, whale_keep, whale_down = await _score_and_partition(
        whale_fresh, category=ROUTE_ALPHA_CATEGORY, source="WhaleWatcher"
    )
    smart_main, smart_keep, smart_down = await _score_and_partition(
        smart_fresh, category=ROUTE_ALPHA_CATEGORY, source="smart_money_feed"
    )
    launch_main, launch_keep, launch_down = await _score_and_partition(
        launch_fresh, category=ROUTE_ALPHA_CATEGORY, source="launch_feed"
    )
    reranker_down = whale_down or smart_down or launch_down
    posted = 0
    if whale_keep or smart_keep or launch_keep:
        ok = await _post_photo_aware(
            ctx.bot,
            chat_id,
            format_alpha_post(whale_keep, smart_keep, launch_keep),
            _safe_send,
        )
        if ok:
            posted += 1
    main_chat_id = _chat_id("main")
    if main_chat_id:
        escalated = sorted(
            [*whale_main, *smart_main, *launch_main],
            key=lambda i: i.get("importance", 0.0),
            reverse=True,
        )
        for item in escalated:
            if not _remember("alphamain", _dedupe_key("alphamain", item)):
                continue
            ok = await _post_photo_aware(ctx.bot, main_chat_id, format_main_escalation(item), _safe_send)
            if ok:
                posted += 1
    if posted:
        logger.info(
            "channels:alpha posted=%s whales=%s smart=%s launches=%s main=%s",
            posted,
            len(whale_keep),
            len(smart_keep),
            len(launch_keep),
            len(escalated) if main_chat_id else 0,
        )
    logger.info(
        "channels:tick",
        job="channel_alpha",
        items_swept=swept,
        items_scored=scored,
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted


# ── Registration ──────────────────────────────────────────────────────────


def register_channel_jobs(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the channel poster jobs on the PTB JobQueue (fail-open).

    Skips a channel when its job is disabled or its chat id is unset, so a
    half-configured deployment still gets the channels it can post to.
    """
    if not CHANNEL_JOBS_ENABLED:
        logger.info("channels:job scheduling disabled (RMI_CHANNELS_ENABLED=false)")
        return
    job_queue = app.job_queue
    if job_queue is None:
        logger.info("channels:no job queue — skipping channel jobs")
        return

    # Durability: hydrate the dedupe sets + last-run timestamps from Redis
    # once at startup (one-shot job) so a restart resumes cleanly. Guarded
    # for queues that predate PTB run_once (and test fakes).
    if hasattr(job_queue, "run_once"):
        job_queue.run_once(_load_channel_state, when=1.0, name="channel_state_load")

    # DEPRECATED 2026-08-16 — legacy 4h news digest job (``channel_news``).
    # Superseded by the routed tg_news path (``channel_tg_news`` below):
    # ``post_tg_news_update`` + ``channels/router.py::route_news`` now own the
    # news channel, so keeping BOTH jobs registered would double-post the same
    # story (the two jobs use different dedupe prefixes: ``news:`` here vs
    # ``tgnews:``/``tgmain:``/``tgalpha:`` in the tg_news lane). Commented out;
    # remove the ``post_news_update`` function in a follow-up.
    # if NEWS_CHANNEL_ENABLED and _chat_id("news"):
    #     job_queue.run_repeating(
    #         _tick_wrapper("channel_news", post_news_update),
    #         interval=NEWS_CHANNEL_INTERVAL_HOURS * 3600,
    #         first=_FIRST_RUN_SECONDS["news"],
    #         name="channel_news",
    #     )
    if ALERTS_CHANNEL_ENABLED and _chat_id("alerts"):
        job_queue.run_repeating(
            _tick_wrapper("channel_alerts", post_alert_update),
            interval=ALERTS_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["alerts"],
            name="channel_alerts",
        )
    if ALPHA_CHANNEL_ENABLED and _chat_id("alpha"):
        job_queue.run_repeating(
            _tick_wrapper("channel_alpha", post_alpha_update),
            interval=ALPHA_CHANNEL_INTERVAL_HOURS * 3600,
            first=_FIRST_RUN_SECONDS["alpha"],
            name="channel_alpha",
        )
    if SCAN_CHANNEL_ENABLED and _chat_id("scans"):
        job_queue.run_repeating(
            _tick_wrapper("channel_scans", post_scan_update),
            interval=SCAN_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["scans"],
            name="channel_scans",
        )
    # Routed tg_news job: registered when ANY routed destination resolves
    # (news / main @cryptorugmuncher / alpha) — the post path routes each
    # scored story to exactly one destination via ``route_news``.
    if TG_NEWS_ENABLED and any(_chat_id(dest) for dest in ("news", "main", "alpha")):
        job_queue.run_repeating(
            _tick_wrapper("channel_tg_news", post_tg_news_update, envelope=True),
            interval=_TG_NEWS_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["tg_news"],
            name="channel_tg_news",
        )
    if SCAM_WATCH_ENABLED:
        # Feeds the scanner (no bot chat id needed — nothing is posted).
        job_queue.run_repeating(
            _tick_wrapper("channel_scam_watch", run_scam_watch, envelope=True),
            interval=SCAM_WATCH_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["scam_watch"],
            name="channel_scam_watch",
        )
    if PERSONAL_CHANNEL_ENABLED and resolve_personal_chat_id():
        job_queue.run_repeating(
            _tick_wrapper("channel_personal", post_personal_channel, envelope=True),
            interval=PERSONAL_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["personal"],
            name="channel_personal",
        )
    # Daily Market Summary — 7:00 AM America/New_York, 7 days/week, on the
    # main channel (@cryptorugmuncher). Writes an editorial market breakdown
    # via ``chat_with_fallback(messages, "long")`` and posts it to the main
    # channel + the rugmunch.io feed (both fail-open) — see
    # ``channels/daily_market_summary.py``. Env knobs:
    # ``RMI_DAILY_MARKET_SUMMARY_ENABLED`` / ``_TIME`` / ``_TZ``.
    if DAILY_MARKET_SUMMARY_ENABLED and _chat_id("main"):
        job_queue.run_daily(
            _tick_wrapper("daily_market_summary", post_daily_market_summary, envelope=True),
            time=summary_run_time(),
            days=tuple(range(7)),
            name="daily_market_summary",
        )
    logger.info(
        "channels:channel jobs registered (news/alerts/alpha/scans/tg_news/scam_watch/personal/daily_market_summary)"
    )


__all__ = [
    "post_alert_update",
    "post_alpha_update",
    "post_news_update",
    "post_scan_update",
    "register_channel_jobs",
    "route_news",
    "run_scam_watch",
]

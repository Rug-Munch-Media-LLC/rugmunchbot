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
            when operators want the trending digest that fast
* alpha    → every 2h  (``RMI_CHANNEL_ALPHA_INTERVAL_HOURS``) — whale moves
            are collected on the same cadence so whale events surface in the
            digest within one interval (plus the existing event-driven rails)
* alerts   → every 30m (``RMI_CHANNEL_ALERTS_INTERVAL_MINUTES``)
* tg_news  → every 30m (``RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES``) — ingests
            public crypto-news Telegram channels via the headless client,
            persists new articles to the website news table, reranks them
            for importance (``channels/importance.py``, Infinity
            BAAI/bge-reranker-v2-m3), then posts only the important digest
            (see ``channels/telegram_news.py``)
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

from rugmunchbot.channels import scheduler_state
from rugmunchbot.channels.content import (
    fetch_alpha_content,
    fetch_risk_alerts,
    fetch_trending_news,
)
from rugmunchbot.channels.formatters import (
    format_alerts_post,
    format_alpha_post,
    format_news_post,
)
from rugmunchbot.channels.personal_channel import (
    PERSONAL_CHANNEL_ENABLED,
    PERSONAL_CHANNEL_INTERVAL_MINUTES,
    post_personal_channel,
    resolve_personal_chat_id,
)
from rugmunchbot.channels.scam_watch import (
    SCAM_WATCH_ENABLED,
    SCAM_WATCH_INTERVAL_MINUTES,
    run_scam_watch,
)
from rugmunchbot.channels.telegram_news import (
    TG_NEWS_ENABLED,
    TG_NEWS_INTERVAL_MINUTES,
    post_tg_news_update,
)
from rugmunchbot.config import (
    ALERTS_CHANNEL_ENABLED,
    ALERTS_CHANNEL_INTERVAL_MINUTES,
    ALERTS_CHANNEL_LIMIT,
    ALPHA_CHANNEL_ENABLED,
    ALPHA_CHANNEL_INTERVAL_HOURS,
    ALPHA_CHANNEL_LIMIT,
    CHANNEL_JOBS_ENABLED,
    NEWS_CHANNEL_ENABLED,
    NEWS_CHANNEL_INTERVAL_HOURS,
    NEWS_CHANNEL_LIMIT,
    _resolve_channel_id,
)
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.channels")

# First-run delays so the channels go live shortly after boot (not silent for
# a full interval): news 45s, alerts 75s, alpha 105s, tg_news 135s,
# scam_watch 165s, personal 195s.
_FIRST_RUN_SECONDS = {
    "news": 45,
    "alerts": 75,
    "alpha": 105,
    "tg_news": 135,
    "scam_watch": 165,
    "personal": 195,
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


def _chat_id(channel: str) -> str | None:
    """Resolve a channel chat id (gopass → env → fallback), None when unknown."""
    return _resolve_channel_id(channel)


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
    """Post the news digest to the news channel. Returns items sent (0-1)."""
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
    """Post the scanner risk digest to the alerts channel. Returns 0-1."""
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
    ok = await _safe_send(ctx.bot, chat_id, format_alerts_post(fresh))
    if ok:
        logger.info("channels:alerts posted %s risk alert(s)", len(fresh))
    logger.info(
        "channels:tick",
        job="channel_alerts",
        items_swept=len(alerts),
        items_scored=len(fresh),
        items_posted=len(fresh) if ok else 0,
        items_failed=0 if ok else 1,
        latency_ms=_elapsed_ms(started),
    )
    return 1 if ok else 0


# ── Alpha job ─────────────────────────────────────────────────────────────


async def post_alpha_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the alpha digest (whale moves + smart money + launches). Returns 0-1."""
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
    whale_fresh = [
        m for m in content["whale_moves"] if _remember("alpha:whale", _dedupe_key("whale", m))
    ]
    smart_fresh = [
        w for w in content["smart_money"] if _remember("alpha:smart", _dedupe_key("smart", w))
    ]
    launch_fresh = [
        item
        for item in content["launches"]
        if _remember("alpha:launch", _dedupe_key("launch", item))
    ]
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
    ok = await _safe_send(
        ctx.bot,
        chat_id,
        format_alpha_post(whale_fresh, smart_fresh, launch_fresh),
    )
    if ok:
        logger.info(
            "channels:alpha posted whales=%s smart=%s launches=%s",
            len(whale_fresh),
            len(smart_fresh),
            len(launch_fresh),
        )
    logger.info(
        "channels:tick",
        job="channel_alpha",
        items_swept=swept,
        items_scored=scored,
        items_posted=scored if ok else 0,
        items_failed=0 if ok else 1,
        latency_ms=_elapsed_ms(started),
    )
    return 1 if ok else 0


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

    if NEWS_CHANNEL_ENABLED and _chat_id("news"):
        job_queue.run_repeating(
            _tick_wrapper("channel_news", post_news_update),
            interval=NEWS_CHANNEL_INTERVAL_HOURS * 3600,
            first=_FIRST_RUN_SECONDS["news"],
            name="channel_news",
        )
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
    if TG_NEWS_ENABLED and _chat_id("news"):
        job_queue.run_repeating(
            _tick_wrapper("channel_tg_news", post_tg_news_update, envelope=True),
            interval=TG_NEWS_INTERVAL_MINUTES * 60,
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
    logger.info("channels:channel jobs registered (news/alerts/alpha/tg_news/scam_watch/personal)")


__all__ = [
    "post_alert_update",
    "post_alpha_update",
    "post_news_update",
    "register_channel_jobs",
    "run_scam_watch",
]

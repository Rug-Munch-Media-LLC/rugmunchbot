"""Channel-posting infrastructure — retries, dedupe, safe send, partitioning."""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from rugmunchbot._vendor.news_ingest.models import NewsArticle
from rugmunchbot.channels import scheduler_state
from rugmunchbot.channels._media import send_news_photo
from rugmunchbot.channels.formatters._format_law import enforce_format_law
from rugmunchbot.channels.importance import score_importance
from rugmunchbot.channels.router import (
    ROUTE_NEWS_MIN_IMPORTANCE,
    route_news,
)
from rugmunchbot.config import (
    _resolve_channel_id,
)
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes  # noqa: F401

    from rugmunchbot.channels.formatters import NewsPost

logger = get_logger("rmi_bot.channels")


_FIRST_RUN_SECONDS = {
    "news": 45,
    "alerts": 75,
    "alpha": 105,
    "tg_news": 135,
    "scam_watch": 165,
    "personal": 195,
    "scans": 225,
}


_posted_keys: dict[str, set[str]] = {}


_MAX_POSTED = 500


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
    """Return True when ``key`` is new (and record it).

    2026-08-24 P0 fix: dedupe is now GLOBAL across channels. The old
    per-channel sets let the same story post once per lane (tgnews/tgmain/
    tgalpha each had their own namespace), and restarts amplified it.
    Story identity = the key itself (URL or title) -- lane prefixes are
    stripped so a CoinJournal story can never post twice anywhere.
    """
    # Global story namespace: strip any legacy lane prefix from the key.
    bare = key.split(":", 1)[-1] if ":" in key else key
    seen = _posted_keys.setdefault("__global__", set())
    if bare in seen:
        return False
    seen.add(bare)
    if len(seen) > _MAX_POSTED:
        _posted_keys["__global__"] = set(list(seen)[-_MAX_POSTED // 2 :])
    # Durability: best-effort background persist so a restart does not
    # re-post/re-ingest this key (fail-open when Redis is down).
    if scheduler_state.is_available():
        scheduler_state.spawn_persist(channel, key)
    return True


def _release(channel: str, key: str) -> None:
    """Undo one ``_remember`` mark (send failed — let the next tick retry).

    2026-08-25 companion to the rag-posting ordering fix: a draft whose send
    failed must not stay in the global seen-set or it is black-holed forever.
    In-memory only; the durable Redis copy (if it landed) expires with the
    set TTL and only widens the restart window by one interval.
    """
    bare = key.split(":", 1)[-1] if ":" in key else key
    seen = _posted_keys.get("__global__")
    if seen is not None:
        seen.discard(bare)


def _dedupe_key(prefix: str, item: dict[str, Any]) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return f"{prefix}:{url}"
    title = (item.get("title") or item.get("symbol") or "").strip()
    addr = (item.get("token_address") or item.get("address") or "").strip()
    return f"{prefix}:{addr or title}"


async def _safe_send(bot: Any, chat_id: str, text: str) -> bool:
    """Send one HTML message to a channel. Returns True on success, fails open.

    WO-3 (2026-08-25): EVERY post passes through ``enforce_format_law``
    immediately before send — this is the single chokepoint all builders
    share, so no channel can bypass the universal format law (no walls of
    text, no repeated blocks, no emoji spam, no >80-char headlines, no
    mid-text truncation artifacts). Fail-open: a formatter bug returns the
    original text unchanged.

    Retries failed sends up to ``_POST_RETRIES`` times with exponential
    backoff (0.5s, 1s, 2s, … capped at 8s) before logging and dropping. The
    sleeps only suspend this job's coroutine — the JobQueue loop keeps
    running other jobs. A poster never crashes the JobQueue.
    """
    text = enforce_format_law(text)
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

    Reuses ``channels/importance.py::score_importance`` (the same qwen3
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

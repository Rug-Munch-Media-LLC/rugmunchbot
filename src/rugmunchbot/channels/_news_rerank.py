"""Shared reranker-driven story selection for the branded daily posts.

Both flagship posts ("The Morning Rug" 07:00 ET and "Midday Market Update"
12:30 ET) must lead with the HIGHEST-RANKED stories from the rugbrain
consumer reranker (``POST /api/v1/rag/news-rerank``) over the timeframe
between the two posts — not "everything in the last 24h".

Fail-open by contract: any failure returns ``[]`` and the caller keeps its
existing selection pipeline. No new deps — httpx + the shared RAG api
credentials (``RAG_API_URL`` / ``RMI_RAG_API_KEY``) already used by
``tools.rag_lookup``.

Split: the window math lives in ``_news_window``, the row/prompt shapes in
``_news_items``; this module is the transport lane only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rugmunchbot.channels._news_items import (
    TOP_K,
    normalise_article,
    parse_published_at,
    to_prompt_block,
)
from rugmunchbot.channels._news_window import (
    MAX_WINDOW_HOURS,
    MORNING_JOB_NAMES,
    MIDDAY_JOB_NAMES,
    midday_window_hours,
    morning_window_hours,
    window_hours_since,
)

logger = logging.getLogger("rmi_bot.channels.news_rerank")

#: Fallback window (hours) when the counterpart job has never run.
DEFAULT_WINDOW_HOURS = 12.0

# ── Re-exports: every caller and every test imports these from this module. ──
_parse_published_at = parse_published_at


def _rag_api_url() -> str:
    """RugBrain rag-api base URL — delegates to ``tools.rag_lookup``.

    Single canonical resolver: the reranker lane must resolve credentials
    exactly like every other RAG lane, so a context that does not source the
    bot env file (a systemd unit with no ``RMI_RAG_API_KEY``, a cron one-shot)
    degrades identically instead of silently skipping.
    """
    from rugmunchbot.tools.rag_lookup import _rag_api_url as _url

    return _url()


def _rag_api_key() -> str:
    """RugBrain X-API-Key — delegates to ``tools.rag_lookup._rag_api_key``.

    The local env-only read (``os.getenv("RMI_RAG_API_KEY", "")``) had no
    secret-file fallback, so the running unit (``Environment=`` carries only
    ``RAG_API_URL``) hit the ``no_api_config`` guard on every run and the
    Daily Rug published with zero ranked stories. Import inside the function:
    ``rag_lookup`` reaches back into ``channels`` — a module-level import here
    closes the cycle.
    """
    from rugmunchbot.tools.rag_lookup import _rag_api_key as _key

    return _key()


def _in_window(items: list[dict[str, Any]], *, window_hours: float) -> list[dict[str, Any]]:
    """Split into dated-in-window plus undated; drop only provably-stale rows.

    Undated items are kept and ranked after dated ones, mirroring the
    ``daily_market_summary._rerank.enforce_freshness`` doctrine.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    in_window: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dt = parse_published_at(item.get("published_at"))
        if dt is None:
            undated.append({**item, "_undated": True})
        elif dt >= cutoff:
            in_window.append(item)
        # provably stale → dropped (the whole point of the window)
    return in_window + undated


async def fetch_top_ranked_stories(
    *,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    limit: int = TOP_K,
    query: str | None = None,
    timeout_s: float = 15.0,
) -> list[dict[str, Any]]:
    """Top-K ranked stories within the timeframe window.

    2026-10-17: ``/rag/news-rerank`` does not exist on the live RugBrain
    (404 — it was never implemented), so this lane returned ``[]`` on every
    run and the Daily Rug had no ranked stories. ``/api/v1/news/top`` is the
    real endpoint: GET, already ranked, same row shape.

    Returns ``[]`` on any failure — never raises.
    """
    api_url = _rag_api_url()
    key = _rag_api_key()
    if not api_url or not key:
        logger.debug("news_rerank:no_api_config skipped")
        return []
    url = f"{api_url}/news/top"
    params = {"limit": min(50, max(limit * 3, 20))}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.get(url, headers={"X-API-Key": key}, params=params)
        if resp.status_code != 200:
            logger.warning("news_rerank:http_error status=%s", resp.status_code)
            return []
        raw_items = resp.json().get("articles") or []
        items = [normalise_article(a) for a in raw_items if isinstance(a, dict)]
    except Exception as exc:
        logger.warning("news_rerank:failed fail_open err=%s", str(exc)[:140])
        return []

    picked = _in_window(items, window_hours=window_hours)[:limit]
    logger.info(
        "news_rerank:selected window_h=%.1f candidates=%d picked=%d",
        window_hours,
        len(items),
        len(picked),
    )
    return picked


__all__ = [
    "DEFAULT_WINDOW_HOURS",
    "MAX_WINDOW_HOURS",
    "MIDDAY_JOB_NAMES",
    "MORNING_JOB_NAMES",
    "TOP_K",
    "_parse_published_at",
    "fetch_top_ranked_stories",
    "midday_window_hours",
    "morning_window_hours",
    "to_prompt_block",
    "window_hours_since",
]

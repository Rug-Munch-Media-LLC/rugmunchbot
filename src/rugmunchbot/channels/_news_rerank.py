"""Shared reranker-driven story selection for the branded daily posts.

Both flagship posts ("The Morning Rug" 07:00 ET and "Midday Market Update"
12:30 ET) must lead with the HIGHEST-RANKED stories from the rugbrain
consumer reranker (``POST /api/v1/rag/news-rerank``) over the timeframe
between the two posts — not "everything in the last 24h":

* Morning Rug window  = since the last Midday Update (fallback 12h).
* Midday Update window = since the last Morning Rug (fallback 12h).

The window is derived from the durable scheduler state
(``channels.scheduler_state`` last-run hash, Redis-backed, survives
restarts); a missing/never-run counterpart falls back to 12h.

Fail-open by contract: any failure returns ``[]`` and the caller keeps its
existing selection pipeline. No new deps — httpx + the shared RAG api
credentials (``RAG_API_URL`` / ``RMI_RAG_API_KEY``) already used by
``tools.rag_lookup``.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger("rmi_bot.channels.news_rerank")

#: rugbrain rag-api base (same lane as ``rag_lookup._rag_api_url``).
_DEFAULT_RAG_API_URL = "http://127.0.0.1:8012/api/v1"

#: Fallback window (hours) when the counterpart job has never run.
DEFAULT_WINDOW_HOURS = 12.0

#: Hard cap — a restart gap must never pull a multi-day window.
MAX_WINDOW_HOURS = 48.0

#: Ranked stories requested per post (top-K of the reranker).
TOP_K = 8

#: Query for the reranker — market-moving + scam/shill intel per the post
#: doctrine (stories making waves, scam coins, shilled tokens).
_MORNING_QUERY = (
    "most market-moving crypto stories: regulation, ETF flows, hacks, "
    "exploits, depegs, protocol launches, whale moves, scam coins to watch, "
    "tokens being shilled by KOLs"
)
_MIDDAY_QUERY = (
    "crypto stories making waves right now: rug pulls, scam coins, honeypots, "
    "shill campaigns and which KOLs are pushing them, exploits, whale moves, "
    "breaking market news"
)

#: Scheduler job names (new + legacy) used for window computation.
MORNING_JOB_NAMES = ("channel_morning_rug", "daily_market_summary")
MIDDAY_JOB_NAMES = (
    "channel_midday_update",
    "channel_daily_rug_update",
    "daily_market_summary_midday",
)


def _rag_api_url() -> str:
    import os

    return os.getenv("RAG_API_URL", _DEFAULT_RAG_API_URL).rstrip("/")


def _rag_api_key() -> str:
    import os

    raw = os.getenv("RMI_RAG_API_KEY", "")
    # Rotation split (``new,old``) — send only the active key.
    parts = [k.strip() for k in raw.split(",") if k.strip()]
    return parts[0] if parts else ""


def _parse_published_at(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        if isinstance(raw, (int, float)):
            ts = float(raw)
            if ts > 1e12:  # ms epoch
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=UTC)
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError, OSError):
        return None


def window_hours_since(
    job_names: tuple[str, ...], fallback_hours: float = DEFAULT_WINDOW_HOURS
) -> float:
    """Hours since the counterpart job last ran (clamped, fail-open).

    Reads the durable scheduler state; unknown/never-run jobs fall back to
    ``fallback_hours``. The result is clamped to
    ``[0.5, MAX_WINDOW_HOURS]`` so a clock gap can never produce an empty
    or multi-day window.
    """
    try:
        from rugmunchbot.channels.scheduler_state import get_state

        state = get_state()
        for name in job_names:
            last = state.last_run(name)
            if last is not None:
                hours = (datetime.now(UTC) - last).total_seconds() / 3600.0
                if hours <= 0:
                    return fallback_hours
                return max(0.5, min(MAX_WINDOW_HOURS, hours))
    except Exception as exc:
        logger.debug("news_rerank:window_lookup_failed err=%s", str(exc)[:120])
    return fallback_hours


async def fetch_top_ranked_stories(
    *,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    limit: int = TOP_K,
    query: str | None = None,
    timeout_s: float = 15.0,
) -> list[dict[str, Any]]:
    """Top-K reranker-ranked stories within the timeframe window.

    Calls rugbrain ``POST /api/v1/rag/news-rerank`` (hybrid news_v2 search +
    one rerank pass), then filters to stories published inside the window.
    Undated items are kept (flagged ``_undated``) and ranked after dated
    ones — mirrors ``daily_market_summary._rerank.enforce_freshness``
    doctrine. Returns ``[]`` on any failure — never raises.
    """
    api_url = _rag_api_url()
    key = _rag_api_key()
    if not api_url or not key:
        logger.debug("news_rerank:no_api_config skipped")
        return []
    url = f"{api_url}/rag/news-rerank"
    headers = {"Content-Type": "application/json", "X-API-Key": key}
    payload = {"query": query or _MORNING_QUERY, "limit": min(50, max(limit * 3, 20))}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning("news_rerank:http_error status=%s", resp.status_code)
            return []
        items = resp.json().get("items") or []
    except Exception as exc:
        logger.warning("news_rerank:failed fail_open err=%s", str(exc)[:140])
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    in_window: list[dict[str, Any]] = []
    undated: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dt = _parse_published_at(item.get("published_at"))
        if dt is None:
            undated.append({**item, "_undated": True})
        elif dt >= cutoff:
            in_window.append(item)
        # provably stale → dropped (the whole point of the window)
    picked = (in_window + undated)[:limit]
    logger.info(
        "news_rerank:selected window_h=%.1f candidates=%d picked=%d",
        window_hours,
        len(items),
        len(picked),
    )
    return picked


def morning_window_hours() -> float:
    """Morning Rug timeframe: since the last Midday Update (fallback 12h)."""
    return window_hours_since(MIDDAY_JOB_NAMES)


def midday_window_hours() -> float:
    """Midday Update timeframe: since the last Morning Rug (fallback 12h)."""
    return window_hours_since(MORNING_JOB_NAMES)


def to_prompt_block(items: list[dict[str, Any]], *, label: str) -> str:
    """Serialize ranked items into the cite-or-omit prompt block format."""
    lines: list[str] = []
    for it in items[:TOP_K]:
        title = str(it.get("title") or "").strip()
        summary = str(it.get("summary") or "").strip()[:220]
        src = str(it.get("source") or "").strip()
        url = str(it.get("url") or "").strip()
        cat = str(it.get("category") or "").strip()
        bits = [title or summary[:80]]
        if cat:
            bits.append(f"category {cat}")
        if src:
            bits.append(f"source {src}")
        if summary and title:
            bits.append(summary)
        line = " — ".join(b for b in bits if b)
        if url.startswith(("http://", "https://")):
            line += f" [url={url}]"
        if line.strip():
            lines.append(line)
    if not lines:
        return ""
    return f"{label}: " + " || ".join(lines)

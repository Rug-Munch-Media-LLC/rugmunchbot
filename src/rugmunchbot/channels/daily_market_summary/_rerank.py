"""Story selection for the market report — freshness gate + 4B reranker.

Doctrine: only market-moving, verifiably-fresh stories make the brief.
Selection pipeline: freshness filter -> deep reranker (:8003) with a
market-impact instruction -> top-N. Every stage fails open so the summary
never blocks, but failures are logged loudly.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RERANK_URL = os.getenv("DAILY_RUG_RERANK_URL", "http://127.0.0.1:8003")
MAX_AGE_HOURS = float(os.getenv("DAILY_RUG_MAX_STORY_AGE_H", "48"))

# Vertical instruction per rugbrain.search.rerank doctrine ([TASK]...[QUERY]...)
_INSTRUCTION = (
    "[TASK] Rank crypto news by market-moving significance for today's session: "
    "regulatory decisions and enforcement, large-cap repricings and ETF flows, "
    "hacks exploits and depegs, major protocol launches or outages, macro "
    "catalysts (rates, CPI, Fed), and whale/treasury moves. Strongly penalize "
    "evergreen explainers, education-course marketing, event invitations, "
    "affiliate promos, and recycled week-old coverage. [QUERY]"
)

_DATE_FIELDS = ("published_at", "published", "created_at", "date", "datetime", "timestamp")


def _parse_date(item: dict[str, Any]) -> datetime | None:
    for f in _DATE_FIELDS:
        raw = item.get(f)
        if not raw:
            continue
        try:
            if isinstance(raw, (int, float)):
                ts = float(raw)
                if ts > 1e12:  # ms epoch
                    ts /= 1000.0
                return datetime.fromtimestamp(ts, tz=UTC)
            s = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except (ValueError, TypeError, OSError):
            continue
    return None


def enforce_freshness(
    stories: list[dict[str, Any]], max_age_h: float = MAX_AGE_HOURS
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split stories into (fresh, rejected_reasons).

    2026-08-26 fix: undated stories are no longer auto-rejected — the
    morning run rejected EVERY news item ("no_date") leaving the brief
    with an empty news section. Undated items now pass with a
    ``_undated`` flag (ranked below dated fresh items by the caller);
    only provably STALE items are rejected. Accuracy still enforced via
    the reranker + cite-or-omit prompt rule."""
    cutoff = datetime.now(tz=UTC) - timedelta(hours=max_age_h)
    fresh: list[dict[str, Any]] = []
    rejected: list[str] = []
    for s in stories:
        dt = _parse_date(s)
        if dt is None:
            # keep it — flag for lower rank instead of rejecting outright
            s = dict(s)
            s["_undated"] = True
            fresh.append(s)
            continue
        if dt < cutoff:
            age_h = (datetime.now(tz=UTC) - dt).total_seconds() / 3600
            rejected.append(f"stale({age_h:.0f}h): {str(s.get('title') or '?')[:70]}")
            continue
        fresh.append(s)
    for r in rejected:
        logger.warning("market_report:freshness_rejected %s", r)
    return fresh, rejected


def _doc_text(s: dict[str, Any]) -> str:
    parts = [s.get("title") or "", s.get("summary") or s.get("text") or s.get("content") or ""]
    return " ".join(str(p) for p in parts if p)[:600]


async def rerank_stories(
    stories: list[dict[str, Any]], top_n: int = 6, timeout_s: float = 25.0
) -> tuple[list[dict[str, Any]], bool]:
    """Rank stories via the deep 4B reranker. Fail-open to input order."""
    if not stories:
        return stories, False
    docs = [_doc_text(s) for s in stories]
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(
                f"{RERANK_URL}/rerank",
                json={
                    "query": f"{_INSTRUCTION} most market-moving crypto events today",
                    "documents": docs,
                },
            )
            if r.status_code != 200:
                raise RuntimeError(f"reranker {r.status_code}")
            results = r.json().get("results", [])
        ordered = sorted(results, key=lambda x: x.get("relevance_score", 0.0), reverse=True)
        picked = [
            stories[item["index"]] for item in ordered[:top_n] if item["index"] < len(stories)
        ]
        logger.info(
            "market_report:reranked %d->%d stories via 4B reranker", len(stories), len(picked)
        )
        return picked, True
    except Exception as exc:
        logger.warning("market_report:rerank_failed fail_open err=%s", str(exc)[:140])
        return stories[:top_n], False

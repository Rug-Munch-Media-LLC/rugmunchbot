"""The Daily Rug — story gathering (best of the previous day, ranked).

Sources (each fail-open, gathered in parallel):
* Ranked stories — the rugbrain consumer reranker
  (``POST /api/v1/rag/news-rerank``) over the last 24h. This is the
  PRIMARY source: it is the fleet's curated, importance-ranked news lane,
  not raw RSS.
* RAG intel — rugbrain ``news_v2`` + ``social_intel`` + ``threat_intel_v2``
  via the bot-side direct-Qdrant lane, with the rag-api ``/search/multi``
  fallback. Fills gaps the reranker missed.
* Scanner alerts — the top rugs/scams of the last 24h from the scanner
  pipeline (``GET /api/v1/alerts/recent``).

Ranking: the reranker already returns importance-ordered stories; the RAG
hits are sorted by score and deduped by content prefix. The LLM prompt
receives the ranked block FIRST so the brief leads with the day's best.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("rmi_bot.channels.daily_rug")

#: RAG collections queried for the brief's intel context.
# 2026-10-17: the live 4096-dim twins. The bare `news_v2` name still
# exists but carries a NAMED vector ({"dense": {...}}) so an unnamed
# {"vector": [...]} search 400s, and `threat_intel_v2` does not exist
# at all (404). These are the collections the 8B embedding matches.
_RAG_COLLECTIONS = ["news_v2_8b", "social_intel_8b", "threat_intel_v2_8b"]
_RAG_QUERY = (
    "most important crypto stories of the last 24 hours: regulation, ETF flows, "
    "hacks, exploits, depegs, protocol launches, whale moves, scam coins, "
    "tokens being shilled by KOLs, market-moving news "
    "yesterday only, no stories older than 24 hours"
)
_RAG_LIMIT = 5  # per collection — dedup keeps the prompt small

#: Scanner-alert sweep window: the last 24 hours.
_SCANNER_ALERT_MINUTES = 1440

#: The brief covers the PREVIOUS day — a 24h window.
_BRIEF_WINDOW_HOURS = 24.0


async def _fetch_rag_context() -> list[dict[str, Any]]:
    """Query rugbrain news_v2 + social_intel + threat_intel_v2 (fail-open)."""
    from rugmunchbot.tools.rag_lookup import _rag_direct_qdrant, _rag_direct_search_multi

    hits = await _rag_direct_qdrant(_RAG_QUERY, _RAG_COLLECTIONS, _RAG_LIMIT)
    if not hits:
        hits = await _rag_direct_search_multi(_RAG_QUERY, _RAG_COLLECTIONS, _RAG_LIMIT * 2)
    if not hits:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for h in sorted(hits, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        content = str(h.get("content") or "").strip()
        if not content:
            continue
        key = " ".join(content.lower().split())[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= 10:
            break
    return out


async def _fetch_ranked_stories() -> list[dict[str, Any]]:
    """Top reranker-ranked stories of the previous day (fail-open).

    The reranker is the fleet's curated importance lane — the brief leads
    with these, not with raw feed items. A hard 24h recency filter drops
    undated items (the exact vector by which months-old stories slip in).
    """
    from rugmunchbot.channels._news_rerank import fetch_top_ranked_stories

    try:
        stories = await fetch_top_ranked_stories(window_hours=_BRIEF_WINDOW_HOURS)
    except Exception as exc:  # fail-open — never block the brief
        logger.warning("daily_rug:ranked_stories_failed err=%s", str(exc)[:140])
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=_BRIEF_WINDOW_HOURS)
    fresh: list[dict[str, Any]] = []
    for item in stories:
        raw = item.get("published_at")
        dt: datetime | None = None
        try:
            if isinstance(raw, (int, float)):
                ts = float(raw)
                if ts > 1e12:  # ms epoch
                    ts /= 1000.0
                dt = datetime.fromtimestamp(ts, tz=UTC)
            elif raw:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
        except (ValueError, TypeError, OSError):
            dt = None
        if dt is not None and dt >= cutoff:
            fresh.append(item)
    if len(fresh) < len(stories):
        logger.info(
            "daily_rug:recency_filter kept=%d dropped=%d",
            len(fresh),
            len(stories) - len(fresh),
        )
    return fresh


async def gather_daily_rug_data() -> dict[str, Any]:
    """Pull every brief block in parallel; never raises.

    Returns ``{alerts, rag_intel, ranked_stories, sources, generated_at}``
    where ``sources`` lists the lanes that returned data. Every block
    degrades to an empty value — a broken lane contributes nothing.
    """
    from rugmunchbot.channels.scanner_alerts import fetch_rag_alerts

    results = await asyncio.gather(
        fetch_rag_alerts(minutes=_SCANNER_ALERT_MINUTES),
        _fetch_rag_context(),
        _fetch_ranked_stories(),
        return_exceptions=True,
    )
    alerts, rag_intel, ranked = (r if not isinstance(r, BaseException) else [] for r in results)
    for name, value in (("alerts", alerts), ("rag_intel", rag_intel), ("ranked", ranked)):
        if isinstance(value, BaseException):
            logger.warning("daily_rug:lane_failed lane=%s err=%s", name, str(value)[:120])

    sources = [
        name
        for name, value in (
            ("alerts", alerts),
            ("rag_intel", rag_intel),
            ("ranked_stories", ranked),
        )
        if value
    ]
    return {
        "alerts": alerts or [],
        "rag_intel": rag_intel or [],
        "ranked_stories": ranked or [],
        "sources": sources,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _serialize_context(context: dict[str, Any]) -> str:
    """Compact, LLM-friendly text of the gathered data (cite-or-omit)."""
    parts: list[str] = []

    ranked = context.get("ranked_stories") or []
    if ranked:
        lines = ["RANKED TOP STORIES (reranker-ordered, best first):"]
        for i, s in enumerate(ranked[:8], 1):
            title = str(s.get("title") or "").strip()
            summary = str(s.get("summary") or "").strip()[:220]
            src = str(s.get("source") or "").strip()
            url = str(s.get("url") or "").strip()
            cat = str(s.get("category") or "").strip()
            bits = [f"{i}. {title or summary[:80]}"]
            if cat:
                bits.append(f"category {cat}")
            if src:
                bits.append(f"source {src}")
            if summary and title:
                bits.append(summary)
            line = " — ".join(bits)
            if url:
                line += f" [url={url}]"
            lines.append(line)
        parts.append("\n".join(lines))

    alerts = context.get("alerts") or []
    if alerts:
        lines = ["SCANNER ALERTS (top rugs/scams, last 24h):"]
        for a in alerts[:6]:
            name = str(a.get("name") or a.get("title") or "Unknown token").strip()
            symbol = str(a.get("symbol") or "").strip()
            chain = str(a.get("chain") or "").strip()
            severity = str(a.get("severity") or a.get("risk_level") or "").strip()
            score = a.get("safety_score", a.get("risk_score"))
            flags = a.get("risk_flags") or a.get("flags") or []
            url = str(a.get("url") or "").strip()
            bits = [f"{name} ({symbol})" if symbol and symbol != name else name]
            if chain:
                bits.append(f"chain {chain}")
            if severity:
                bits.append(f"severity {severity}")
            if score is not None:
                bits.append(f"risk {score}")
            if flags:
                bits.append("flags: " + ", ".join(str(f) for f in flags[:4]))
            line = " — ".join(bits)
            if url:
                line += f" [url={url}]"
            lines.append(line)
        parts.append("\n".join(lines))

    rag = context.get("rag_intel") or []
    if rag:
        lines = ["RAG INTEL (news_v2 / social_intel / threat_intel_v2):"]
        for h in rag[:8]:
            content = str(h.get("content") or "").strip()[:300]
            meta = h.get("metadata") or {}
            url = str(meta.get("url") or h.get("url") or "").strip()
            coll = str(h.get("collection") or "").strip()
            line = f"- {content}"
            if coll:
                line += f" [collection={coll}]"
            if url:
                line += f" [url={url}]"
            lines.append(line)
        parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else "(no data)"


def pick_daily_rug_image(context: dict[str, Any]) -> str:
    """Best available image for the brief (story og:image, else "").

    The caller falls back to the branded Daily Rug logo when this is empty.
    """
    for story in context.get("ranked_stories") or []:
        image = str(story.get("image_url") or story.get("image") or "").strip()
        if image.startswith(("http://", "https://")):
            return image
    for hit in context.get("rag_intel") or []:
        meta = hit.get("metadata") or {}
        image = str(meta.get("image_url") or hit.get("image_url") or "").strip()
        if image.startswith(("http://", "https://")):
            return image
    return ""

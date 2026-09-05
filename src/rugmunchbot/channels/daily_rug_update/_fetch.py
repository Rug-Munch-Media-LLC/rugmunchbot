"""Daily rug update — RAG + intel gathering (all content RAG-served).

Lanes (each fail-open, gathered in parallel):
* RAG context — rugbrain ``news_v2`` + ``threat_intel_v2`` + scanner memory
  via the bot-side direct-Qdrant lane (``rag_lookup._rag_direct_qdrant``,
  one embed + per-collection vector search) with the rag-api ``/search/multi``
  fallback — the same lane ``daily_market_summary._assemble`` uses.
* Shill campaigns — rugbrain ``POST /rag/shill-campaigns`` (reuses
  ``channels.shill_campaign_post.fetch_shill_campaigns``).
* Scanner alerts — rugbrain ``GET /api/v1/alerts/recent`` (reuses
  ``channels.scanner_alerts.fetch_rag_alerts``) — top rugs/scams of the
  last 24h straight from the scanner pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("rmi_bot.channels.daily_rug_update")

#: RAG collections queried for the update's intel context. ``scanner_memory``
#: carries the scanner pipeline's detection write-ups; [REVIEW] if the
#: deployed collection name differs, the lane degrades to [] harmlessly.
_RAG_COLLECTIONS = ["news_v2", "threat_intel_v2", "scanner_memory"]
_RAG_QUERY = (
    "rug pull scam exploit honeypot shill campaign KOL promotion whale movement "
    "breaking news last 24 hours "
    # Recency guard (2026-09-05): vector search kept surfacing months-old
    # stories (e.g. "SEC v. BlockFi settlement $100M, 2022-02-14") because
    # similarity ignores publish date. The phrase biases the query toward
    # fresh docs; the hard ``published_at`` filter below enforces it.
    "last 12 hours only, no stories older than today"
)
_RAG_LIMIT = 4  # per collection — dedup keeps the prompt small
_RAG_SNIPPET_MAX = 300

#: Scanner-alert sweep window: the last 24 hours.
_SCANNER_ALERT_MINUTES = 1440


async def _fetch_rag_context() -> list[dict[str, Any]]:
    """Query rugbrain news_v2 + threat_intel_v2 + scanner memory (fail-open).

    Mirrors ``daily_market_summary._assemble._fetch_rag_intel``: direct-Qdrant
    lane first, rag-api ``/search/multi`` fallback, dedupe by content prefix.
    """
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
        if len(out) >= 8:
            break
    return out


async def _fetch_ranked_stories() -> list[dict[str, Any]]:
    """Top reranker-ranked stories since the last Morning Rug (fail-open).

    The rugbrain consumer reranker (``POST /api/v1/rag/news-rerank``) ranks
    news_v2 over the timeframe between the two flagship posts — the Midday
    Update leads with those stories, not "everything in the last 24h".
    """
    from rugmunchbot.channels._news_rerank import (
        fetch_top_ranked_stories,
        midday_window_hours,
    )

    try:
        stories = await fetch_top_ranked_stories(window_hours=midday_window_hours())
    except Exception as exc:  # fail-open — never block the update
        logger.warning("daily_rug_update:ranked_stories_failed err=%s", str(exc)[:140])
        return []

    # Hard recency filter (2026-09-05): only stories published in the last
    # 12 hours. The reranker keeps undated items (flagged ``_undated``) —
    # those are exactly how months-old stories slip through when the doc
    # lacks ``published_at``, so they are dropped here (fail-closed).
    cutoff = datetime.now(UTC) - timedelta(hours=12)
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
            "daily_rug_update:recency_filter kept=%d dropped=%d",
            len(fresh),
            len(stories) - len(fresh),
        )
    # Cross-edition dedup (2026-09-05): drop stories the Morning Rug already
    # posted today so the Midday Update leads with fresh stories only.
    from rugmunchbot.channels._edition_dedup import exclude_morning_stories

    return exclude_morning_stories(fresh)


async def gather_rug_update_data() -> dict[str, Any]:
    """Pull every update block in parallel; never raises.

    Returns ``{alerts, campaigns, rag_intel, ranked_stories, sources,
    generated_at}`` where ``sources`` lists the lanes that returned data.
    Every block degrades to an empty value — a broken lane contributes
    nothing.
    """
    from rugmunchbot.channels.scanner_alerts import fetch_rag_alerts
    from rugmunchbot.channels.shill_campaign_post import fetch_shill_campaigns

    results = await asyncio.gather(
        fetch_rag_alerts(minutes=_SCANNER_ALERT_MINUTES),
        fetch_shill_campaigns(window_hours=24, min_kols=3),
        _fetch_rag_context(),
        _fetch_ranked_stories(),
        return_exceptions=True,
    )
    cleaned: list[list[dict[str, Any]]] = [r if isinstance(r, list) else [] for r in results]
    names = ["alerts", "campaigns", "rag_intel", "ranked_stories"]
    sources = [n for n, v in zip(names, cleaned, strict=True) if v]
    return {
        "alerts": cleaned[0],
        "campaigns": cleaned[1],
        "rag_intel": cleaned[2],
        "ranked_stories": cleaned[3],
        "sources": sources,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _serialize_context(context: dict[str, Any]) -> str:
    """Compact, LLM-friendly text of the gathered data (cite-or-omit)."""
    parts: list[str] = []

    alerts = context.get("alerts") or []
    if alerts:
        a_lines = []
        for a in alerts[:6]:
            name = str(a.get("name") or a.get("title") or "Unknown token").strip()
            symbol = str(a.get("symbol") or "").strip()
            chain = str(a.get("chain") or "").strip()
            severity = str(a.get("severity") or a.get("risk_level") or "").strip()
            score = a.get("safety_score", a.get("risk_score"))
            flags = a.get("risk_flags") or a.get("flags") or []
            url = str(a.get("url") or "").strip()
            image = str(a.get("image_url") or "").strip()
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
            if image:
                line += f" [image={image}]"
            a_lines.append(line)
        parts.append("TOP RUGS & SCANNER ALERTS (last 24h): " + " | ".join(a_lines))

    campaigns = context.get("campaigns") or []
    if campaigns:
        c_lines = []
        for c in campaigns[:4]:
            token = str(c.get("token") or "?").strip()
            handles = [str(h or "").strip().lstrip("@") for h in (c.get("handles") or [])]
            handles = [h for h in handles if h]
            evidence = int(c.get("evidence_count") or 0)
            urls = [str((e or {}).get("url") or "").strip() for e in (c.get("evidence") or [])]
            urls = [u for u in urls if u.startswith(("http://", "https://"))]
            line = f"{token}: {len(handles)} KOLs (@{', @'.join(handles[:6])}) {evidence} posts"
            if urls:
                line += f" [url={urls[0]}]"
            c_lines.append(line)
        parts.append("SHILL CAMPAIGNS (clustered, last 24h): " + " | ".join(c_lines))

    ranked = context.get("ranked_stories") or []
    if ranked:
        r_lines = []
        for it in ranked[:8]:
            title = str(it.get("title") or "").strip()
            summary = str(it.get("summary") or "").strip()[:200]
            src = str(it.get("source") or "").strip()
            url = str(it.get("url") or "").strip()
            bits = [title or summary[:80]]
            if src:
                bits.append(f"source {src}")
            if summary and title:
                bits.append(summary)
            line = " — ".join(b for b in bits if b)
            if url.startswith(("http://", "https://")):
                line += f" [url={url}]"
            if line.strip():
                r_lines.append(line)
        if r_lines:
            parts.append(
                "RANKED TOP STORIES (reranker-ranked since last Morning Rug, highest "
                "first — build BREAKING NEWS from these): " + " || ".join(r_lines)
            )

    rag_intel = context.get("rag_intel") or []
    if rag_intel:
        r_lines = []
        for hit in rag_intel[:8]:
            content = str(hit.get("content") or "").strip()[:_RAG_SNIPPET_MAX]
            col = str(hit.get("collection") or "rag")
            meta = hit.get("metadata") or {}
            url = str(meta.get("url") or meta.get("source_url") or "").strip()
            image = str(meta.get("image_url") or "").strip()
            if content:
                line = f"[{col}] {content}"
                if url:
                    line += f" [url={url}]"
                if image:
                    line += f" [image={image}]"
                r_lines.append(line)
        if r_lines:
            parts.append(
                "RAG INTEL (news_v2 + threat_intel_v2 + scanner memory): " + " || ".join(r_lines)
            )
    return "\n".join(parts)


def pick_update_image(context: dict[str, Any]) -> str:
    """First usable http(s) image for the post (or "").

    Priority: scanner-alert evidence image → RAG hit metadata image. The
    post MUST carry an image when any lane provides one.

    2026-09-02 image-leak fix: RAG hit metadata is untrusted (pre-fix-era
    docs carry the operator's brand banner) — every candidate is checked
    against the image doctrine's banned patterns before it can win.
    """
    from rugmunchbot.support.image_policy import is_valid_image

    for a in context.get("alerts") or []:
        url = str((a or {}).get("image_url") or "").strip()
        if url.startswith(("http://", "https://")) and is_valid_image(url):
            return url
    for hit in context.get("rag_intel") or []:
        meta = (hit or {}).get("metadata") or {}
        url = str(meta.get("image_url") or "").strip()
        if url.startswith(("http://", "https://")) and is_valid_image(url):
            return url
    return ""

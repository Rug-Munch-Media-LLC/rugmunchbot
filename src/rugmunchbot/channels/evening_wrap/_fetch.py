"""Evening Wrap — the day's recap gathering (scams, KOLs, rugs).

DISTINCT source facet from the Morning Brief and the Midday Market Report.
The Evening Wrap is the day's RECAP of the crypto underbelly:

* Scanner alerts — the rugs/scams detected today (``fetch_rag_alerts``).
* Shill campaigns — KOLs shilling (``fetch_shill_campaigns``).
* Recap intel — rugbrain ``threat_intel_v2`` + ``scam_intel_v2`` +
  ``social_intel`` via the bot-side direct-Qdrant lane, queried for the
  day's rugs, shills and exit ramps.

Every lane is fail-open; a broken lane contributes nothing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("rmi_bot.channels.evening_wrap")

#: RAG collections for the recap facet (the underbelly, not the market).
_RAG_COLLECTIONS = ["threat_intel_v2", "scam_intel_v2", "social_intel"]
_RAG_QUERY = (
    "today's crypto rugs and scams: rug pulls, honeypots, exit scams, "
    "KOL shill campaigns, tokens being shilled by influencers, "
    "what rugged today, deployer dumps, LP pulls "
    "last 24 hours only, no stories older than today"
)
_RAG_LIMIT = 5  # per collection — dedup keeps the prompt small

#: Scanner-alert sweep window: the last 24 hours.
_SCANNER_ALERT_MINUTES = 1440


async def _fetch_recap_intel() -> list[dict[str, Any]]:
    """Query rugbrain threat/scam/social collections (fail-open)."""
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


async def gather_evening_wrap_data() -> dict[str, Any]:
    """Pull every wrap block in parallel; never raises.

    Returns ``{alerts, campaigns, recap_intel, sources, generated_at}``.
    Every block degrades to an empty value.
    """
    from rugmunchbot.channels.scanner_alerts import fetch_rag_alerts
    from rugmunchbot.channels.shill_campaign_post import fetch_shill_campaigns

    results = await asyncio.gather(
        fetch_rag_alerts(minutes=_SCANNER_ALERT_MINUTES),
        fetch_shill_campaigns(window_hours=24, min_kols=3),
        _fetch_recap_intel(),
        return_exceptions=True,
    )
    alerts, campaigns, recap = (r if not isinstance(r, BaseException) else [] for r in results)
    for name, value in (
        ("alerts", alerts),
        ("campaigns", campaigns),
        ("recap_intel", recap),
    ):
        if isinstance(value, BaseException):
            logger.warning("evening_wrap:lane_failed lane=%s err=%s", name, str(value)[:120])

    sources = [
        name
        for name, value in (
            ("alerts", alerts),
            ("campaigns", campaigns),
            ("recap_intel", recap),
        )
        if value
    ]
    return {
        "alerts": alerts or [],
        "campaigns": campaigns or [],
        "recap_intel": recap or [],
        "sources": sources,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _serialize_context(context: dict[str, Any]) -> str:
    """Compact, LLM-friendly text of the gathered recap data (cite-or-omit)."""
    parts: list[str] = []

    alerts = context.get("alerts") or []
    if alerts:
        lines = ["SCANNER ALERTS (rugs/scams detected today):"]
        for a in alerts[:8]:
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

    campaigns = context.get("campaigns") or []
    if campaigns:
        lines = ["SHILL CAMPAIGNS (KOLs promoting tokens today):"]
        for c in campaigns[:6]:
            token = str(c.get("token") or c.get("symbol") or c.get("name") or "").strip()
            kols = c.get("kols") or c.get("kol_handles") or []
            count = c.get("kol_count") or (len(kols) if isinstance(kols, list) else None)
            url = str(c.get("url") or "").strip()
            bits = [token or "unknown token"]
            if count:
                bits.append(f"{count} KOLs")
            if isinstance(kols, list) and kols:
                bits.append("handles: " + ", ".join(str(k) for k in kols[:4]))
            line = " — ".join(bits)
            if url:
                line += f" [url={url}]"
            lines.append(line)
        parts.append("\n".join(lines))

    recap = context.get("recap_intel") or []
    if recap:
        lines = ["RECAP INTEL (threat_intel_v2 / scam_intel_v2 / social_intel):"]
        for h in recap[:8]:
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


def pick_evening_image(context: dict[str, Any]) -> str:
    """Best available image for the wrap (alert og:image, else "")."""
    for alert in context.get("alerts") or []:
        image = str(alert.get("image_url") or alert.get("image") or "").strip()
        if image.startswith(("http://", "https://")):
            return image
    for hit in context.get("recap_intel") or []:
        meta = hit.get("metadata") or {}
        image = str(meta.get("image_url") or hit.get("image_url") or "").strip()
        if image.startswith(("http://", "https://")):
            return image
    return ""


__all__ = [
    "_serialize_context",
    "gather_evening_wrap_data",
    "pick_evening_image",
]

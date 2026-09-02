"""Shill-campaign channel poster — rugbrain campaigns → @cryptorugmuncher.

2026-08-31: rugbrain's POST /rag/shill-campaigns clusters coordinated shill
evidence (``threat_intel_v2`` payloads, ``kind`` = ``shill_evidence``) into
campaigns — 3+ distinct KOL handles pushing the same token inside a 24h
window. This task is the auto-poster: it periodically clusters, finds NEW
campaigns, and posts each to the flagship channel with a screenshot + real
commentary in the cryptorugmuncher voice.

Design:

* Dedup — campaign signature = sha256(token + window_start + sorted
  handles). Recorded through the scheduler's global ``_remember`` namespace
  (Redis-durable, fail-open), so a restart never reposts a campaign.
* Screenshot — the evidence screenshots already captured by the shill
  watcher (``tools.shill_shot``) are persisted on disk; the first evidence
  item with an existing ``image_path`` is attached via ``send_photo``.
  No new capture calls here — reuse what was already captured.
* Commentary — deterministic, voice-checked against the banned-phrase list
  (``prompts.py`` NO-AI-SLOP list + ``style_guides.py`` main contract):
  no botlike openers, no hedging, one concrete figure per claim.
* One source link per post — the strongest evidence URL only; never
  double-linked.

Fail-open everywhere: rugbrain down, empty sweep, Redis outage, missing
screenshot, or a failed send never crashes the tick — silence is valid.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from telegram.constants import ParseMode

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.shill_campaign_post")

#: RugBrain base URL (same env chain as the vendored client).
_RUGBRAIN_API_URL: str = os.getenv("RAG_API_URL", "http://rugbrain-api:8012/api/v1").rstrip("/")
_RAG_API_KEY: str = os.getenv("RMI_RAG_API_KEY", "")
_HTTP_TIMEOUT: int = int(os.getenv("RAG_HTTP_TIMEOUT", "60"))

#: Env gate + cadence (minutes). Defaults on, every 90 min.
SHILL_CAMPAIGN_ENABLED = os.getenv("RMI_SHILL_CAMPAIGN_POST_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
SHILL_CAMPAIGN_INTERVAL_MIN = int(os.getenv("RMI_SHILL_CAMPAIGN_POST_INTERVAL_MIN", "90"))

#: Max campaigns posted per run (bounded, no spam).
SHILL_CAMPAIGN_MAX_POSTS = int(os.getenv("RMI_SHILL_CAMPAIGN_MAX_POSTS", "2"))

#: Banned-phrase list (prompts.py "NO AI SLOP" + style_guides anti-slop).
_BANNED_PHRASES: tuple[str, ...] = (
    "delve into",
    "dive into",
    "unpack",
    "it's worth noting",
    "in the ever-evolving world of crypto",
    "navigate the complex landscape",
    "in the world of crypto",
    "the crypto space is buzzing",
    "landscape of",
    "let's explore",
    "it's important to understand",
    "at the end of the day",
    "robust",
    "revolutionary",
    "game-changing",
    "paradigm shift",
    "staying ahead of the curve",
    "the future of finance",
    "main character",
    "this is not financial advice",
    "not financial advice",
    "nfa",
    "dyor",
    "stay tuned",
    "as an ai",
    "per my data",
    "in this chat",
)


def campaign_signature(campaign: dict[str, Any]) -> str:
    """Stable dedupe identity: token + window + member-handle hash.

    Same campaign re-clustered on a later sweep (same members, same window
    start) hashes identically; a genuinely new cluster (new member or new
    window) gets a fresh signature.
    """
    token = str(campaign.get("token") or "").strip().lower()
    window = str(campaign.get("window_start") or "")[:19]
    handles = ",".join(
        sorted(
            str(h or "").strip().lstrip("@").lower()
            for h in (campaign.get("handles") or [])
            if str(h or "").strip()
        )
    )
    return hashlib.sha256(f"{token}|{window}|{handles}".encode()).hexdigest()[:24]


def _span_hours(window_start: str, window_end: str) -> float:
    """Campaign time span in hours (0.0 when unparsable)."""
    try:
        start = datetime.fromisoformat(str(window_start).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(window_end).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        return max(0.0, (end - start).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        return 0.0


def commentary(campaign: dict[str, Any]) -> str:
    """Commentary on the pattern, in the cryptorugmuncher voice.

    Deterministic (no LLM call, no banned phrases): reads the campaign's
    shape — member count, span, evidence density — and states the take.
    """
    kols = int(campaign.get("kol_count") or len(campaign.get("handles") or []) or 1)
    evidence = int(campaign.get("evidence_count") or 0)
    span = _span_hours(campaign.get("window_start", ""), campaign.get("window_end", ""))
    span_txt = f"{span:.0f}h" if span >= 1 else "under an hour"
    if kols >= 5:
        return (
            f"{kols} separate call channels pushed this inside {span_txt} "
            f"— {evidence} posts total. Coordination this tight is rarely "
            f"conviction; it is usually a coordinated exit ramp. Watch who "
            f"exits first."
        )
    if evidence > kols * 2:
        return (
            f"{kols} accounts, {evidence} posts in {span_txt} — the same "
            f"pitch repeated far past the point where organic coverage "
            f"would have moved on. Amplified pushes like this usually "
            f"front-load an exit."
        )
    return (
        f"{kols} accounts pushed this within {span_txt} — too many "
        f"voices on one ticker, too little time between them. That "
        f"pattern rhymes with paid promotion, not independent research."
    )


def build_shill_campaign_post(campaign: dict[str, Any]) -> str:
    """Build the HTML post body for one campaign (summary + commentary).

    Exactly one source link (the first evidence URL) — no double-linking.
    Raises ValueError when the banned-phrase check fails (never ship slop).
    """
    token = str(campaign.get("token") or "").strip()
    handles = [str(h or "").strip().lstrip("@") for h in (campaign.get("handles") or [])]
    handles = [h for h in handles if h]
    evidence = int(campaign.get("evidence_count") or 0)
    url = ""
    for item in campaign.get("evidence") or []:
        u = str((item or {}).get("url") or "").strip()
        if u.startswith(("http://", "https://")):
            url = u
            break

    lines = [f"<b>Shill campaign: {token}</b>"]
    if handles:
        shown = ", ".join("@" + h for h in handles[:6])
        more = f" +{len(handles) - 6} more" if len(handles) > 6 else ""
        lines.append(f"KOLs ({len(handles)}): {shown}{more}")
    if evidence:
        lines.append(f"Evidence: {evidence} posts")
    if url:
        lines.append(f"Source: {url}")

    commentary_txt = commentary(campaign)
    for banned in _BANNED_PHRASES:
        if banned in commentary_txt.lower():
            raise ValueError(f"style violation: banned phrase {banned!r}")
    lines.append("")
    lines.append(commentary_txt)
    return "\n".join(lines)


def _pick_screenshot(campaign: dict[str, Any]) -> str | None:
    """First evidence image_path that still exists on disk (None otherwise)."""
    for item in campaign.get("evidence") or []:
        path = str((item or {}).get("image_path") or "").strip()
        if path and os.path.isfile(path):
            return path
    return None


async def fetch_shill_campaigns(
    *,
    window_hours: int | None = None,
    min_kols: int | None = None,
) -> list[dict[str, Any]]:
    """POST rugbrain /rag/shill-campaigns; returns campaigns ([] on failure)."""
    payload: dict[str, Any] = {}
    if window_hours:
        payload["window_hours"] = int(window_hours)
    if min_kols:
        payload["min_kols"] = int(min_kols)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            r = await client.post(
                f"{_RUGBRAIN_API_URL}/rag/shill-campaigns",
                json=payload,
                headers={"X-API-Key": _RAG_API_KEY},
            )
            if r.status_code == 200:
                return list(r.json().get("campaigns") or [])
    except Exception as exc:  # fail-open by contract
        logger.debug("shill_campaign_post:fetch_failed err=%s", str(exc)[:160])
    return []


async def run_shill_campaign_poster(ctx: Any) -> int:
    """Sweep rugbrain for new campaigns and post each to the main channel.

    Returns the number of campaigns posted. Fail-open everywhere.
    """
    if not SHILL_CAMPAIGN_ENABLED:
        return 0
    from rugmunchbot.channels.scheduler._core import (
        _chat_id,
        _release,
        _remember,
        _safe_send,
    )

    chat_id = _chat_id("main")
    if not chat_id:
        logger.debug("shill_campaign_post:skipped reason=no_chat_id")
        return 0

    campaigns = await fetch_shill_campaigns()
    posted = 0
    for campaign in campaigns:
        if posted >= SHILL_CAMPAIGN_MAX_POSTS:
            break
        try:
            key = f"shillcamp:{campaign_signature(campaign)}"
            text = build_shill_campaign_post(campaign)
        except Exception as exc:  # malformed campaign — skip, never crash
            logger.warning("shill_campaign_post:build_failed err=%s", str(exc)[:120])
            continue
        if not _remember("shillcamp", key):
            continue  # already posted
        # HARD quality gate (2026-08-31): lint → autofix → skip.
        from rugmunchbot.support.quality_gate import pre_post_quality_gate

        gate = await pre_post_quality_gate(
            text,
            source=str(campaign.get("id") or campaign.get("token") or "")[:80],
            kind="shill_campaign",
            logger=logger,
        )
        if not gate.ok:
            _release("shillcamp", key)
            continue
        text = gate.text
        image_path = _pick_screenshot(campaign)
        sent = False
        try:
            if image_path:
                photo = await asyncio.to_thread(Path(image_path).read_bytes)
                await ctx.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                )
                sent = True
            elif text:
                # Image doctrine (2026-08-31): no local screenshot → ship the
                # gate-resolved image (branded placeholder) as photo + caption
                # instead of a naked text message.
                from rugmunchbot.channels._media import send_news_photo
                from rugmunchbot.support.image_policy import is_valid_image

                image_url = str(gate.image_url or "").strip()
                # 2026-09-02 image-leak fix: banned imagery (profile
                # avatars/headers, brand chrome) never ships — text-only.
                if image_url.startswith(("http://", "https://")) and is_valid_image(image_url):
                    sent = await send_news_photo(
                        ctx.bot, chat_id, caption=text, photo_url=image_url
                    )
                if not sent:
                    sent = await _safe_send(ctx.bot, chat_id, text)
        except Exception as exc:
            logger.warning("shill_campaign_post:send_failed err=%s", str(exc)[:120])
        if sent:
            posted += 1
        else:
            _release("shillcamp", key)  # allow retry next tick
    return posted


__all__ = [
    "build_shill_campaign_post",
    "campaign_signature",
    "commentary",
    "fetch_shill_campaigns",
    "run_shill_campaign_poster",
]

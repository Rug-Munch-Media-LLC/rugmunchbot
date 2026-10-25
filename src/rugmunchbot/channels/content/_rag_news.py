"""RugBrain news retrieval rail — the pipeline's first content source (2026-10-11).

Why this exists
---------------
The news channel's sweep (Telethon → normalize → dedupe) is the primary rail,
but it is *frequently* empty: a headless client without credentials, channels
in cooldown, or a quiet news day all yield ``[]``. Before this module the only
fallback was ``fetch_trending_news`` — an rmi-backend HTTP endpoint that is not
guaranteed to exist in the standalone deployment. The result is what the
operator reported: the channel goes silent.

This rail goes to RugBrain directly, through the repo's OWN vendored client
(``rugmunchbot._vendor.rugbrain_client.rugbrain_search``) — no second client,
no duplicated HTTP code. It searches the ``news_v2`` collection (RugBrain
resolves the server-side ``_8b`` suffix itself; the API accepts the BASE name)
with **rerank ENABLED** so the cross-orders candidates before we ever see them.

Visibility (2026-10-25)
-----------------------
A rerank outage used to collapse into the same ``[]`` as a quiet news day, so a
post could claim RAG-derived context it never received. The rail now returns a
typed ``RagNewsResult``: on rerank FAILURE it retries once with ``rerank=False``
and marks the batch ``degraded=True`` with a reason; every item carries
``_rag_rerank`` / ``_rag_degraded`` so downstream text can only claim what it
actually got. A genuinely empty result stays ``degraded=False`` (it is data).

Contract (matching every other fetcher in this package):
* returns a normalized list of dicts, or ``[]``
* **never raises** — every failure is a structured log and an empty list
* every value comes from the live source; nothing is synthesized
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from rugmunchbot.channels.content.helpers import logger

#: RugBrain news collection. The API accepts the BASE name — the ``_8b``
#: suffix is resolved server-side on the 8B-only hosted rerank lane
#: (deepinfra → siliconflow → openrouter).
_DEFAULT_COLLECTION = "news_v2"

#: Candidate pool handed to the cross-encoder. Comfortably above ``limit`` so
#: the reranker has room to promote a good story past a bad-but-recent one.
_SEARCH_OVERFETCH = 10

__all__ = ["RagNewsResult", "fetch_rag_news_items", "fetch_rag_news_result"]


@dataclass(frozen=True)
class RagNewsResult:
    """Typed news-rail outcome — ``degraded`` = we did not get what we claim."""

    items: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    reason: str = ""
    rerank: bool = True


def _collection() -> str:
    """News collection name (env override, never a hardcoded suffix)."""
    return os.getenv("RMI_RAG_NEWS_COLLECTION", _DEFAULT_COLLECTION).strip() or _DEFAULT_COLLECTION


async def _search(limit: int, rerank: bool) -> list[dict[str, Any]]:
    """One RugBrain search; returns raw hits (may be empty)."""
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    return await rugbrain_search(
        query="latest breaking crypto market news",
        collections=[_collection()],
        limit=max(1, limit) * 2,
        rerank=rerank,
    )


def _field(hit: dict[str, Any], metadata: dict[str, Any], *keys: str) -> str:
    """First non-empty string among *keys* (hit first, then metadata)."""
    for key in keys:
        value = str(hit.get(key) or metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _item(hit: dict[str, Any], *, rerank: bool, degraded: bool) -> dict[str, Any] | None:
    """One raw hit -> trending-news item (``None`` when it carries no content)."""
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    title = _field(hit, metadata, "title")
    text = _field(hit, metadata, "text", "content")
    if not title and not text:
        return None
    return {
        "title": (title or text)[:200],
        "summary": (text or title)[:500],
        "url": _field(hit, metadata, "source_url"),
        "source": _field(hit, metadata, "source") or "rugbrain",
        "sentiment_label": _field(hit, metadata, "sentiment_label"),
        "factual_rating": _field(hit, metadata, "factual_rating"),
        # Provenance markers: a post may only claim RAG grounding when these
        # say so (consumed by the news poster's context footer).
        "_rag_source": f"rugbrain:{_collection()}",
        "_rag_rerank": rerank,
        "_rag_degraded": degraded,
    }


def _normalize(
    hits: list[dict[str, Any]], limit: int, *, rerank: bool, degraded: bool
) -> list[dict[str, Any]]:
    """Map raw RugBrain hits onto the shared trending-news item shape."""
    mapped = (
        _item(h, rerank=rerank, degraded=degraded)
        for h in hits[: max(1, limit)]
        if isinstance(h, dict)
    )
    return [item for item in mapped if item is not None]


async def fetch_rag_news_result(limit: int = 5) -> RagNewsResult:
    """Recent news items from RugBrain ``news_v2``, with a typed outcome.

    Rerank is tried first (quality). A rerank FAILURE (exception, not an empty
    result set) retries once without it so the rail still returns news — the
    batch is then flagged ``degraded`` so no post claims reranked context.
    """
    try:
        hits = await _search(limit, rerank=True)
    except Exception as exc:  # fail-open by contract, but LOUD + typed
        logger.warning(
            "rag_news_rerank_failed err=%s fallback=rerank_off",
            f"{type(exc).__name__}:{str(exc)[:140]}",
        )
        try:
            hits = await _search(limit, rerank=False)
        except Exception as exc2:
            logger.warning("rag_news_failed err=%s", f"{type(exc2).__name__}:{str(exc2)[:140]}")
            return RagNewsResult(degraded=True, reason=f"search_failed:{type(exc2).__name__}")
        items = _normalize(hits or [], limit, rerank=False, degraded=True)
        logger.warning(
            "rag_news_degraded reason=rerank_unavailable items=%d collection=%s",
            len(items),
            _collection(),
        )
        return RagNewsResult(items=items, degraded=True, reason="rerank_unavailable", rerank=False)
    if not hits:
        logger.info("rag_news_empty collection=%s (no rail data, not a failure)", _collection())
        return RagNewsResult()
    return RagNewsResult(items=_normalize(hits, limit, rerank=True, degraded=False))


async def fetch_rag_news_items(limit: int = 5) -> list[dict[str, Any]]:
    """Backward-compatible list accessor for :func:`fetch_rag_news_result`."""
    return (await fetch_rag_news_result(limit)).items

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

Contract (matching every other fetcher in this package):
* returns a normalized list of dicts, or ``[]``
* **never raises** — every failure is a debug-level log and an empty list
* every value comes from the live source; nothing is synthesized
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.content.helpers import logger

#: RugBrain news collection. The API accepts the BASE name — the ``_8b``
#: suffix is resolved server-side on the 8B-only hosted rerank lane
#: (deepinfra → siliconflow → openrouter).
_DEFAULT_COLLECTION = "news_v2"

#: Candidate pool handed to the cross-encoder. Comfortably above ``limit`` so
#: the reranker has room to promote a good story past a bad-but-recent one.
_SEARCH_OVERFETCH = 10

__all__ = ["fetch_rag_news_items"]


def _collection() -> str:
    """News collection name (env override, never a hardcoded suffix)."""
    return os.getenv("RMI_RAG_NEWS_COLLECTION", _DEFAULT_COLLECTION).strip() or _DEFAULT_COLLECTION


async def fetch_rag_news_items(limit: int = 5) -> list[dict[str, Any]]:
    """Recent news items from RugBrain ``news_v2`` with rerank enabled.

    Returns normalized items:
        ``{"title", "summary", "url", "source", "sentiment_label",
           "factual_rating"}``
    — the same shape ``fetch_trending_news`` returns, so both rails drop into
    the downstream scorer unchanged.

    Fail-open: RugBrain down, no API key, or an empty result set all yield
    ``[]`` (the caller then tries the next rail). Never raises.
    """
    from rugmunchbot._vendor.rugbrain_client import rugbrain_search

    try:
        hits = await rugbrain_search(
            query="latest breaking crypto market news",
            collections=[_collection()],
            limit=max(1, limit) * 2,
            rerank=True,  # cross-encoder ENABLED — quality first on content
        )
    except Exception as exc:  # fail-open by contract
        logger.debug("rag_news_failed err=%s", str(exc)[:120])
        return []
    if not hits:
        return []

    items: list[dict[str, Any]] = []
    for hit in hits[: max(1, limit)]:
        if not isinstance(hit, dict):
            continue
        metadata = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        title = str(hit.get("title") or metadata.get("title") or "").strip()
        text = str(hit.get("text") or hit.get("content") or "").strip()
        if not title and not text:
            continue
        url = str(metadata.get("source_url") or hit.get("source_url") or "").strip()
        items.append(
            {
                "title": (title or text)[:200],
                "summary": (text or title)[:500],
                "url": url,
                "source": str(metadata.get("source") or "rugbrain").strip(),
                "sentiment_label": str(metadata.get("sentiment_label") or "").strip(),
                "factual_rating": str(metadata.get("factual_rating") or "").strip(),
            }
        )
    return items

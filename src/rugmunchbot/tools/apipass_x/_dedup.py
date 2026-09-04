"""ApiPass X — embed → semantic dedup + rerank (qwen3, fail-open).

References the package namespace (``_pkg``) for the shared recent-vector
store and the embedder/reranker so monkeypatching the package propagates.
"""

from __future__ import annotations

import math
import time
from typing import Any

import rugmunchbot.tools.apipass_x as _pkg


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _remember_vector(text: str, vector: list[float]) -> None:
    _pkg._recent_vectors.append((text, vector))
    _pkg._embed_available = True


def reset_embed_availability() -> None:
    """Force re-probe of the embedder (tests / after an outage)."""
    _pkg._embed_available = None
    _pkg._embed_retry_after = 0.0


def _semantic_is_duplicate(text: str, vector: list[float]) -> bool:
    """Cosine-compare *vector* against recent vectors; True when near-dup."""
    for stored_text, stored_vector in _pkg._recent_vectors:
        if stored_text == text:
            return True
        if _cosine(vector, stored_vector) >= _pkg.DEDUP_COSINE_THRESHOLD:
            return True
    return False


async def semantic_dedup(text: str, *, key: str = "") -> bool:
    """True when *text* is a semantic near-duplicate of a recent item.

    Embeds with qwen3-4B (``_vendor.rag_pkg.embeddings.get_embedding``) and
    compares cosine against the recent-vector window. When the embedder is
    unavailable the vector is not computed and this returns False (the
    caller's URL-keyed dedup still applies) — dedup degrades, never blocks.
    After an outage the embedder is re-probed on a cooldown
    (``APIPASS_EMBED_RETRY_SECONDS``) instead of being disabled forever, and
    the first failure is logged loudly.
    """
    if _pkg._embed_available is False and time.monotonic() < _pkg._embed_retry_after:
        return False
    vector = await _pkg.get_embedding(text)
    if not vector:
        if _pkg._embed_available is not False:
            _pkg.logger.warning(
                "apipass_x:embed_unavailable semantic_dedup_disabled retry_seconds=%s",
                _pkg._EMBED_RETRY_SECONDS,
            )
        _pkg._embed_available = False
        _pkg._embed_retry_after = time.monotonic() + _pkg._EMBED_RETRY_SECONDS
        return False
    _pkg._embed_available = True
    if _semantic_is_duplicate(text, vector):
        return True
    _remember_vector(text, vector)
    return False


async def rerank_tweets(
    tweets: list[dict[str, Any]], query: str, top_n: int = 5
) -> list[dict[str, Any]]:
    """Rank *tweets* by relevance to *query* via the qwen3 reranker.

    Fail-open: reranker unavailable/empty → input order unchanged (the
    caller's own ordering still applies). Returns up to ``top_n`` tweets.
    """
    if not tweets:
        return []
    documents = [str(t.get("text") or "")[:500] for t in tweets]
    try:
        ranked = await _pkg.rerank_results(
            query, documents, top_n=min(top_n, len(documents)), tier="live"
        )
    except Exception as exc:  # fail-open by contract
        _pkg.logger.debug("apipass_x:rerank_failed err=%s", str(exc)[:120])
        return tweets[:top_n]
    if not ranked:
        return tweets[:top_n]
    ordered: list[dict[str, Any]] = []
    for item in sorted(ranked, key=lambda r: float(r.get("relevance_score") or 0.0), reverse=True):
        idx = int(item.get("index") or 0)
        if 0 <= idx < len(tweets):
            ordered.append(tweets[idx])
    return ordered[:top_n] or tweets[:top_n]

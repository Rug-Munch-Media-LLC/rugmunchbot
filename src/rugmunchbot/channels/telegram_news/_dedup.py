"""Semantic news dedup — title-token overlap + embed cosine (2026-08-29).

Closes the "Trump Digital Gold" class of duplicates: beincrypto and
cryptobriefing ran the same story with different URLs, different headlines
("Trump's Digital Gold..." vs "Digital Gold: Trump...") and reworded bodies,
so every exact key (canonical URL / t.me URL / normalized title / content
hash) missed and both copies posted ~simultaneously.

Two new layers, applied AFTER the vendor's exact-key ``dedupe_articles``:

 1. **Title token overlap** (sync, free) — normalized-title token-set Jaccard
    ≥ ``RMI_NEWS_TITLE_OVERLAP_DEDUP`` (default 0.60) marks two headlines as
    the same story. Catches reworded headlines that share most content words.
 2. **Embedding cosine** (async, one embed per candidate) — title + first
    300 chars of body embedded via the DeepInfra-first embed lane
    (``_embed_candidate``:     DeepInfra ``Qwen3-Embedding-8B`` → local
    llama.cpp ``:8012`` → vendored ``get_embedding``); cosine ≥
    ``RMI_NEWS_SEMANTIC_DEDUP_THRESHOLD`` (default 0.82) collapses
    paraphrased copies. Fail-open: embedder down → layer skipped, exact-key
    dedup still ran.

First-wins on input order (the sweep's channel order = recency), matching
``dedupe_articles`` semantics.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.news_ingest.ingest import dedupe_articles, normalize_title

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

logger = get_logger(__name__)

#: Title token-set Jaccard at/above which two headlines are the same story.
_TITLE_OVERLAP_DEFAULT = 0.60
#: Embedding cosine at/above which two articles are the same story.
#: 2026-08-30 recalibrated 0.90 → 0.82: the module's own "Trump Digital Gold"
#: regression case (beincrypto vs cryptobriefing — reworded headline + body,
#: different URL) measures ~0.85-0.89 on the DeepInfra Qwen3-Embedding-8B
#: lane, so the 0.90 default MISSED the exact duplicate it was built to catch.
#: Related-but-distinct stories (e.g. ETH-ETF vs BTC-ETF approval) measure
#: ~0.75, so 0.82 keeps them separate while collapsing true duplicates.
_SEMANTIC_COSINE_DEFAULT = 0.82
#: Max pairwise cosine comparisons per batch (O(n²) guard; sweep batches
#: are ≤ a few hundred — beyond this the oldest candidates stop matching).
_MAX_EMBED_CANDIDATES = 200
#: Concurrent embed HTTP calls (the embed API is a local service; bounded
#: concurrency collapses ~200 serial 1.4s calls into a few seconds).
_EMBED_CONCURRENCY = 8


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


TITLE_OVERLAP_THRESHOLD = _env_float("RMI_NEWS_TITLE_OVERLAP_DEDUP", _TITLE_OVERLAP_DEFAULT)
SEMANTIC_COSINE_THRESHOLD = _env_float(
    "RMI_NEWS_SEMANTIC_DEDUP_THRESHOLD", _SEMANTIC_COSINE_DEFAULT
)

_WORD_RE = re.compile(r"[a-z0-9$]{3,}")
#: Generic words that carry no story identity ("trump says bitcoin..." vs
#: "bitcoin surges as trump..." share these but are different stories).
_STOPWORDS = frozenset(
    {"the", "and", "for", "with", "from", "that", "this", "into", "over", "after", "amid", "as"}
)


def title_tokens(title: str) -> set[str]:
    """Content-word set of a normalized title (lowercase, punct stripped).

    Reuses the vendor ``normalize_title`` (punctuation/emoji stripped,
    breaking markers removed) then keeps tokens ≥3 chars minus stopwords —
    the story-identity words only.
    """
    return {tok for tok in _WORD_RE.findall(normalize_title(title or "")) if tok not in _STOPWORDS}


def title_overlap(a: str, b: str) -> float:
    """Token-set Jaccard between two titles (0.0 when either is empty)."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = float(sum(x * y for x, y in zip(a, b, strict=True)))
    na = float(sum(x * x for x in a) ** 0.5)
    nb = float(sum(x * x for x in b) ** 0.5)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _dedup_text(article: NewsArticle) -> str:
    """Embedding input: title + lead of the body (mirrors rugbrain's fingerprint)."""
    return f"{article.title or ''} {str(article.body or '')[:300]}".strip()


# ══════════════════════════════════════════════════════════════════════════
# Embed lane (2026-10-11) — DeepInfra Qwen3-Embedding-8B (4096d) primary.
# ══════════════════════════════════════════════════════════════════════════
# 2026-10-11 fix: the previous default pointed at the RETIRED 2560-dim 4B
# lane. Port 8004 has nothing listening on it any more (only leftover socat
# proxies on container bridge addresses), so every cosine comparison no-op'd
# and the semantic layer silently degraded to title-overlap only. The live
# fleet lane is DeepInfra ``Qwen/Qwen3-Embedding-8B`` at 4096 dims — the same
# model the systemd drop-in (systemd/60-rag-8b.conf) pins for every other
# embedder. Never 4B / 2560.
#   1. DeepInfra ``Qwen/Qwen3-Embedding-8B`` (4096d, ``/v1/inference``)
#   2. RugBrain rerank/embed lane is 8B-only hosted (deepinfra →
#      siliconflow → openrouter) — the local :8012 API accepts BASE collection
#      names (``news_v2``) and resolves the ``_8b`` suffix server-side, so
#      nothing here points at 4B.
#   3. vendored ``get_embedding`` (last resort; may be unreachable at runtime)
# The chain is fail-open — any unreachable tier returns ``[]`` and the caller
# keeps the article (never drops content on an embedder outage).
_DEEPINFRA_EMBED_URL = os.getenv(
    "DEEPINFRA_EMBED_URL", "https://api.deepinfra.com/v1/inference"
).rstrip("/")
_DEEPINFRA_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-8B")
#: Local llama.cpp OpenAI-compatible embed endpoint (self-hosted fallback).
_LOCAL_EMBED_URL = os.getenv("DEEP_EMBED_URL", "http://127.0.0.1:8012/v1").rstrip("/")
_LOCAL_EMBED_MODEL = os.getenv("DEEP_EMBED_LOCAL_MODEL", "qwen3-embedding-8b")
#: Embedding dims of the live lane (4096 for Qwen3-Embedding-8B). Used by the
#: tests to assert the retired 4B/2560 lane never returns here.
_EMBED_DIMS = 4096
#: Embed timeout per tier (a slow embedder must never stall the whole sweep).
_EMBED_CALL_TIMEOUT = float(os.getenv("DEEPINFRA_EMBED_TIMEOUT", "35"))
#: Key resolved once (env → key file → fleet secret store), cached.
_DEEPINFRA_EMBED_KEY: str | None = None


def _deepinfra_key() -> str | None:
    """Resolve the DeepInfra API key (env → key file → fleet secret store)."""
    global _DEEPINFRA_EMBED_KEY
    if _DEEPINFRA_EMBED_KEY is not None:
        return _DEEPINFRA_EMBED_KEY or None
    key = os.getenv("DEEPINFRA_API_KEY", "").strip()
    if not key:
        kf = os.getenv("DEEPINFRA_KEY_FILE") or os.path.join(
            os.getenv("RMI_SECRETS_DIR", "/srv/secrets"), "deepinfra_api_key"
        )
        try:
            with open(kf, encoding="utf-8") as fh:
                key = fh.read().strip()
        except OSError:
            key = ""
    _DEEPINFRA_EMBED_KEY = key  # cached; "" is the "no key" sentinel
    return _DEEPINFRA_EMBED_KEY or None


async def _embed_deepinfra(text: str) -> list[float]:
    """Embed via DeepInfra-hosted Qwen3-Embedding-8B (4096d). ``[]`` fail-open."""
    key = _deepinfra_key()
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=_EMBED_CALL_TIMEOUT) as client:
            resp = await client.post(
                f"{_DEEPINFRA_EMBED_URL}/{_DEEPINFRA_EMBED_MODEL}",
                headers={"Authorization": f"Bearer {key}"},
                json={"inputs": [text[:2048]]},
            )
        if resp.status_code != 200:
            logger.debug("news_dedup:deepinfra_embed_status=%d", resp.status_code)
            return []
        data = resp.json()
        embs = data.get("embeddings") or []
        return list(embs[0]) if embs and isinstance(embs[0], list) else []
    except Exception as exc:  # fail-open by contract
        logger.debug("news_dedup:deepinfra_embed_failed err=%s", str(exc)[:120])
        return []


async def _embed_local(text: str) -> list[float]:
    """Embed via local llama.cpp qwen3-embedding-8b (``/v1/embeddings``)."""
    try:
        async with httpx.AsyncClient(timeout=_EMBED_CALL_TIMEOUT) as client:
            resp = await client.post(
                f"{_LOCAL_EMBED_URL}/embeddings",
                json={"model": _LOCAL_EMBED_MODEL, "input": [text[:2048]]},
            )
        if resp.status_code != 200:
            return []
        items = resp.json().get("data") or []
        if not items:
            return []
        vec = sorted(items, key=lambda i: i.get("index", 0))[0].get("embedding") or []
        return list(vec) if isinstance(vec, list) else []
    except Exception as exc:  # fail-open by contract
        logger.debug("news_dedup:local_embed_failed err=%s", str(exc)[:120])
        return []


async def _embed_candidate(text: str) -> list[float]:
    """Chain the embed tiers (DeepInfra 8B → local :8012 → vendored default).

    The first tier returning a non-empty vector wins. Every tier is fail-open;
    total failure returns ``[]`` so the caller keeps the article and the
    semantic dedup layer degrades to title-overlap only (loud fail-open).
    """
    vec = await _embed_deepinfra(text)
    if vec:
        return vec
    vec = await _embed_local(text)
    if vec:
        return vec
    from rugmunchbot._vendor.rag_pkg.embeddings import get_embedding

    return await get_embedding(text)


def dedupe_articles_title_overlap(articles: list[NewsArticle]) -> list[NewsArticle]:
    """Sync pass: drop articles whose title token-overlap matches an earlier one."""
    kept: list[NewsArticle] = []
    kept_tokens: list[set[str]] = []
    for article in articles:
        tokens = title_tokens(article.title or "")
        if tokens and any(
            len(tokens & kt) / len(tokens | kt) >= TITLE_OVERLAP_THRESHOLD for kt in kept_tokens
        ):
            logger.info(
                "news_dedup:title_overlap_drop title=%s",
                (article.title or "")[:80],
            )
            continue
        kept.append(article)
        kept_tokens.append(tokens)
    return kept


async def dedupe_articles_semantic(articles: list[NewsArticle]) -> list[NewsArticle]:
    """Full semantic dedup: exact keys → title overlap → embed cosine ≥0.90.

    Fail-open at the embed layer (embedder down → title-overlap result only).
    """
    result = dedupe_articles(articles)
    before_overlap = len(result)
    result = dedupe_articles_title_overlap(result)
    if before_overlap != len(result):
        logger.info("news_dedup:title_overlap dropped=%s", before_overlap - len(result))
    if len(result) <= 1:
        return result

    candidates = result[:_MAX_EMBED_CANDIDATES]
    tail = result[_MAX_EMBED_CANDIDATES:]

    # Concurrent, bounded embed — the embed API is a local service; serializing
    # ~200 HTTP round-trips at ~1.4s each made the whole sweep take 120-170s.
    # 2026-08-30: switched to the DeepInfra-first embed lane
    # (``_embed_candidate``) so the semantic dedup actually embeds — the
    # vendored ``get_embedding`` resolved ``RAG_API_URL`` (a Docker service
    # name) and silently returned ``[]``, no-op'ing the cosine layer.
    sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

    async def _embed_one(article: NewsArticle) -> list[float]:
        async with sem:
            return await _embed_candidate(_dedup_text(article))

    try:
        vectors = await asyncio.gather(*(_embed_one(a) for a in candidates))
    except Exception as exc:  # fail-open — never block the sweep on the embedder
        logger.warning("news_dedup:embed_failed err=%s", str(exc)[:120])
        return result

    if not any(vectors):
        # get_embedding swallows API errors and returns [] — without this
        # guard the semantic layer silently no-ops (every "empty vector" is
        # treated as "keep", no log at all). Make the fail-open LOUD.
        logger.warning(
            "news_dedup:embed_unavailable candidates=%s semantic_layer_skipped fail_open",
            len(candidates),
        )
        return result

    kept: list[NewsArticle] = []
    kept_vecs: list[list[float]] = []
    dropped = 0
    for article, vec in zip(candidates, vectors, strict=True):
        if not vec:
            kept.append(article)  # embedder returned empty for this one — keep
            continue
        dup = False
        for kv in kept_vecs:
            sim = _cosine(vec, kv)
            if sim >= SEMANTIC_COSINE_THRESHOLD:
                dup = True
                logger.info(
                    "news_dedup:semantic_drop title=%s cos=%.3f",
                    (article.title or "")[:80],
                    sim,
                )
                break
        if dup:
            dropped += 1
            continue
        kept.append(article)
        kept_vecs.append(vec)
    if dropped:
        logger.info("news_dedup:semantic dropped=%s kept=%s", dropped, len(kept))
    return kept + tail


def dedup_stats(articles: list[NewsArticle]) -> dict[str, Any]:
    """Small observability dict for the sweep log line."""
    return {
        "threshold_cosine": SEMANTIC_COSINE_THRESHOLD,
        "threshold_title_overlap": TITLE_OVERLAP_THRESHOLD,
        "input": len(articles or []),
    }

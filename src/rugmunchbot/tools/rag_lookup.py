"""rag_lookup tool — direct RAG knowledge grounding for chat questions.

Searches the Rug Munch intel database (RAG) BEFORE any web/LLM fallback for
knowledge-style questions: project history, methodology, definitions
("what is X"), rugpull / scam patterns, exploit write-ups and on-chain
forensics.

Why this tool exists:
  * Tier-1 chat (``POST /api/v1/chat``) already runs the backend's own RAG
    grounding, so the common path is covered.
  * When the backend chat path is unavailable, the local fallback is
    grounded by the Chat RAG Bridge (port 8095) — and by this tool when the
    bridge is also down. Before this tool the fallback path had NO direct
    bot-side RAG access: knowledge questions degraded to the open web or the
    LLM's training memory.
  * It also gives the pipeline a REAL, traceable "RAG hit" data source that
    is independent of the bridge and of the live market tools.

Layers (free stack, per AGENTS.md):
  1. Backend RAG facade — ``POST /api/v1/rag/v2/search`` (the same endpoint
     the legacy bot-side RAG search used before the dedup refactor),
     searched in parallel across the intel collections, deduped by content,
     best hits kept.
  There is no layer 2: if RAG is down the tool reports ``ok=False`` and the
  pipeline degrades exactly as before — RAG is an enhancement, never a gate.

The tool is fail-open and bounded (~5s total). Results are REAL RAG hits;
the LLM must cite them, never invent.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from typing import Any

import httpx

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import BackendApiError
from rugmunchbot.tools.guardrails import extract_addresses, extract_symbols
from rugmunchbot.tools.registry import ToolResult, backend_json_post

logger = get_logger("rmi_bot.tools.rag_lookup")

# ── Knowledge collections (mirrors app/routers/chat/_rag.py RAG_COLLECTIONS
# ── so the direct lookup and the backend auto-RAG search the same intel).
# ── FOCUSED KNOWLEDGE SET (consolidated 2026-08-15 — was 13 collections):
# ── each fan-out collection costs one backend search (embed + rerank), so
# ── only the 5 collections that cover the bot's knowledge intents are kept:
# ──   security_patterns  — curated vuln patterns (408 pts; verdict/severity)
# ──   codebase_reference — secure reference snippets, reputable libs (997 pts)
# ──   defi_exploits      — exploit writeups / PoCs (13.6K pts)
# ──   contract_security  — verdict-labeled good/bad contract patterns (221 pts)
# ──   scam_patterns      — scam pattern docs + rug_timeline alias (9.9K pts)
# ── Dropped as redundant/thin/data-class: threat_intel (live scanner
# ── findings → scanner_v2 handles token asks), scam_intel (600, subsumed by
# ── scam_patterns), news_v2 (market/news, gate skips market asks),
# ── coding_knowledge (SWC dup of security_patterns + snippets dup of
# ── codebase_reference), regulation_enforcement (16), wallet_v2 (entity
# ── data), contract_audits (stubs, overlaps contract_security),
# ── audit_findings (10), stablecoin_market (market data).
# ── Only REAL, populated Qdrant collections are fanned out: phantom legacy
# ── names (token_analysis / wallet_profiles / forensic_reports) and
# ── currently-empty collections (scanned_tokens / whale_transfers) are
# ── dropped — see app.rag._qdrant._COLLECTION_ALIASES for direct requests.
# ── scanner_v2 is added when the question mentions a token address.
KNOWLEDGE_COLLECTIONS: list[str] = [
    "security_patterns",  # curated vuln patterns (408 pts; SWC registry, verdict/severity)
    "codebase_reference",  # secure reference snippets, reputable libs (997 pts)
    "defi_exploits",  # historical exploit writeups / PoCs (14.8K pts)
    "scam_patterns",  # scam pattern docs + rug_timeline alias (9.9K pts)
    "contract_security",  # verdict-labeled good/bad contract patterns (221 pts)
]
RAG_TOKEN_COLLECTIONS: list[str] = [
    "scanner_v2"
]  # scan-to-RAG memory (191 pts — token-address questions)

RAG_LOOKUP_TOP_K = 5  # per collection — dedup keeps the prompt small
RAG_LOOKUP_TIMEOUT = 5.0  # overall budget (seconds) — chat stays snappy
RAG_LOOKUP_CONTEXT_MAX_CHARS = 1800  # total context block size
RAG_LOOKUP_HIT_MAX_CHARS = 700  # per-hit snippet
RAG_LOOKUP_SOURCE_LABEL = "rag:/api/v1/rag/v2/search"

# ── Knowledge-question gate ─────────────────────────────────────────────
# Positive: definitions / explanations / history / methodology / patterns —
# exactly the questions the intel DB answers best.
_KNOWLEDGE_WORDS = re.compile(
    r"\b("
    r"what (is|are|was)|what'?s|who (is|are)|how (does|do|is|are)|why|explain|"
    r"meaning|definition|define|history|background|methodology|method|approach|"
    r"pattern|patterns|typical|common|example|examples|red flags?|warning signs?|"
    r"difference|mechanism|workings|tell me about|learn about|what happened|"
    r"how does it work|how do they work|is it a scam|what makes|what caused"
    r")\b",
    re.IGNORECASE,
)
# Negative: pure live-market / scan / news asks are better served by the live
# tools and the backend auto-RAG — skip the direct lookup for them.
_MARKET_SKIP_WORDS = re.compile(
    r"\b(price of|price now|price for|current price|how much (is|are)|"
    r"market cap|mcap|fdv|trading at|chart|buying|selling|24h|"
    r"volume|buy or sell|buy and sell)\b",
    re.IGNORECASE,
)


def should_rag_lookup(text: str) -> bool:
    """True when ``text`` is a knowledge question the RAG intel DB should
    answer (definitions, explanations, history, methodology, scam patterns)
    — as opposed to a live-market/scan/news ask better served by the live
    tools and the backend auto-RAG."""
    if not text or not text.strip():
        return False
    low = text.lower()
    if _MARKET_SKIP_WORDS.search(low):
        return False
    return bool(_KNOWLEDGE_WORDS.search(low))


# ── Query building + context formatting (mirrors chat/_rag.py helpers) ──


def _build_query(message: str, addresses: list[str], symbols: list[str]) -> str:
    """RAG search query: the raw message plus explicit token hints."""
    query = message.strip()[:500]
    hints: list[str] = []
    if addresses:
        hints.append(f"token address {' '.join(addresses)}")
    if symbols:
        hints.append("token " + " ".join(f"${s}" for s in symbols))
    if hints:
        query = f"{query}\n{'; '.join(hints)}"
    return query[:600]


# Compact model-facing guide appended to the RAG block so the LLM knows how
# to cite the labeled sources (kept short — it counts against the budget).
# Mirrors ``app/routers/chat/_rag.py:_RAG_CITATION_GUIDE`` so the bot-side
# fallback and the backend auto-RAG cite identically.
_RAG_CITATION_GUIDE = (
    "RAG KNOWLEDGE \u2014 cite each source by its [collection\u00b7label], e.g. "
    'a vuln pattern as "Reentrancy \u2014 Critical" or a code snippet as '
    "[codebase_reference\u00b7openzeppelin/contracts\u00b7clean]. "
    "Trust the verdict labels (clean vs malicious, severity)."
)


def _rag_hit_label(h: dict[str, Any]) -> str:
    """Compact ``[collection\u00b7primary\u00b7secondary]`` source label for a RAG hit.

    Mirrors ``app/routers/chat/_rag.py:_rag_hit_label`` so the fallback marks
    verdicts exactly like the backend path. The primary qualifier is the
    corpus's natural key (vuln ``category`` for ``security_patterns`` /
    ``contract_security``, ``library_id`` for ``codebase_reference``); the
    secondary is the verdict/severity the corpus stamps (e.g. ``Critical``
    for a SWC pattern, ``clean``/``malicious`` for a code snippet). Falls
    back gracefully when metadata is absent so every hit still carries a
    collection label.
    """
    col = str(h.get("collection") or "unknown")
    metadata = h.get("metadata") or {}

    def _get(*keys: str) -> str:
        for k in keys:
            v = h.get(k) or metadata.get(k)
            if v:
                return str(v).strip()
        return ""

    category = _get("category", "pattern")
    severity = _get("severity")
    verdict = _get("verdict")
    library_id = _get("library_id", "library_name")

    parts = [col]
    if col == "codebase_reference":
        if library_id:
            parts.append(library_id)
        if verdict:
            parts.append(verdict)
    elif col in ("security_patterns", "contract_security"):
        if category:
            parts.append(category)
        if severity:
            parts.append(severity.capitalize())
        elif verdict:
            parts.append(verdict)
    else:
        if severity:
            parts.append(severity.capitalize())
        elif verdict:
            parts.append(verdict)
    return "[" + "\u00b7".join(parts) + "]"


def _format_context(
    hits: list[dict[str, Any]], max_chars: int = RAG_LOOKUP_CONTEXT_MAX_CHARS
) -> str:
    """Render top RAG hits as a compact, LLM-friendly context block.

    Each hit is labeled with its collection + score AND a verdict source
    label (``[collection\u00b7category\u00b7severity/verdict]``, e.g.
    ``[security_patterns\u00b7Reentrancy\u00b7Critical]`` or
    ``[codebase_reference\u00b7openzeppelin/contracts\u00b7clean]``) so the LLM
    can distinguish clean vs malicious snippets and cite each source. A short
    citation guide is prepended; the guide counts against the budget, so its
    space is reserved up front. Truncates to ``max_chars`` total (guide +
    hits).
    """
    parts: list[str] = []
    guide = _RAG_CITATION_GUIDE
    reserve = len(guide) + 2  # guide + blank line after it
    total = 0 if reserve > max_chars else reserve
    for i, h in enumerate(hits, 1):
        content = (h.get("content") or "").strip()
        if not content:
            continue
        snippet = content[:RAG_LOOKUP_HIT_MAX_CHARS]
        col = h.get("collection") or "unknown"
        score = float(h.get("score") or 0.0)
        block = f"[{i}] Source: {col} (score: {score:.3f}) {_rag_hit_label(h)}\n{snippet}"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    if not parts:
        return ""
    if reserve > max_chars:
        return "\n\n".join(parts)
    return f"{guide}\n\n" + "\n\n".join(parts)


def _content_key(hit: dict[str, Any]) -> str:
    """Deterministic cross-collection dedup key for a RAG hit.

    Normalized (whitespace-collapsed + lowercased) full content, falling
    back to the metadata ``title`` when the body is empty, hashed to a
    compact string. Precisely collapses identical snippets that surface in
    several collections (e.g. a pattern in both security_patterns and
    contract_security) WITHOUT over-collapsing distinct short docs that
    merely share a prefix — the old ``[:100]``-prefix key merged unrelated
    scanner findings that started with the same text.

    The verdict/severity labels are folded INTO the hash so a CLEAN and a
    MALICIOUS snippet with identical code never collapse into one hit —
    they carry opposite security meaning and both must reach the LLM.
    """
    content = (hit.get("content") or "").strip()
    if not content:
        content = str((hit.get("metadata") or {}).get("title") or "").strip()
    norm = re.sub(r"\s+", " ", content.lower()).strip()
    if not norm:
        return ""
    metadata = hit.get("metadata") or {}
    verdict = str(hit.get("verdict") or metadata.get("verdict") or "").strip().lower()
    severity = str(hit.get("severity") or metadata.get("severity") or "").strip().lower()
    return hashlib.sha256(f"{norm}\0{verdict}\0{severity}".encode()).hexdigest()


# ── Direct RugBrain rag-api access (bot runs on HOST → 127.0.0.1:8012) ──────
# The bot's chat path historically grounded through the backend facade
# (``POST /api/v1/rag/v2/search``), but the backend container cannot reach the
# host-loopback rag-api service (binds 127.0.0.1:8012). The BOT runs on the
# HOST, so it CAN reach the rag-api directly. This is the primary RAG lane;
# the backend facade stays as the fallback. (2026-08-30)
_DEFAULT_RAG_API_URL = "http://127.0.0.1:8012/api/v1"


def _split_key_rotation(key: str) -> str:
    """Return the CURRENT key from a ``new,old`` rotation string.

    Key rotation stores the active key FIRST and the previous key after a
    comma (``new,old``). The RAG/backend auth validates an exact match, so the
    client must send ONLY the active (first) part — never the raw ``new,old``
    blob. A plain single key (no comma) passes through unchanged.
    """
    if not key:
        return ""
    # Strip whitespace; keep the FIRST non-empty segment (the active key).
    parts = [k.strip() for k in key.split(",") if k.strip()]
    return parts[0] if parts else ""


def _rag_api_url() -> str:
    """RugBrain rag-api base URL (env ``RAG_API_URL``, host loopback default)."""
    return os.getenv("RAG_API_URL", _DEFAULT_RAG_API_URL).rstrip("/")


def _rag_api_key() -> str:
    """RugBrain X-API-Key, rotation-split to active.

    Resolution chain: ``RMI_RAG_API_KEY`` env → the shared secret-file
    resolver (``/srv/secrets/rugbrain.env`` → gopass). The file fallback keeps
    the lane alive in a context that does not source the bot env file (a
    dry-run harness, a cron one-shot) — without it the lane silently 401s.
    """
    key = os.getenv("RMI_RAG_API_KEY", "")
    if not key:
        from rugmunchbot.config import RAG_API_KEY

        key = RAG_API_KEY
    return _split_key_rotation(key)


async def _rag_direct_search_multi(
    search_query: str, collections: list[str], limit: int
) -> list[dict[str, Any]]:
    """Query the RugBrain ``/api/v1/search/multi`` endpoint directly.

    RRF-fuses across ``collections``, ``rerank=false`` (the cross-encoder
    rack is down as of 2026-08-30 and its absence must not hang the bot). Maps
    hits to the standard ``{content, score, collection, metadata}`` shape the
    prompt formatter expects. Returns ``[]`` on any failure — never raises.
    """
    api_url = _rag_api_url()
    key = _rag_api_key()
    if not api_url or not key or not collections:
        return []
    url = f"{api_url}/search/multi"
    headers = {"Content-Type": "application/json", "X-API-Key": key}
    payload = {"query": search_query, "collections": collections, "limit": limit, "rerank": False}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                RAG_LOOKUP_TIMEOUT, connect=3.0, read=RAG_LOOKUP_TIMEOUT, write=8.0
            )
        ) as client:
            resp = await asyncio.wait_for(
                client.post(url, headers=headers, json=payload), timeout=RAG_LOOKUP_TIMEOUT
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
        return []
    if resp.status_code != 200:
        return []
    try:
        raw = resp.json()
    except ValueError:
        return []
    raw_results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(raw_results, list):
        return []
    out: list[dict[str, Any]] = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        content = (r.get("content") or r.get("text") or "").strip()
        if not content:
            continue
        score = float(r.get("rrf_score") or r.get("similarity") or 0.0)
        if score == 0.0:
            score = float(r.get("score") or 0.0)
        out.append(
            {
                "content": content,
                "score": score,
                "collection": r.get("collection") or collections[0],
                "metadata": r.get("metadata") or {},
                "id": r.get("id") or r.get("doc_id") or "",
            }
        )
    return out


# ── Direct Qdrant lane (bot = HOST -> 127.0.0.1:6334; embed via local qwen3) ──
# The rag-api HTTP wrapper is slow (~9s, RRF + query-transform) and the backend
# container cannot reach it. Direct Qdrant vector search is ~0.03s per
# collection; the whole lane is bound by ONE query embedding (~1-3s). This is
# the primary grounding lane. (2026-08-30)
_DEFAULT_QDRANT_URL = "http://127.0.0.1:6334"
_DEFAULT_EMBED_URL = "http://127.0.0.1:8004/v1/embeddings"
_EMBED_MODEL = os.getenv("DEEPINFRA_EMBED_MODEL", "Qwen/Qwen3-Embedding-4B")
_EMBED_DIMS_EXPECTED = 2560  # Qwen3-Embedding-4B native dim (matches all collections)


def _qdrant_url() -> str:
    return (os.getenv("QDRANT_URL") or _DEFAULT_QDRANT_URL).rstrip("/")


def _embed_url() -> str:
    return (os.getenv("QWEN_EMBED_URL") or os.getenv("RMI_EMBED_URL") or _DEFAULT_EMBED_URL).rstrip(
        "/"
    )


async def _embed_query(query: str) -> list[float] | None:
    """Embed ``query`` via the local Qwen3-Embedding-4B server (2560-dim).

    Returns the vector or ``None`` on any failure (never raises). One embedding
    serves every collection in the lane (2560d cosine is collection-agnostic).
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=3.0, read=8.0, write=8.0)
        ) as client:
            resp = await asyncio.wait_for(
                client.post(
                    _embed_url(),
                    headers={"Content-Type": "application/json"},
                    json={"model": _EMBED_MODEL, "input": query},
                ),
                timeout=8.0,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
        return None
    if resp.status_code != 200:
        return None
    try:
        raw = resp.json()
        vec = raw["data"][0]["embedding"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    if not isinstance(vec, list) or not vec:
        return None
    return [float(x) for x in vec]


async def _qdrant_collection_search(
    collection: str, vector: list[float], limit: int
) -> list[dict[str, Any]]:
    """Vector-search one Qdrant collection (``/collections/{c}/points/search``).

    Maps each hit to the standard ``{content, score, collection, metadata}``
    shape. Returns ``[]`` on any failure — never raises.
    """
    url = f"{_qdrant_url()}/collections/{collection}/points/search"
    payload = {"vector": vector, "limit": limit, "with_payload": True}
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0, read=6.0, write=6.0)
        ) as client:
            resp = await asyncio.wait_for(
                client.post(url, headers={"Content-Type": "application/json"}, json=payload),
                timeout=6.0,
            )
    except (httpx.HTTPError, OSError, ConnectionError, TimeoutError, ValueError):
        return []
    if resp.status_code != 200:
        return []
    try:
        raw = resp.json()
    except ValueError:
        return []
    result = raw.get("result") if isinstance(raw, dict) else None
    points = result if isinstance(result, list) else []
    out: list[dict[str, Any]] = []
    for p in points:
        if not isinstance(p, dict):
            continue
        pl = p.get("payload") or {}
        content = (pl.get("content") or pl.get("text") or "").strip()
        if not content:
            continue
        meta = pl.get("metadata") or {}
        out.append(
            {
                "content": content,
                "score": float(p.get("score") or 0.0),
                "collection": pl.get("collection") or collection,
                "metadata": meta if isinstance(meta, dict) else {},
                "id": pl.get("doc_id") or p.get("id") or "",
            }
        )
    return out


async def _rag_direct_qdrant(
    search_query: str, collections: list[str], limit: int
) -> list[dict[str, Any]]:
    """Embed ONCE and vector-search every collection in the knowledge set.

    Bound to ~8s total (embed ~1-3s + per-collection @0.03s). Runs the
    collection searches concurrently. Returns collected hits (not yet deduped)
    or ``[]`` on failure — never raises.
    """
    vector = await _embed_query(search_query)
    if not vector:
        return []
    tasks = [_qdrant_collection_search(c, vector, limit) for c in collections]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=RAG_LOOKUP_TIMEOUT
        )
    except TimeoutError:
        return []
    out: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, list):
            out.extend(res)
    return out


def _finalize_rag_lookup(
    query: str,
    all_hits: list[dict[str, Any]],
    symbols: list[str],
    addresses: list[str],
    source_label: str = RAG_LOOKUP_SOURCE_LABEL,
) -> ToolResult | None:
    """Dedupe, format, and build the ToolResult from raw hits.

    Returns ``None`` when no usable (deduped) hit survives, so callers can
    fall through to their next RAG lane. Shared by the direct rag-api path and
    the backend-facade path so both cite/format identically.
    """
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for h in sorted(all_hits, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        key = _content_key(h)
        if key and key not in seen:
            seen.add(key)
            deduped.append(h)
    top = deduped[:RAG_LOOKUP_TOP_K]
    if not top:
        return None
    context = _format_context(top)
    per_collection: dict[str, int] = {}
    for h in top:
        col = str(h.get("collection") or "unknown")
        per_collection[col] = per_collection.get(col, 0) + 1
    sources = [
        {"type": "rag", "query": query[:90], "collection": col} for col in sorted(per_collection)
    ]
    known_syms: set[str] = set(symbols)
    known_addrs: set[str] = set(addresses)
    for h in top:
        content = str(h.get("content") or "")
        known_syms.update(extract_symbols(content))
        known_addrs.update(extract_addresses(content))
    return ToolResult(
        tool="rag_lookup",
        ok=True,
        data={"query": query, "context": context, "hits": top, "sources": sources},
        symbols=sorted(known_syms),
        addresses=sorted(known_addrs),
        source=source_label,
    )


async def rag_lookup(*, query: str = "") -> ToolResult:
    """Search the intel DB for knowledge relevant to ``query``.

    Returns a :class:`ToolResult` with:
      * ``data["context"]`` — formatted RAG context block (LLM-facing)
      * ``data["hits"]`` — the real hit dicts (content/score/collection)
      * ``data["sources"]`` — ``[{"type": "rag", "query", "collection"}]``
        citation metadata for the grounding footer
      * ``symbols``/``addresses`` — tokens the RAG content actually mentions
        (feeds the zero-fabrication guardrail's known sets)

    ``ok=False`` when the query is not a knowledge question or the search
    failed / found nothing — never raises.
    """
    if not query.strip():
        return ToolResult(tool="rag_lookup", ok=False, error="query required")
    if not should_rag_lookup(query):
        return ToolResult(tool="rag_lookup", ok=False, error="not a knowledge question")

    addresses = extract_addresses(query)
    symbols = extract_symbols(query)
    collections = list(KNOWLEDGE_COLLECTIONS)
    if addresses:
        collections.extend(RAG_TOKEN_COLLECTIONS)
    search_query = _build_query(query, addresses, symbols)

    # LANE 1 — Qdrant DIRECT (bot=HOST -> 127.0.0.1:6334, embed via local qwen3).
    # Fastest + most reliable: ~0.03s/collection + one embed. The rag-api HTTP
    # wrapper is ~9s and the backend container can't reach it, so Qdrant direct
    # is the primary grounding lane.
    qdrant_hits = await _rag_direct_qdrant(search_query, collections, RAG_LOOKUP_TOP_K)
    if qdrant_hits:
        qdrant_result = _finalize_rag_lookup(
            query,
            qdrant_hits,
            symbols,
            addresses,
            source_label="rag:qdrant:/collections/points/search",
        )
        if qdrant_result is not None:
            return qdrant_result

    # PRIMARY — RugBrain rag-api DIRECT (bot runs on HOST -> 127.0.0.1:8012).
    # The backend facade below is only the fallback: the backend container
    # cannot reach the host-loopback rag-api service, so a direct call is what
    # actually grounds bot chat. rerank=false — the cross-encoder rack is down
    # (as of 2026-08-30) and a missing reranker must never hang the bot.
    direct_hits = await _rag_direct_search_multi(
        search_query, collections, max(RAG_LOOKUP_TOP_K, 8)
    )
    if direct_hits:
        direct_result = _finalize_rag_lookup(
            query, direct_hits, symbols, addresses, source_label="rag:rag-api:/search/multi"
        )
        if direct_result is not None:
            return direct_result

    # Loud backend errors collected per collection — a fully-down RAG backend
    # must surface as "backend unavailable", not as "no knowledge" (silent).
    backend_errors: list[str] = []

    async def _search(collection: str) -> list[dict[str, Any]]:
        try:
            body = await backend_json_post(
                "/api/v1/rag/v2/search",
                json={
                    "query": search_query,
                    "collection": collection,
                    "top_k": RAG_LOOKUP_TOP_K,
                    "min_similarity": 0.0,
                    "filters": {},
                },
                timeout=RAG_LOOKUP_TIMEOUT,
            )
        except BackendApiError as exc:
            # Fail-loud helper, but a RAG miss must not kill the answer —
            # record the loud error (so a fully-down backend is surfaced, not
            # rendered as "no knowledge") and degrade this collection to [].
            backend_errors.append(
                f"{collection}: {exc} (HTTP {exc.status_code})"
                if exc.status_code
                else f"{collection}: {exc}"
            )
            logger.debug("rag_lookup backend miss (%s): %s", exc.status_code, exc)
            return []
        if not body:
            return []
        raw_hits = body.get("hits")
        if not isinstance(raw_hits, list):
            return []
        out: list[dict[str, Any]] = []
        for h in raw_hits:
            if not isinstance(h, dict):
                continue
            item = dict(h)
            item.setdefault("collection", collection)
            out.append(item)
        return out

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(_search(c) for c in collections), return_exceptions=True),
            timeout=RAG_LOOKUP_TIMEOUT,
        )
    except TimeoutError:
        logger.debug("rag_lookup timed out (query=%.60s)", query)
        return ToolResult(tool="rag_lookup", ok=False, error="rag lookup timed out")

    all_hits: list[dict[str, Any]] = []
    for res in results:
        if isinstance(res, list):
            all_hits.extend(res)
    if not all_hits:
        if backend_errors:
            # LOUD: zero hits because the backend search failed — surface the
            # HTTP statuses instead of a silent "no knowledge" (audit §F).
            detail = "; ".join(backend_errors)[:300]
            return ToolResult(
                tool="rag_lookup", ok=False, error=f"rag backend unavailable ({detail})"
            )
        return ToolResult(tool="rag_lookup", ok=False, error="no rag hits")

    # Dedupe across collections by normalized content hash, keep the
    # highest-score copy of each unique snippet.
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for h in sorted(all_hits, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        key = _content_key(h)
        if key and key not in seen:
            seen.add(key)
            deduped.append(h)
    top = deduped[:RAG_LOOKUP_TOP_K]
    if not top:
        return ToolResult(tool="rag_lookup", ok=False, error="no rag hits")

    context = _format_context(top)
    per_collection: dict[str, int] = {}
    for h in top:
        col = str(h.get("collection") or "unknown")
        per_collection[col] = per_collection.get(col, 0) + 1
    sources = [
        {"type": "rag", "query": query[:90], "collection": col} for col in sorted(per_collection)
    ]
    known_syms: set[str] = set()
    known_addrs: set[str] = set()
    for h in top:
        content = str(h.get("content") or "")
        known_syms.update(extract_symbols(content))
        known_addrs.update(extract_addresses(content))
    return ToolResult(
        tool="rag_lookup",
        ok=True,
        data={"query": query, "context": context, "hits": top, "sources": sources},
        symbols=sorted(known_syms),
        addresses=sorted(known_addrs),
        source=RAG_LOOKUP_SOURCE_LABEL,
    )

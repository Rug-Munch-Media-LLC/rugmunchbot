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
import re
from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.guardrails import extract_addresses, extract_symbols
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_post

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
    "defi_exploits",  # historical exploit writeups / PoCs (13.6K pts)
    "contract_security",  # verdict-labeled good/bad contract patterns (221 pts)
    "scam_patterns",  # scam pattern docs + rug_timeline alias (9.9K pts)
]
RAG_TOKEN_COLLECTIONS: list[str] = ["scanner_v2"]  # scan-to-RAG memory (191 pts — token-address questions)

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


def _format_context(hits: list[dict[str, Any]], max_chars: int = RAG_LOOKUP_CONTEXT_MAX_CHARS) -> str:
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

    async def _search(collection: str) -> list[dict[str, Any]]:
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
    sources = [{"type": "rag", "query": query[:90], "collection": col} for col in sorted(per_collection)]
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

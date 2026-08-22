"""Crypto entity extraction + news boosts + graph-result merging."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.rag_pkg._retrieval_scoring import ScoredHit

_ENTITY_CANON: dict[str, str] = {
    "BTC": "bitcoin",
    "BITCOIN": "bitcoin",
    "ETH": "ethereum",
    "ETHEREUM": "ethereum",
    "SOL": "solana",
    "SOLANA": "solana",
    "BNB": "bnb",
    "XRP": "ripple",
    "RIPPLE": "ripple",
    "DOGE": "dogecoin",
    "DOGECOIN": "dogecoin",
    "ADA": "cardano",
    "CARDANO": "cardano",
    "AVAX": "avalanche",
    "AVALANCHE": "avalanche",
    "LINK": "chainlink",
    "CHAINLINK": "chainlink",
    "DOT": "polkadot",
    "POLKADOT": "polkadot",
    "MATIC": "polygon",
    "POLYGON": "polygon",
    "POL": "polygon",
    "UNI": "uniswap",
    "UNISWAP": "uniswap",
    "PEPE": "pepe",
    "SHIB": "shiba",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
    "USDC": "usdc",
    "USDT": "usdt",
    "DAI": "dai",
    "WBTC": "wbtc",
    "ARB": "arbitrum",
    "ARBITRUM": "arbitrum",
    "OP": "optimism",
    "OPTIMISM": "optimism",
    "SUI": "sui",
    "APT": "aptos",
    "APTOS": "aptos",
    "NEAR": "near",
    "ATOM": "cosmos",
    "COSMOS": "cosmos",
    "LTC": "litecoin",
    "LITECOIN": "litecoin",
    "XLM": "stellar",
    "STELLAR": "stellar",
    "TRX": "tron",
    "TRON": "tron",
    "TON": "ton",
    "INJ": "injective",
    "SEI": "sei",
    "TIA": "celestia",
    "JUP": "jupiter",
    "JUPITER": "jupiter",
    "PYTH": "pyth",
    "AAVE": "aave",
    "COMPOUND": "compound",
    "CURVE": "curve",
    "LIDO": "lido",
    "MAKER": "maker",
    "PANCAKESWAP": "pancakeswap",
    "SUSHISWAP": "sushiswap",
    "RAYDIUM": "raydium",
    "ORCA": "orca",
    "METAMASK": "metamask",
    "PHANTOM": "phantom",
    "BINANCE": "binance",
    "COINBASE": "coinbase",
    "KRAKEN": "kraken",
    "BYBIT": "bybit",
    "OKX": "okx",
    "OPENSEA": "opensea",
    "BLUR": "blur",
    "CIRCLE": "circle",
    "TETHER": "tether",
    "BSC": "bsc",
}

_NEWS_COLLECTIONS: frozenset[str] = frozenset({"news", "news_v2"})


def extract_crypto_entities(text: str) -> list[str]:
    """Extract crypto entities (tickers + protocol names) from free text.

    Returns canonical lowercased labels (e.g. "BTC"/"Bitcoin" → ``bitcoin``,
    "UNI"/"Uniswap" → ``uniswap``). Used to cross-reference query mentions
    against article content so news hits about the same token/protocol are
    boosted.
    """
    if not text:
        return []
    found: set[str] = set()
    for m in re.finditer(r"\b([A-Za-z][A-Za-z0-9]{1,15})\b", text):
        canon = _ENTITY_CANON.get(m.group(1).upper())
        if canon:
            found.add(canon)
    return sorted(found)


def _metadata_entities(hit: ScoredHit) -> set[str]:
    """Entity labels already extracted by the ingestion pipeline.

    Prefer ``tokens_mentioned`` / ``chains_mentioned`` metadata (normalized
    at ingest time) and fall back to content extraction when absent.
    """
    meta = hit.metadata or {}
    meta_entities: set[str] = set()
    for key in ("tokens_mentioned", "chains_mentioned", "protocols_mentioned", "entities"):
        val = meta.get(key)
        if isinstance(val, list):
            meta_entities.update(str(v).lower() for v in val)
        elif isinstance(val, str):
            meta_entities.add(val.lower())
    return meta_entities | set(extract_crypto_entities(hit.content or ""))


def _parse_published_at(hit: ScoredHit) -> datetime | None:
    """Parse a news hit's publish timestamp from metadata (best-effort).

    Accepts ISO-8601 strings (with or without tz), bare dates, and unix
    epoch numbers. Returns a tz-aware datetime or None.

    Key aliases are tried in order so docs written by every ingestion path
    get a freshness signal: the crawler (``published_at``), the databus
    news_intel fetchers (``published``), the nightly indexer (``date``),
    and ingest-time fallbacks (``timestamp`` / ``fetched_at``).
    """
    meta = hit.metadata or {}
    raw = (
        meta.get("published_at")
        or meta.get("published")
        or meta.get("date")
        or meta.get("timestamp")
        or meta.get("fetched_at")
    )
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC)
        except (ValueError, OSError, OverflowError):
            return None
    s = str(raw).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def apply_news_boosts(
    hits: list[ScoredHit],
    query: str,
    collection: str | None = None,
    now: datetime | None = None,
) -> list[ScoredHit]:
    """Apply news-specific ranking boosts to news hits.

    Freshness (articles only):
      - < 1h old:   x1.5  (breaking news)
      - < 6h old:   x1.2  (recent)
      - > 72h old:  x0.5  (stale penalty)

    Entity overlap: each crypto entity mentioned in both the query and the
    document adds +10% (``1.0 + 0.1 * overlap``), so news that names the
    same token/protocol the user asked about ranks higher.

    Only news hits (collection ``news``/``news_v2`` or, when ``collection``
    is passed, a news collection) are touched. Mutates ``score`` and
    ``final_score`` in place, records ``metadata["news_boost"]``, and
    re-sorts by ``final_score`` descending. No-op for non-news retrieval.
    """
    if not hits:
        return hits
    if now is None:
        now = datetime.now(UTC)
    if collection is not None and collection not in _NEWS_COLLECTIONS:
        return hits

    query_entities = set(extract_crypto_entities(query))
    changed = False
    # Retrieval (engine hits) leaves ScoredHit.collection="" — treat hits
    # with no collection as members of the news collection being searched.
    news_context = collection in _NEWS_COLLECTIONS if collection is not None else False
    for h in hits:
        hcol = (h.collection or "").lower()
        if hcol not in _NEWS_COLLECTIONS and not (news_context and not hcol):
            continue
        boost = 1.0

        published = _parse_published_at(h)
        if published is not None:
            age_hours = (now - published).total_seconds() / 3600.0
            if age_hours < 1:
                boost *= 1.5  # breaking news
            elif age_hours < 6:
                boost *= 1.2  # recent
            elif age_hours > 72:
                boost *= 0.5  # stale penalty

        overlap = len(query_entities & _metadata_entities(h))
        if overlap > 0:
            boost *= 1.0 + (0.1 * overlap)

        if abs(boost - 1.0) > 1e-9:
            base = float(h.final_score or h.score or 0.0)
            h.score = float(h.score or 0.0) * boost
            h.final_score = base * boost
            h.metadata = {**h.metadata, "news_boost": round(boost, 3)}
            changed = True

    if changed:
        hits.sort(key=lambda s: -(s.final_score or s.score))
    return hits


def _merge_with_graph_results(
    results: list[ScoredHit],
    graph_results: list[dict[str, Any]],
) -> list[ScoredHit]:
    """Merge graph-enriched hits with vector hits using graph-boosted ranking.

    Graph hits come from exact entity matches in the knowledge graph, so
    they receive a boost that ranks them above weak similarity matches while
    remaining below strong ones. Results are deduplicated by ``doc_id``
    (higher score wins) and returned sorted by ``final_score`` descending.
    """
    by_doc: dict[str, ScoredHit] = {}
    for h in results:
        key = h.doc_id or f"vec:{id(h)}"
        if key not in by_doc or h.final_score > by_doc[key].final_score:
            by_doc[key] = h

    for gr in graph_results:
        content = gr.get("content", "")
        if not content:
            continue
        doc_id = gr.get("doc_id") or gr.get("id") or ""
        base = float(gr.get("score", 0.5))
        # Exact entity matches get a boost over weak vector similarity.
        boosted = min(1.0, base * 1.15 + 0.12)
        hit = ScoredHit(
            content=content,
            score=base,
            final_score=boosted,
            metadata={
                "relation": gr.get("relation", ""),
                "node_type": gr.get("neighbor_type", ""),
                "node_id": gr.get("neighbor_id", ""),
            },
            collection="graph",
            doc_id=doc_id,
        )
        key = doc_id or f"kg:{len(by_doc)}"
        if key not in by_doc or boosted > by_doc[key].final_score:
            by_doc[key] = hit

    return sorted(by_doc.values(), key=lambda s: -s.final_score)

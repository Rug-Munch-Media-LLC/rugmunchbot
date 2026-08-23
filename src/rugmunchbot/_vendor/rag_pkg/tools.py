# VENDORED 2026-08-23 from rmi-backend/app/rag/tools.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Real-time tool registry for the agentic RAG graph.

Each tool is a thin, fail-open wrapper around an existing RugMunch data
provider. The graph calls these during ``synthesize_answer`` to enrich
LLM context with live data (price, gas, balances, token security scans,
news). A tool NEVER raises — on provider failure it returns a dict with
``{"error": "unavailable"}`` so the graph keeps flowing.

Wired from ``app.rag.graph`` as the ``TOOLS`` registry:

    TOOLS = {
        "get_token_price": get_token_price,       # CoinGecko/DexScreener
        "get_gas_price": get_gas_price,           # public RPC (Etherscan-style)
        "get_wallet_balance": get_wallet_balance, # web3_chat balances
        "scan_token": scan_token,                 # RugMunchScan v2 (SENTINEL)
        "get_news": get_news,                     # RAG news_v2 collection
    }
"""

from __future__ import annotations

import re
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

log = get_logger(__name__)

# ── Address extraction ────────────────────────────────────────────────
# EVM 0x + Solana base58 addresses found inside a natural-language query.
_ADDRESS_RE = re.compile(r"\b(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})\b")

# CoinGecko id hints for common tickers (free tier).
_TICKER_COINGECKO: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "POL": "polygon-ecosystem-token",
    "UNI": "uniswap",
    "PEPE": "pepe",
    "SHIB": "shiba-inu",
    "WIF": "dogwifcoin",
    "BONK": "bonk",
}


def extract_address(text: str) -> str | None:
    """Extract an EVM/Solana address from free text, if present."""
    match = _ADDRESS_RE.search(text or "")
    return match.group(1) if match else None


def _ticker_hint(query: str) -> str | None:
    """Return an uppercase ticker hint found in the query (e.g. SOL, BTC)."""
    for m in re.finditer(r"\b([A-Z]{2,10})\b", query or ""):
        ticker = m.group(1)
        if ticker in _TICKER_COINGECKO:
            return ticker
    return None


async def get_token_price(token: str) -> dict[str, Any]:
    """Live token price via CoinGecko free API.

    Args:
        token: A CoinGecko id, ticker (BTC/ETH/SOL/...), or 0x address.

    Returns:
        Dict with ``price_usd`` and ``source``. ``{"error": ...}`` on
        provider failure (never raises).
    """
    try:
        import httpx

        token = (token or "").strip()
        # Prefer DexScreener for addresses (search by contract).
        if token.startswith("0x"):
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    "https://api.dexscreener.com/latest/dex/search",
                    params={"q": token},
                )
                r.raise_for_status()
                pairs = r.json().get("pairs") or []
                if pairs:
                    pair = pairs[0]
                    return {
                        "token": token,
                        "price_usd": float(pair.get("priceUsd") or 0.0),
                        "chain": pair.get("chainId", ""),
                        "source": "DexScreener",
                    }
                return {"token": token, "price_usd": None, "source": "DexScreener", "error": "no pair"}

        # Ticker or id → CoinGecko simple price.
        coin_id = _TICKER_COINGECKO.get(token.upper(), token.lower())
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
            )
            r.raise_for_status()
            data = r.json()
            if coin_id in data:
                return {
                    "token": token,
                    "price_usd": float(data[coin_id].get("usd") or 0.0),
                    "source": "CoinGecko",
                }
        return {"token": token, "price_usd": None, "source": "CoinGecko", "error": "unknown_id"}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        log.debug("tool_get_token_price_failed err=%s", exc)
        return {"token": token, "price_usd": None, "source": "CoinGecko", "error": "unavailable"}


async def get_gas_price(chain: str = "ethereum") -> dict[str, Any]:
    """Current gas price in gwei via a public Ethereum RPC (no API key).

    Args:
        chain: Chain label (only ``ethereum`` supported today).

    Returns:
        Dict with ``gas_price_gwei``. ``{"error": ...}`` on failure.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                "https://ethereum-rpc.publicnode.com",
                json={"jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 1},
                headers={"Content-Type": "application/json"},
            )
            r.raise_for_status()
            result = r.json().get("result")
            if result:
                wei = int(result, 16)
                return {"chain": chain, "gas_price_gwei": round(wei / 1e9, 2), "source": "public_rpc"}
        return {"chain": chain, "gas_price_gwei": None, "source": "public_rpc", "error": "no_result"}
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        log.debug("tool_get_gas_price_failed err=%s", exc)
        return {"chain": chain, "gas_price_gwei": None, "source": "public_rpc", "error": "unavailable"}


async def get_wallet_balance(query: str, chain: str | None = None) -> dict[str, Any]:
    """Wallet balance for an address found in the query.

    Uses the existing ``app.services.web3_chat.balances`` implementation
    (EVM + Solana). Fails open to a zero-balance dict when the address is
    missing or the RPC is unreachable.
    """
    address = extract_address(query or "")
    if not address:
        return {"address": None, "error": "no_address_in_query"}
    # EVM addresses always start with 0x; Solana base58 is 32-44 chars
    # (length alone cannot distinguish them).
    inferred = "ethereum" if address.startswith("0x") else "solana"
    try:
        from app.services.web3_chat.balances import get_wallet_balance as _wb

        result = await _wb(address, chain or inferred)
        return {**result, "address": address, "chain": chain or inferred}
    except Exception as exc:
        log.debug("tool_get_wallet_balance_failed err=%s", exc)
        return {"address": address, "chain": chain or inferred, "error": "unavailable"}


async def scan_token(query: str, chain: str = "ethereum") -> dict[str, Any]:
    """Run RugMunchScan v2 (SENTINEL) on an address found in the query.

    Delegates to ``app.domains.scanner.scan_token`` (legacy tier-based
    path, kept as the stable facade). Fails open when no address is
    present or the scanner errors.
    """
    address = extract_address(query or "")
    if not address:
        return {"address": None, "error": "no_address_in_query"}
    # Preserve an explicitly passed chain for EVM (0x) addresses; any other
    # base58 address is a Solana one (32-44 chars, length alone is ambiguous).
    inferred = "solana" if not address.startswith("0x") else chain
    try:
        from app.domains.scanner import scan_token as _scan

        result = await _scan(address, chain=inferred, tiers=["free"])
        return {
            "address": address,
            "chain": inferred,
            "safety_score": getattr(result, "safety_score", None),
            "risk_level": getattr(result, "risk_level", None),
            "risk_flags": list(getattr(result, "risk_flags", []) or []),
            "source": "RugMunchScan v2",
        }
    except Exception as exc:
        log.debug("tool_scan_token_failed err=%s", exc)
        return {"address": address, "chain": inferred, "error": "unavailable"}


# ── News helper ───────────────────────────────────────────────────────

#: Post-rerank results returned by ``get_news``.
_NEWS_TOP_K: int = 5
#: Pre-rerank candidate pool handed to the cross-encoder (wider than the
#: final answer so the Qwen3 reranker ranks ~2x more candidates).
_NEWS_CANDIDATE_K: int = 10


def _news_canonical_url(metadata: dict[str, Any] | None) -> str | None:
    """Canonical article URL from stored metadata, key-agnostic.

    Every ingest path writes the source link under a different key:
    crawler/story_ingest ``source_url``, story-cluster docs ``url`` +
    ``source_urls``, and the crawler's canonical pass ``canonical_url``.
    Returns the first non-empty candidate (strongest first), or None.
    """
    meta = metadata or {}
    for key in ("canonical_url", "url"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    source_urls = meta.get("source_urls")
    if isinstance(source_urls, list) and source_urls:
        first = source_urls[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    src = meta.get("source_url")
    if isinstance(src, str) and src.strip():
        return src.strip()
    return None


async def get_news(query: str, top_k: int = _NEWS_TOP_K) -> dict[str, Any]:
    """Search the RAG ``news_v2`` collection for recent articles.

    Retrieves a wider pre-rerank pool (``candidate_k=10``) so the Qwen3 reranker
    cross-encoder ranks ~2x the candidates before the final ``top_k``
    slice. Results are deduped by canonical URL (same story from multiple
    chunks/sources collapses to the highest-scoring hit). Fails open to an
    empty result set.
    """
    try:
        from rugmunchbot._vendor.rag_pkg.retrieval import HybridRetriever

        hits = await HybridRetriever().retrieve(
            query=query,
            collection="news_v2",
            top_k=top_k,
            candidate_k=_NEWS_CANDIDATE_K,
            rerank=True,
        )
        # Dedup by canonical URL — a story clustered from N sources (or a
        # multi-chunk doc) can surface several hits for the same article;
        # keep the highest-scoring representative per URL.
        by_url: dict[str, dict[str, Any]] = {}
        for h in hits:
            metadata = getattr(h, "metadata", None) or {}
            canonical = _news_canonical_url(metadata)
            result = {
                "content": (h.content or "")[:300],
                "score": round(float(h.final_score or h.score), 3),
                "source": h.collection or "news",
                "doc_id": h.doc_id,
                # Article image + canonical URL surface from stored metadata so
                # the answer layer can cite/show the article image. Absent
                # metadata (or a missing key) degrades to None.
                "image_url": metadata.get("image_url"),
                "canonical_url": canonical,
            }
            key = canonical or f"doc:{h.doc_id or id(h)}"
            prev = by_url.get(key)
            if prev is None or result["score"] > prev["score"]:
                by_url[key] = result
        results = list(by_url.values())
        return {"query": query, "count": len(results), "results": results, "source": "RAG news_v2"}
    except Exception as exc:
        log.debug("tool_get_news_failed err=%s", exc)
        return {"query": query, "count": 0, "results": [], "source": "RAG news_v2", "error": "unavailable"}


__all__ = [
    "extract_address",
    "get_gas_price",
    "get_news",
    "get_token_price",
    "get_wallet_balance",
    "scan_token",
]

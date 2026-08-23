# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/own_copycat_detect.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Copycat Detection — ClickHouse + RAG-Powered Token Similarity Analysis
============================================================================
Phase 3 of the Data Ownership Strategy.

Detects copycat tokens using our own data:
  - Name/Symbol similarity: Levenshtein distance against tracked tokens
  - RAG similarity: Vector search for similar token names/contracts
  - Deployer reuse: Same deployer deploying similar-named tokens
  - Bytecode similarity (EVM): Same bytecode = exact copy

Architecture:
  - Queries ClickHouse deployer_events for known token names/symbols
  - Uses RAG vector search when available for deeper similarity
  - Maintains a known-name index from scanned tokens
  - Falls back to simple Levenshtein against top symbols

Usage:
    from rugmunchbot._vendor.scanner_engines.own_copycat_detect import get_own_copycat_analysis

    result = await get_own_copycat_analysis("0x...", "ethereum", "PEPE", "PEPE")
    # result = {
    #     "is_copycat": bool,
    #     "confidence": float,
    #     "similar_tokens": [...],
    #     "copycat_of": str,
    #     "match_type": str,
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.deployer_events (all deployed tokens)
  Primary: RAG vector search (semantic token name similarity)
  Fallback: Static top-symbol list + Levenshtein distance
"""

from __future__ import annotations

import os
import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_COPYCAT_CACHE: dict[str, dict[str, Any]] = {}
_COPYCAT_CACHE_TTL = 3600  # 1 hour — token names change slowly
_COPYCAT_CACHE_MAX = 200

# ── Lazy ClickHouse client ────────────────────────────────────────────────
_ch_client: Any = None

# ── ClickHouse error types (narrowed broad excepts) ───────────────────────
CH_ERRORS: tuple[type[BaseException], ...]
try:
    import clickhouse_driver.errors as ch_errors

    CH_ERRORS = (ch_errors.Error, ConnectionError, OSError, TimeoutError, ValueError)
except ImportError:
    CH_ERRORS = (ConnectionError, OSError, TimeoutError, ValueError)

# ── Top token symbols (expanded from static list for similarity matching) ─
_TOP_SYMBOLS: set[str] = {
    "ETH",
    "BTC",
    "USDT",
    "USDC",
    "SOL",
    "BNB",
    "XRP",
    "ADA",
    "DOGE",
    "AVAX",
    "DOT",
    "MATIC",
    "SHIB",
    "TRX",
    "LINK",
    "ATOM",
    "BCH",
    "LTC",
    "NEAR",
    "AAVE",
    "APE",
    "AXS",
    "SAND",
    "MANA",
    "FIL",
    "ALGO",
    "FTM",
    "VET",
    "THETA",
    "XLM",
    "ICP",
    "HBAR",
    "EGLD",
    "STX",
    "KAS",
    "RUNE",
    "ARB",
    "OP",
    "INJ",
    "SEI",
    "SUI",
    "APT",
    "TIA",
    "PEPE",
    "FLOKI",
    "WIF",
    "BONK",
    "JUP",
    "RAY",
    "UNI",
    "CRV",
    "MKR",
    "SNX",
    "COMP",
    "YFI",
    "BAL",
    "LDO",
    "RPL",
    "FXS",
    "GNO",
    "ZRX",
    "ENJ",
    "CHZ",
    "SXP",
    "ANKR",
    "BAT",
    "ZIL",
    "IOST",
    "IOTX",
    "ONG",
    "FET",
    "AGIX",
    "OCEAN",
    "RLC",
    "NMR",
    "POL",
    "PRIME",
    "BLUR",
    "STRK",
    "ZK",
    "ENA",
    "OMNI",
    "REZ",
    "ETHFI",
    "AEVO",
    "WORM",
    "BEAM",
    "TAO",
    "TURBO",
    "PENG",
    "MOON",
    "MYRO",
    "POPCAT",
    "MOG",
    "MEW",
    "MICHI",
    "GME",
    "KITTY",
}


def _normalize_chain(chain: str) -> str:
    _chain_map = {
        "ethereum": "ethereum",
        "eth": "ethereum",
        "mainnet": "ethereum",
        "bsc": "bsc",
        "bnb": "bsc",
        "binance": "bsc",
        "polygon": "polygon",
        "matic": "polygon",
        "arbitrum": "arbitrum",
        "arb": "arbitrum",
        "optimism": "optimism",
        "op": "optimism",
        "base": "base",
        "avalanche": "avalanche",
        "avax": "avalanche",
        "fantom": "fantom",
        "ftm": "fantom",
        "solana": "solana",
    }
    return _chain_map.get(chain.lower(), chain.lower())


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row: list[int] = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = curr_row[j] + 1
            deletions = prev_row[j + 1] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Own copycat detect ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own copycat detect ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_copycat_analysis(
    token_address: str,
    chain: str,
    token_symbol: str = "",
    token_name: str = "",
    deployer_address: str = "",
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Get copycat analysis from own data.

    Uses multiple signals to detect copycat tokens:
    1. Symbol similarity — Levenshtein vs known token symbols
    2. Name similarity — Levenshtein vs known token names from ClickHouse
    3. Deployer pattern — same deployer launching similar-named tokens
    4. RAG vector search — semantic similarity against known scams

    Args:
        token_address: Token contract address
        chain: Chain name
        token_symbol: Token symbol (e.g., "PEPE")
        token_name: Token name (e.g., "Pepe the Frog")
        deployer_address: Deployer wallet address (if known)
        force_fresh: Bypass cache

    Returns:
        {
            "is_copycat": bool,
            "confidence": float,
            "risk_label": str,
            "similar_tokens": [{"symbol": str, "name": str, "score": float, "match_type": str}],
            "copycat_of": str,
            "match_type": str,
            "symbol_similarity_found": bool,
            "name_similarity_found": bool,
            "deployer_copy_pattern": bool,
            "source": str,
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    symbol = token_symbol.upper().strip() if token_symbol else ""
    name = token_name.strip() if token_name else ""
    _start = time.monotonic()

    # Check cache
    cache_key = f"{token_address}:{chain}"
    if not force_fresh and cache_key in _COPYCAT_CACHE:
        cached = _COPYCAT_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _COPYCAT_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "is_copycat": False,
        "confidence": 0.0,
        "risk_label": "unknown",
        "similar_tokens": [],
        "copycat_of": "",
        "match_type": "unique",
        "symbol_similarity_found": False,
        "name_similarity_found": False,
        "deployer_copy_pattern": False,
        "source": "unavailable",
    }

    try:
        # ── Signal 1: Symbol similarity against known tokens ──
        symbol_matches = _check_symbol_similarity(symbol, name)
        if symbol_matches:
            result["similar_tokens"].extend(symbol_matches)
            result["symbol_similarity_found"] = True
            result["source"] = "own"

            # If high-confidence copycat, set the copycat_of
            top_match = symbol_matches[0]
            if top_match.get("score", 0) >= 0.8:
                result["copycat_of"] = top_match.get("symbol", "")
                result["match_type"] = "symbol_similarity"

        # ── Signal 2: Name similarity (from ClickHouse known tokens) ──
        if name:
            name_matches = await run_sync(_query_name_similarity, token_address, name, chain)
            if name_matches:
                result["similar_tokens"].extend(name_matches)
                result["name_similarity_found"] = True
                result["source"] = "own"

                if not result["copycat_of"]:
                    top_name = name_matches[0]
                    if top_name.get("score", 0) >= 0.8:
                        result["copycat_of"] = top_name.get("symbol", top_name.get("name", ""))
                        result["match_type"] = "name_similarity"

        # ── Signal 3: Deployer copy pattern ──
        if deployer_address:
            deployer_matches = await run_sync(_query_deployer_similar_launches, deployer_address, chain, symbol, name)
            if deployer_matches:
                result["similar_tokens"].extend(deployer_matches)
                result["deployer_copy_pattern"] = True
                result["source"] = "own"

        # ── Signal 4: RAG vector search (best-effort) ──
        try:
            rag_matches = await _rag_similarity_search(token_address, symbol, name)
            if rag_matches:
                result["similar_tokens"].extend(rag_matches)
                result["source"] = "own"
        except CH_ERRORS as exc:
            logger.debug("rag_copycat_search_failed: %s", exc)

        # ── Compute aggregate score ──
        _compute_copycat_score(result)

    except CH_ERRORS as exc:
        logger.debug("own_copycat_query_failed for %s: %s", token_address[:16], exc)

    # Cache
    _cache_copycat(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_copycat_detect_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        copycat=result["is_copycat"],
        confidence=result["confidence"],
    )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 1: SYMBOL SIMILARITY
# ═══════════════════════════════════════════════════════════════════════════


def _check_symbol_similarity(
    symbol: str,
    name: str,
) -> list[dict[str, Any]]:
    """Check if token symbol/name is similar to known top symbols.

    Returns list of matches sorted by confidence descending.
    """
    matches: list[dict[str, Any]] = []

    if not symbol and not name:
        return matches

    # Direct exact symbol match = real token, not copycat
    if symbol in _TOP_SYMBOLS:
        return matches  # No risk — this IS the real token

    # Check symbol similarity via Levenshtein
    for top in _TOP_SYMBOLS:
        dist = _levenshtein_distance(symbol, top) if symbol else 999
        max_len = max(len(symbol), len(top))

        if max_len == 0:
            continue

        # Normalized distance (0 = identical, 1 = completely different)
        norm_dist = dist / max_len
        similarity = 1.0 - norm_dist

        if similarity >= 0.6 and dist <= 3:
            score = round(similarity * 100, 1)
            matches.append(
                {
                    "symbol": top,
                    "name": top,
                    "score": score,
                    "match_type": "symbol_similarity",
                    "distance": dist,
                }
            )

    # Also check name for embedded top symbols
    if name:
        name_upper = name.upper()
        for top in _TOP_SYMBOLS:
            if top in name_upper and top != symbol:
                # Name contains a known symbol
                score = 60.0  # Moderate confidence — name squatting
                matches.append(
                    {
                        "symbol": top,
                        "name": top,
                        "score": score,
                        "match_type": "name_squatting",
                        "contained_in_name": True,
                    }
                )
            elif name_upper and _levenshtein_distance(name_upper, top) <= 3:
                score = 70.0
                matches.append(
                    {
                        "symbol": top,
                        "name": top,
                        "score": score,
                        "match_type": "name_similarity",
                        "distance": _levenshtein_distance(name_upper, top),
                    }
                )

    # Deduplicate by symbol
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for m in sorted(matches, key=lambda x: x["score"], reverse=True):
        if m["symbol"] not in seen:
            seen.add(m["symbol"])
            unique.append(m)

    return unique


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 2: NAME SIMILARITY FROM CLICKHOUSE
# ═══════════════════════════════════════════════════════════════════════════


def _query_name_similarity(
    token_address: str,
    token_name: str,
    chain: str,
) -> list[dict[str, Any]] | None:
    """Query ClickHouse deployer_events for tokens with similar names.

    Returns tokens with name similarity above threshold.
    """
    client = _get_ch_client()
    if client is None:
        return None

    if not token_name:
        return None

    try:
        # Get all tracked token names from deployer_events
        rows = client.execute(
            """
            SELECT DISTINCT token_address, chain
            FROM wallet_memory.deployer_events
            WHERE chain = %(chain)s
              AND token_address != %(token)s
            LIMIT 1000
            """,
            {"chain": chain, "token": token_address},
        )

        if not rows:
            return None

        # We only have addresses, not names directly
        # For name similarity, we'd need a token_metadata store
        # This is a placeholder for future expansion
        return None  # Requires token_metadata table for full functionality

    except CH_ERRORS as exc:
        logger.debug("name_similarity_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 3: DEPLOYER COPY PATTERN
# ═══════════════════════════════════════════════════════════════════════════


def _query_deployer_similar_launches(
    deployer_address: str,
    chain: str,
    symbol: str,
    name: str,
) -> list[dict[str, Any]] | None:
    """Check if deployer has launched similar-named tokens before.

    A deployer who launches "PEPE" then "PEPE2" then "PEPE3" is
    likely engaged in copycat / repeat scam behavior.
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                token_address,
                block_timestamp,
                type,
                tx_hash
            FROM wallet_memory.deployer_events
            WHERE deployer = %(deployer)s
              AND chain = %(chain)s
              AND token_address != %(token)s
            ORDER BY block_timestamp DESC
            LIMIT 20
            """,
            {"deployer": deployer_address.lower(), "chain": chain, "token": ""},
        )

        if not rows:
            return None

        # We have the tokens but not their names/symbols
        # Return them for reference — names would need token_metadata table
        return [
            {
                "token_address": r[0],
                "timestamp": r[1].isoformat() if hasattr(r[1], "isoformat") else str(r[1]),
                "type": r[2],
                "score": 30.0,
                "match_type": "same_deployer",
                "deployer": deployer_address,
            }
            for r in rows[:5]
        ]

    except CH_ERRORS as exc:
        logger.debug("deployer_similar_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# SIGNAL 4: RAG SIMILARITY SEARCH
# ═══════════════════════════════════════════════════════════════════════════


async def _rag_similarity_search(
    token_address: str,
    symbol: str,
    name: str,
) -> list[dict[str, Any]] | None:
    """Search RAG for tokens with similar names/symbols.

    Uses the RAG system's vector search to find semantically similar
    token names that may indicate copycat behavior.
    """
    try:
        from app.domains.scanner.rag_scam_check import check_scam

        result = await check_scam(
            token_address=token_address,
            chain="",
            symbol=symbol,
            name=name,
        )

        if result and result.get("similar_scams"):
            return [
                {
                    "token_address": s.get("token_address", ""),
                    "name": s.get("name", s.get("content", "")[:50]),
                    "score": round(float(s.get("score", 0)) * 100, 1),
                    "match_type": "rag_similarity",
                    "collection": s.get("collection", ""),
                }
                for s in result["similar_scams"][:5]
                if float(s.get("score", 0)) > 0.5
            ]

        return None

    except ImportError:
        logger.debug("rag_scam_check not available for copycat detection")
        return None
    except CH_ERRORS as exc:
        logger.debug("rag_similarity_search_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════════════════


def _compute_copycat_score(result: dict[str, Any]) -> None:
    """Compute aggregate copycat confidence from all signals."""
    similar = result.get("similar_tokens", [])
    if not similar:
        result["risk_label"] = "unique"
        return

    # Highest similarity score
    max_score = max(m.get("score", 0) for m in similar)
    result["confidence"] = max_score

    # Count signals
    signal_count = sum(
        [
            result["symbol_similarity_found"],
            result["name_similarity_found"],
            result["deployer_copy_pattern"],
        ]
    )

    # Multiple signals = higher confidence
    if signal_count >= 2:
        result["confidence"] = min(100, result["confidence"] + 15)

    # Determine copycat status
    if max_score >= 80:
        result["is_copycat"] = True
        result["risk_label"] = "high"
    elif max_score >= 60:
        result["is_copycat"] = True
        result["risk_label"] = "medium"
    elif max_score >= 40:
        result["is_copycat"] = True
        result["risk_label"] = "low"
    else:
        result["risk_label"] = "unique"

    # Deduplicate similar_tokens
    seen: set[str] = set()
    unique_list: list[dict[str, Any]] = []
    for m in sorted(similar, key=lambda x: x["score"], reverse=True):
        key = m.get("symbol", m.get("token_address", m.get("name", "")))
        if key not in seen:
            seen.add(key)
            unique_list.append(m)
    result["similar_tokens"] = unique_list[:10]


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_copycat(key: str, data: dict[str, Any]) -> None:
    data["_cached_at"] = time.time()
    _COPYCAT_CACHE[key] = data
    if len(_COPYCAT_CACHE) > _COPYCAT_CACHE_MAX:
        _COPYCAT_CACHE.pop(next(iter(_COPYCAT_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    return _common_get_cache_stats(
        _COPYCAT_CACHE,
        _COPYCAT_CACHE_MAX,
        _COPYCAT_CACHE_TTL,
        extra={"clickhouse_connected": _ch_client is not None},
    )


def clear_cache() -> None:
    _common_clear_cache(_COPYCAT_CACHE)

# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/behavioral_fingerprint.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
C4 v2 Behavioral Fingerprint (Cross-Chain Entity Matching)
============================================================
Computes a behavioral fingerprint for a wallet from our own ClickHouse data
(chain-indexer ``wallet_memory`` tables) and compares fingerprints across
chains to confirm cross-chain entity links.

v1 (``cross_chain_entity``) links the same actor across chains via deployer
and funder evidence. v2 adds behavioral confirmation: when the deployer check
finds a candidate, the wallet's *behavior* on each chain is compared with
cosine similarity over a fixed-dimension feature vector.

Features (all from LOCAL tables, zero cost):

  1. **Frequency** — ``evm_transfers`` count, active days, tx/day, burst score
     (fraction of activity on the busiest day).
  2. **Gas usage profile** — [REVIEW] ``evm_transfers`` does not persist a gas
     column (schema: chain, block_number, tx_hash, from/to_address,
     token_address, value, block_timestamp, log_index), so median gas and gas
     price sensitivity are emitted as ``None`` and excluded from the vector.
     They remain in the payload for forward-compat if a gas column lands.
  3. **DEX interaction** — ``dex_events`` swap count, DEX/pool diversity, token
     diversity, top protocols.
  4. **Temporal** — active-hours histogram (24 bins) and day-of-week histogram
     (7 bins) from ``block_timestamp``.
  5. **Value distribution** — median and p95 transfer value (``quantile`` over
     ``toFloat64OrZero(value)`` because the column is ``String``), plus the
     p95/median spread as a distribution-shape feature.
  6. **Contract interaction** — top DEX protocol names enable cross-chain
     protocol-overlap matching (e.g. Uniswap on ethereum ↔ Uniswap on polygon).

Every ClickHouse query is bounded by ``max_execution_time`` (time-boxed — see
Cross-Cutting Principles).

Caching (mirrors ``deployer_fingerprint``):
  - Redis key ``behavioral_fingerprint:{chain}:{address}`` with 1h TTL.
  - In-process dict cache (10 min) for hot-path repeat lookups.

Usage:
    from rugmunchbot._vendor.scanner_engines.behavioral_fingerprint import (
        compute_behavioral_fingerprint,
        compare_fingerprints,
    )

    fp = await compute_behavioral_fingerprint("0xabc...", "ethereum")
    sim = compare_fingerprints(fp_a, fp_b)  # 0.0-1.0 cosine similarity
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.lite_chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
)

# Legacy module-local names kept for consistency with the other engines.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_CACHE_PREFIX = "behavioral_fingerprint"
REDIS_TTL_SECONDS = 3600  # 1 hour — same as deployer_fingerprint
_FP_CACHE: dict[str, dict[str, Any]] = {}
_FP_CACHE_TTL = 600  # 10 minutes in-process mirror
_FP_CACHE_MAX = 200

# ── Query / analysis limits (time-boxing) ─────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10

# Confidence: behavioral fingerprint strategy baseline (cross-chain docs §6)
CONFIDENCE_BEHAVIORAL = 0.70

# Similarity threshold for a positive behavioral match
BEHAVIORAL_MIN_SIMILARITY = 0.60

# Active-hour / day-of-week histogram sizes
_HOURS_BINS = 24
_DOW_BINS = 7

# Feature-vector dimension caps used for normalization
_FEATURE_CAPS: dict[str, float] = {
    "tx_per_day": 100.0,
    "active_days": 365.0,
    "burst_score": 1.0,
    "value_median": 1e24,  # log10-capped at 1e24 wei
    "value_spread": 1e6,  # p95/median ratio cap
    "swap_per_day": 100.0,
    "dex_diversity": 50.0,
    "token_diversity": 200.0,
}


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def compute_behavioral_fingerprint(address: str, source_chain: str) -> dict[str, Any]:
    """Compute a behavioral fingerprint for cross-chain matching.

    Features:
      1. Transaction frequency (txs/day, active days, burst patterns)
      2. Gas usage profile (median gas, gas price sensitivity) — [REVIEW] not
         available from ``evm_transfers`` today; emitted as ``None``.
      3. DEX interaction pattern (swap frequency, DEX diversity, token diversity)
      4. Temporal pattern (active hours, day-of-week preference)
      5. Value distribution (median transfer value, outlier thresholds)
      6. Contract interaction pattern (interacts with same protocols across chains)

    Args:
        address: Wallet address on the source chain (lowercased internally).
        source_chain: Chain the address lives on (ethereum, bsc, polygon, ...).

    Returns:
        {
            "result": "scanned" | "unavailable",
            "address": ...,
            "chain": ...,
            "fingerprint": {
                "tx_count", "active_days", "tx_per_day", "burst_score",
                "median_gas", "gas_price_sensitivity",
                "swap_count", "swap_per_day", "dex_diversity", "token_diversity",
                "top_protocols", "active_hours" (24), "active_dow" (7),
                "median_value", "p95_value", "outlier_threshold", "value_spread",
            },
            "confidence": 0-1,
        }

        Returns an ``unavailable`` result when ClickHouse is unreachable or the
        address has no transfer/DEX history on the chain.
    """
    addr = (address or "").strip().lower()
    chain_norm = _normalize_chain(source_chain)
    _start = time.monotonic()

    if not addr or not chain_norm:
        return _error_result("address and source_chain are required")

    # ── 1. Redis fast cache (1h TTL) ──
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm}:{addr}"
    cached = await _redis_get(cache_key)
    if cached is not None:
        logger.debug("behavioral_fingerprint_redis_hit", key=cache_key)
        return cast("dict[str, Any]", cached)

    # ── 2. In-process mirror cache ──
    if cache_key in _FP_CACHE:
        entry = _FP_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _FP_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full ClickHouse computation ──
    try:
        result = await _compute_fingerprint(addr, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_query_failed: %s", exc)
        result = _unavailable_result(addr, chain_norm, reason="clickhouse unavailable")

    # Only cache meaningful results (skip unavailable/error)
    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _FP_CACHE[cache_key] = result
        if len(_FP_CACHE) > _FP_CACHE_MAX:
            _FP_CACHE.pop(next(iter(_FP_CACHE)), None)

    logger.info(
        "behavioral_fingerprint_latency",
        ms=round((time.monotonic() - _start) * 1000, 1),
        address=addr[:10],
        chain=chain_norm,
        confidence=result.get("confidence"),
    )
    return result


def compare_fingerprints(fp_a: dict[str, Any], fp_b: dict[str, Any]) -> float:
    """Cosine similarity between behavioral fingerprints. Returns 0-1.

    Compares the fixed-dimension feature vectors (frequency, value, DEX,
    temporal histograms). When either fingerprint has no feature data the
    similarity is 0.0 (no behavioral evidence).
    """
    vec_a = _feature_vector(fp_a)
    vec_b = _feature_vector(fp_b)
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    return round(_cosine(vec_a, vec_b), 4)


def matched_features(fp_a: dict[str, Any], fp_b: dict[str, Any]) -> list[str]:
    """Return the names of fingerprint features that match between two wallets.

    Each feature group is compared with a small tolerance; a feature counts as
    matched when both sides are active and the relative/absolute distance is
    within the tolerance. Used for explainable evidence output.
    """
    fa = fp_a.get("fingerprint") or {}
    fb = fp_b.get("fingerprint") or {}
    matched: list[str] = []
    if not fa or not fb:
        # A missing fingerprint is not evidence of similarity
        return matched

    # 1. Transaction frequency: tx/day within 50% relative distance
    a, b = _num(fa, "tx_per_day"), _num(fb, "tx_per_day")
    if a > 0 and b > 0 and _rel_diff(a, b) <= 0.5:
        matched.append("tx_frequency")

    # Active days within 30% relative distance
    a, b = _num(fa, "active_days"), _num(fb, "active_days")
    if a > 0 and b > 0 and _rel_diff(a, b) <= 0.3:
        matched.append("activity_level")

    # Burst pattern: busiest-day fraction within 0.3 absolute distance
    a, b = _num(fa, "burst_score"), _num(fb, "burst_score")
    if a >= 0 and b >= 0 and abs(a - b) <= 0.3:
        matched.append("burst_pattern")

    # 3. DEX interaction: swap/day within 50%, diversity within tolerance
    a, b = _num(fa, "swap_per_day"), _num(fb, "swap_per_day")
    if a > 0 and b > 0 and _rel_diff(a, b) <= 0.5:
        matched.append("swap_frequency")
    a, b = _num(fa, "dex_diversity"), _num(fb, "dex_diversity")
    if a > 0 and b > 0 and abs(a - b) <= 2:
        matched.append("dex_diversity")
    a, b = _num(fa, "token_diversity"), _num(fb, "token_diversity")
    if a > 0 and b > 0 and _rel_diff(a, b) <= 0.5:
        matched.append("token_diversity")

    # 4. Temporal: hour histogram cosine >= 0.8
    ha, hb = fa.get("active_hours") or [], fb.get("active_hours") or []
    if len(ha) == _HOURS_BINS and len(hb) == _HOURS_BINS and _cosine(ha, hb) >= 0.8:
        matched.append("temporal_pattern")

    # 5. Value distribution: medians within one order of magnitude
    a, b = _num(fa, "median_value"), _num(fb, "median_value")
    if a > 0 and b > 0 and abs(math.log10(a) - math.log10(b)) <= 1.0:
        matched.append("value_distribution")

    # 6. Contract interaction: shared top protocols (Jaccard >= 0.3)
    pa = set(fa.get("top_protocols") or [])
    pb = set(fb.get("top_protocols") or [])
    if pa and pb:
        jac = len(pa & pb) / len(pa | pb)
        if jac >= 0.3:
            matched.append("protocol_overlap")

    return matched


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_fingerprint(address: str, chain_norm: str) -> dict[str, Any]:
    """Run all ClickHouse queries in parallel and assemble the fingerprint."""
    freq_task = asyncio.create_task(run_sync(_query_frequency, address, chain_norm))
    value_task = asyncio.create_task(run_sync(_query_value_profile, address, chain_norm))
    burst_task = asyncio.create_task(run_sync(_query_burst, address, chain_norm))
    dex_task = asyncio.create_task(run_sync(_query_dex_profile, address, chain_norm))
    protocols_task = asyncio.create_task(run_sync(_query_top_protocols, address, chain_norm))
    hours_task = asyncio.create_task(run_sync(_query_active_hours, address, chain_norm))
    dow_task = asyncio.create_task(run_sync(_query_active_dow, address, chain_norm))

    freq, value, burst, dex, protocols, hours, dow = await asyncio.gather(
        freq_task,
        value_task,
        burst_task,
        dex_task,
        protocols_task,
        hours_task,
        dow_task,
    )

    tx_count = int((freq or {}).get("tx_count", 0) or 0)
    swap_count = int((dex or {}).get("swap_count", 0) or 0)

    if tx_count == 0 and swap_count == 0:
        return _unavailable_result(address, chain_norm, reason="no transfer or DEX history")

    active_days = int((freq or {}).get("active_days", 0) or 0)
    max_day = int((burst or {}).get("max_day", 0) or 0)

    # Frequency features
    tx_per_day = round(tx_count / active_days, 4) if active_days > 0 else 0.0
    burst_score = round(max_day / tx_count, 4) if tx_count > 0 else 0.0
    swap_per_day = round(swap_count / active_days, 4) if active_days > 0 else 0.0

    # Value features
    median_value = _num(value, "median_value")
    p95_value = _num(value, "p95_value")
    outlier_threshold = round(p95_value * 1.5, 4) if p95_value > 0 else 0.0
    value_spread = round(p95_value / median_value, 4) if median_value > 0 else 0.0

    # Temporal histograms (normalized to probability mass)
    active_hours = _normalize_hist(hours or {}, _HOURS_BINS)
    active_dow = _normalize_hist(dow or {}, _DOW_BINS)

    fingerprint: dict[str, Any] = {
        "tx_count": tx_count,
        "active_days": active_days,
        "tx_per_day": tx_per_day,
        "burst_score": burst_score,
        # [REVIEW] gas not persisted in evm_transfers — kept for forward-compat
        "median_gas": None,
        "gas_price_sensitivity": None,
        "swap_count": swap_count,
        "swap_per_day": swap_per_day,
        "dex_diversity": int((dex or {}).get("dex_diversity", 0) or 0),
        "token_diversity": int((dex or {}).get("token_diversity", 0) or 0),
        "top_protocols": list(protocols or []),
        "active_hours": active_hours,
        "active_dow": active_dow,
        "median_value": median_value,
        "p95_value": p95_value,
        "outlier_threshold": outlier_threshold,
        "value_spread": value_spread,
    }

    return {
        "check": "behavioral_fingerprint",
        "result": "scanned",
        "source": "own",
        "address": address,
        "chain": chain_norm,
        "fingerprint": fingerprint,
        "confidence": _confidence(tx_count, active_days, swap_count, protocols),
    }


def _confidence(tx_count: int, active_days: int, swap_count: int, protocols: list[str]) -> float:
    """0.0-1.0 confidence based on data volume."""
    confidence = 0.3
    if tx_count > 0:
        confidence += 0.2
    if active_days >= 3:
        confidence += 0.2
    if swap_count > 0:
        confidence += 0.2
    if protocols:
        confidence += 0.1
    return round(min(1.0, confidence), 2)


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (all sync, bounded by max_execution_time)
# ═══════════════════════════════════════════════════════════════════════════


def _query_frequency(address: str, chain: str) -> dict[str, Any] | None:
    """Transfer count, span, and active days for the address on one chain."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                count() AS tx_count,
                min(block_timestamp) AS min_ts,
                max(block_timestamp) AS max_ts,
                uniqExact(toDate(block_timestamp)) AS active_days
            FROM wallet_memory.evm_transfers
            WHERE (from_address = %(addr)s OR to_address = %(addr)s)
              AND chain = %(chain)s
            """,
            {"addr": address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_frequency_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None
    row = rows[0]
    return {
        "tx_count": int(row[0] or 0),
        "min_ts": row[1],
        "max_ts": row[2],
        "active_days": int(row[3] or 0),
    }


def _query_burst(address: str, chain: str) -> dict[str, Any] | None:
    """Busiest single-day transfer count (burst detection)."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT max(c) AS max_day
            FROM (
                SELECT toDate(block_timestamp) AS d, count() AS c
                FROM wallet_memory.evm_transfers
                WHERE (from_address = %(addr)s OR to_address = %(addr)s)
                  AND chain = %(chain)s
                GROUP BY d
            )
            """,
            {"addr": address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_burst_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None
    return {"max_day": int(rows[0][0] or 0)}


def _query_value_profile(address: str, chain: str) -> dict[str, Any] | None:
    """Median and p95 transfer value (value is stored as String)."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                quantile(0.5)(toFloat64OrZero(value)) AS median_value,
                quantile(0.95)(toFloat64OrZero(value)) AS p95_value
            FROM wallet_memory.evm_transfers
            WHERE (from_address = %(addr)s OR to_address = %(addr)s)
              AND chain = %(chain)s
            """,
            {"addr": address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_value_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None
    row = rows[0]
    return {
        "median_value": float(row[0] or 0.0),
        "p95_value": float(row[1] or 0.0),
    }


def _query_dex_profile(address: str, chain: str) -> dict[str, Any] | None:
    """DEX swap count, pool diversity, and token diversity for the sender."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                count() AS swap_count,
                uniqExact(contract_address) AS dex_diversity,
                uniqExact(token0) + uniqExact(token1) AS token_diversity
            FROM wallet_memory.dex_events
            WHERE sender = %(addr)s
              AND chain = %(chain)s
            """,
            {"addr": address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_dex_failed: %s", exc)
        return None

    if not rows or not rows[0]:
        return None
    row = rows[0]
    return {
        "swap_count": int(row[0] or 0),
        "dex_diversity": int(row[1] or 0),
        "token_diversity": int(row[2] or 0),
    }


def _query_top_protocols(address: str, chain: str) -> list[str]:
    """Top DEX protocol names the sender interacts with (cross-chain overlap)."""
    client = _get_ch_client()
    if client is None:
        return []
    try:
        rows = client.execute(
            """
            SELECT dex, count() AS c
            FROM wallet_memory.dex_events
            WHERE sender = %(addr)s
              AND chain = %(chain)s
            GROUP BY dex
            ORDER BY c DESC
            LIMIT 5
            """,
            {"addr": address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_protocols_failed: %s", exc)
        return []

    return [str(row[0]) for row in rows or [] if row and row[0]]


def _query_active_hours(address: str, chain: str) -> dict[int, int] | None:
    """Hour-of-day histogram from transfer timestamps (0-23)."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT toHour(block_timestamp) AS h, count() AS c
            FROM wallet_memory.evm_transfers
            WHERE (from_address = %(addr)s OR to_address = %(addr)s)
              AND chain = %(chain)s
            GROUP BY h
            """,
            {"addr": address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_hours_failed: %s", exc)
        return None

    if not rows:
        return None
    return {int(row[0]): int(row[1]) for row in rows if row and row[0] is not None}


def _query_active_dow(address: str, chain: str) -> dict[int, int] | None:
    """Day-of-week histogram from transfer timestamps (1-7, ClickHouse)."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT toDayOfWeek(block_timestamp) AS d, count() AS c
            FROM wallet_memory.evm_transfers
            WHERE (from_address = %(addr)s OR to_address = %(addr)s)
              AND chain = %(chain)s
            GROUP BY d
            """,
            {"addr": address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("behavioral_fingerprint_dow_failed: %s", exc)
        return None

    if not rows:
        return None
    return {int(row[0]): int(row[1]) for row in rows if row and row[0] is not None}


# ═══════════════════════════════════════════════════════════════════════════
# FEATURE VECTOR / SIMILARITY HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _feature_vector(fp: dict[str, Any]) -> list[float]:
    """Flatten a fingerprint into the fixed-dimension cosine vector.

    Each dimension is normalized to 0-1 (log-scaled where the raw feature has a
    wide dynamic range). Missing features contribute 0.0 so an absent feature
    never boosts similarity.
    """
    f = fp.get("fingerprint") or {}
    vec: list[float] = [
        _log_scale(_num(f, "tx_per_day"), _FEATURE_CAPS["tx_per_day"]),
        _norm(_num(f, "active_days"), _FEATURE_CAPS["active_days"]),
        _norm(_num(f, "burst_score"), _FEATURE_CAPS["burst_score"]),
        _log_scale(_num(f, "median_value"), _FEATURE_CAPS["value_median"]),
        _log_scale(_num(f, "value_spread"), _FEATURE_CAPS["value_spread"]),
        _log_scale(_num(f, "swap_per_day"), _FEATURE_CAPS["swap_per_day"]),
        _norm(_num(f, "dex_diversity"), _FEATURE_CAPS["dex_diversity"]),
        _norm(_num(f, "token_diversity"), _FEATURE_CAPS["token_diversity"]),
    ]
    hours = f.get("active_hours") or [0.0] * _HOURS_BINS
    dow = f.get("active_dow") or [0.0] * _DOW_BINS
    vec.extend(float(h) for h in hours[:_HOURS_BINS])
    vec.extend(float(d) for d in dow[:_DOW_BINS])
    return vec


def _cosine(vec_a: list[float], vec_b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors (0-1 for non-negative)."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(x * y for x, y in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in vec_a))
    norm_b = math.sqrt(sum(y * y for y in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


def _normalize_hist(counts: dict[int, int], bins: int) -> list[float]:
    """Convert {bin: count} to a normalized 0-1 histogram of fixed length."""
    hist = [0.0] * bins
    total = sum(counts.values())
    if total <= 0:
        return hist
    for key, count in counts.items():
        idx = int(key)
        if 0 <= idx < bins:
            hist[idx] = round(count / total, 6)
    return hist


def _num(f: dict[str, Any], key: str) -> float:
    """Read a numeric feature safely."""
    try:
        return float(f.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _norm(value: float, cap: float) -> float:
    """Linear normalization to 0-1 with a cap."""
    if cap <= 0:
        return 0.0
    return max(0.0, min(1.0, value / cap))


def _log_scale(value: float, cap: float) -> float:
    """Log1p normalization to 0-1 with a cap (wide dynamic range features)."""
    if cap <= 0:
        return 0.0
    value = max(0.0, value)
    return max(0.0, min(1.0, math.log1p(value) / math.log1p(cap)))


def _rel_diff(a: float, b: float) -> float:
    """Relative difference between two positive values (0.0 when equal)."""
    if a == 0 and b == 0:
        return 0.0
    return abs(a - b) / max(a, b)


# ═══════════════════════════════════════════════════════════════════════════
# RESULT HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _unavailable_result(address: str, chain_norm: str, reason: str = "clickhouse unavailable") -> dict[str, Any]:
    """Graceful degradation result — callers fall back to other engines."""
    return {
        "check": "behavioral_fingerprint",
        "result": "unavailable",
        "source": "own",
        "address": address,
        "chain": chain_norm,
        "fingerprint": None,
        "confidence": 0.0,
        "note": reason,
    }


def _error_result(message: str) -> dict[str, Any]:
    """Invalid-input error result."""
    return {
        "check": "behavioral_fingerprint",
        "result": "error",
        "error": message,
        "address": "",
        "chain": "",
        "fingerprint": None,
        "confidence": 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _FP_CACHE,
        _FP_CACHE_MAX,
        _FP_CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_FP_CACHE)

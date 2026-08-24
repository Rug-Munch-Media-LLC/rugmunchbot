# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_io.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""ClickHouse fetch / cache IO layer for funding_analysis (degodding wave 6).

The data-fetching and storage machinery from ``funding_analysis.py`` lives
here: the in-process cache, Redis wrappers, query/analysis limits, the
``_compute_*`` ClickHouse orchestrators, and the ``get_cache_stats`` /
``clear_cache`` monitoring API. ``funding_analysis.py`` re-exports every
name so existing ``fa._compute_*`` / ``fa._CACHE`` references keep working.

Imports are one-directional: this module imports pure helpers
(``_funding_helpers`` / ``_funding_queries`` / ``_funding_results`` /
``_scoring``) and never imports ``funding_analysis``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.lite_chain_indexer import (
    _get_ch_client,
    _new_ch_client,
)
from rugmunchbot._vendor.scanner_engines._common import (
    _query_batch_labels,
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
    to_epoch,
    to_iso,
)
from rugmunchbot._vendor.scanner_engines._funding_helpers import (
    _OVERLAP_SCAM_PCT,
    _RISK_DESCRIPTIONS,
    _assess_funding_risk,
    _build_top_funders,
    _classify_freshness,
    _classify_funder,
    _compute_freshness_score,
)
from rugmunchbot._vendor.scanner_engines._funding_queries import (
    _merge_first_tx_rows,
    _query_first_funders,
    _query_funder_tx_counts,
    _query_holder_first_tx_in,
    _query_holder_first_tx_out,
    _query_holder_set,
    _query_token_coverage_start,
    _query_token_scam_status,
    _query_top_holders,
    _query_wallet_first_funder,
    _query_wallet_stats,
)
from rugmunchbot._vendor.scanner_engines._funding_results import (
    _freshness_unavailable,
    _overlap_unavailable,
    _unavailable_result,
    _wallet_metrics_unavailable,
)
from rugmunchbot._vendor.scanner_engines._scoring import (
    _FRESH_WALLET_AGE_DAYS,
    _aggregate_wallet_metrics,
)

# Legacy module-local names kept for consistency with the other engines.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_CACHE_PREFIX = "funding_analysis"
REDIS_TTL_SECONDS = 600  # 10 minutes — holder funding data changes slowly
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 300  # 5 minutes in-process mirror
_CACHE_MAX = 200

# ── Query / analysis limits (time-boxing) ─────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 20
_MAX_HOLDERS = 50  # top-N holders analyzed for funding sources
_MAX_SCAM_TOKENS = 5  # known-scam tokens cross-referenced for overlap

# ── Classification thresholds ─────────────────────────────────────────────
# Pure-logic thresholds (funder classification, freshness scoring, risk
# bands, scam-overlap) now live in ``engines/_funding_helpers.py``.
_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_DEAD_ADDR = "0x000000000000000000000000000000000000dead"


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 1
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_funding(token_address: str, chain: str, holders: list[str] | None) -> dict[str, Any]:
    """Resolve holders, trace funders, classify, and aggregate percentages."""
    # ── 1. Resolve top holders ──
    holder_addrs: list[str] = []
    if holders:
        holder_addrs = [h.lower().strip() for h in holders if h and h.lower().strip() not in (_ZERO_ADDR, _DEAD_ADDR)]
        holder_addrs = list(dict.fromkeys(holder_addrs))[:_MAX_HOLDERS]

    if not holder_addrs:
        holder_rows = await run_sync(_query_top_holders, token_address, chain, _MAX_HOLDERS)
        if holder_rows:
            holder_addrs = [str(r[0] or "").lower() for r in holder_rows if r and r[0]]
            holder_addrs = [h for h in holder_addrs if h not in (_ZERO_ADDR, _DEAD_ADDR)][:_MAX_HOLDERS]

    if not holder_addrs:
        return _unavailable_result(token_address, chain, reason="no holder data")

    # ── 2. First incoming transfer per holder (funding source) ──
    first_in_rows = await run_sync(_query_first_funders, holder_addrs, chain)
    funder_by_holder: dict[str, dict[str, Any]] = {}
    for row in first_in_rows or []:
        h_addr = str(row[0] or "").lower()
        if not h_addr:
            continue
        funder_by_holder[h_addr] = {
            "funder": str(row[1] or "").lower() or None,
            "first_tx": str(row[2] or ""),
            "first_ts": row[3],
        }

    funders = sorted({info["funder"] for info in funder_by_holder.values() if info.get("funder")})

    # ── 3. Funder labels (wallet_labels + static known_entities) ──
    labels_map: dict[str, list[dict[str, Any]]] = {}
    if funders:
        label_rows = await run_sync(_query_batch_labels, funders)
        for row in label_rows or []:
            addr = str(row[0] or "").lower()
            if not addr:
                continue
            labels_map.setdefault(addr, []).append(
                {
                    "label_name": row[1],
                    "label_category": row[2],
                    "label_subtype": row[3],
                    "source": row[4],
                    "is_sanctioned": bool(row[5]) if len(row) > 5 else False,
                }
            )

    # ── 4. Funder total tx counts (<=10 txs => fresh wallet) ──
    tx_count_by_funder: dict[str, int] = {}
    if funders:
        tx_rows = await run_sync(_query_funder_tx_counts, funders, chain)
        for row in tx_rows or []:
            addr = str(row[0] or "").lower()
            if addr:
                tx_count_by_funder[addr] = int(row[1] or 0)

    # ── 5. Classify each holder ──
    holder_details: list[dict[str, Any]] = []
    funding_type_counts: dict[str, int] = {"exchange_funded": 0, "fresh_wallet": 0, "mixer_funded": 0, "other": 0}
    for h_addr in holder_addrs:
        info = funder_by_holder.get(h_addr, {})
        funder = info.get("funder")
        if not funder:
            funding_type = "other"
            funder_label: str | None = None
        else:
            funding_type, funder_label = _classify_funder(
                funder,
                labels_map.get(funder, []),
                tx_count_by_funder.get(funder, 0),
                chain,
            )
        funding_type_counts[funding_type] += 1
        holder_details.append(
            {
                "holder": h_addr,
                "funder": funder,
                "funder_label": funder_label,
                "funding_type": funding_type,
                "first_funding_tx": info.get("first_tx"),
                "first_funding_ts": to_iso(info.get("first_ts")),
            }
        )

    analyzed = len(holder_addrs)
    exchange_pct = round(funding_type_counts["exchange_funded"] / analyzed * 100, 1) if analyzed else 0.0
    fresh_pct = round(funding_type_counts["fresh_wallet"] / analyzed * 100, 1) if analyzed else 0.0
    mixer_pct = round(funding_type_counts["mixer_funded"] / analyzed * 100, 1) if analyzed else 0.0
    other_pct = round(100.0 - exchange_pct - fresh_pct - mixer_pct, 1)

    risk = _assess_funding_risk(exchange_pct, fresh_pct, mixer_pct)
    top_funders = _build_top_funders(holder_details, labels_map, chain)

    return {
        "check": "funding_sources",
        "result": "scanned",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "holders_analyzed": analyzed,
        "exchange_funded_pct": exchange_pct,
        "fresh_wallet_pct": fresh_pct,
        "mixer_funded_pct": mixer_pct,
        "unknown_pct": other_pct,
        "top_funders": top_funders,
        "holder_details": holder_details,
        "risk_assessment": risk,
        "risk_description": _RISK_DESCRIPTIONS[risk],
        "note": "first incoming transfer per holder classified by funder label/tx-count",
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 4 (token-level wallet freshness metrics)
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_wallet_metrics(
    token_address: str,
    chain: str,
    holders: list[str] | None,
) -> dict[str, Any]:
    """Resolve holders, fetch wallet age + first funder, aggregate metrics."""
    # ── 1. Resolve top holders ──
    holder_addrs: list[str] = []
    if holders:
        holder_addrs = [h.lower().strip() for h in holders if h and h.lower().strip() not in (_ZERO_ADDR, _DEAD_ADDR)]
        holder_addrs = list(dict.fromkeys(holder_addrs))[:_MAX_HOLDERS]

    if not holder_addrs:
        holder_rows = await run_sync(_query_top_holders, token_address, chain, _MAX_HOLDERS)
        if holder_rows:
            holder_addrs = [str(r[0] or "").lower() for r in holder_rows if r and r[0]]
            holder_addrs = [h for h in holder_addrs if h not in (_ZERO_ADDR, _DEAD_ADDR)][:_MAX_HOLDERS]

    if not holder_addrs:
        return _wallet_metrics_unavailable(token_address, chain, reason="no holder data")

    # ── 1b. Indexer coverage window for this token (freshness honesty) ──
    # The first-tx lookup below can only see the indexer's transfer window.
    # If the token launched before the window started (old token, late
    # indexing), every holder looks freshly-born — an artifact we expose to
    # callers instead of letting them print bogus fresh/brand-new %.
    coverage_start = await run_sync(_query_token_coverage_start, token_address, chain)
    # Defensive: run_sync is a generic wrapper and the query helper may be
    # substituted in tests/mocks — only treat datetime-like values as valid
    # coverage anchors; anything else degrades to "no coverage window".
    if coverage_start is not None and hasattr(coverage_start, "timestamp"):
        coverage_start_ts = to_iso(coverage_start)
        coverage_days = round((time.time() - coverage_start.timestamp()) / 86400.0, 1)
    else:
        coverage_start_ts = None
        coverage_days = None

    # ── 2. First-ever on-chain tx per holder (wallet age + first-tx token) ──
    # Both directions run concurrently on DEDICATED connections (the shared
    # client rejects simultaneous queries) — each is a chain-partition scan
    # with its own timeout budget (the combined UNION ALL query used to
    # exceed the 10s ClickHouse timeout on busy chains).
    first_tx_in, first_tx_out = await asyncio.gather(
        run_sync(_query_holder_first_tx_in, holder_addrs, chain, _new_ch_client()),
        run_sync(_query_holder_first_tx_out, holder_addrs, chain, _new_ch_client()),
    )
    first_tx_rows = _merge_first_tx_rows(first_tx_in, first_tx_out)
    first_tx_by_holder: dict[str, dict[str, Any]] = {}
    for row in first_tx_rows or []:
        addr = str(row[0] or "").lower()
        if not addr:
            continue
        first_tx_by_holder[addr] = {
            "first_ts": row[1],
            "first_token": str(row[2] or "").lower() or None,
            "first_tx_hash": str(row[3] or "") or None,
        }

    # Without wallet-age data we cannot compute freshness — degrade gracefully.
    if not first_tx_by_holder:
        return _wallet_metrics_unavailable(token_address, chain, reason="no wallet age data")

    # ── 3. First funder per holder (CEX funding source) ──
    first_in_rows = await run_sync(_query_first_funders, holder_addrs, chain)
    funder_by_holder: dict[str, dict[str, Any]] = {}
    for row in first_in_rows or []:
        addr = str(row[0] or "").lower()
        if not addr:
            continue
        funder_by_holder[addr] = {
            "funder": str(row[1] or "").lower() or None,
            "first_tx": str(row[2] or ""),
            "first_ts": row[3],
        }

    # ── 4. Funder labels + tx counts (CEX / mixer / fresh classification) ──
    funders = sorted({info["funder"] for info in funder_by_holder.values() if info.get("funder")})
    labels_map: dict[str, list[dict[str, Any]]] = {}
    if funders:
        label_rows = await run_sync(_query_batch_labels, funders)
        for row in label_rows or []:
            addr = str(row[0] or "").lower()
            if not addr:
                continue
            labels_map.setdefault(addr, []).append(
                {
                    "label_name": row[1],
                    "label_category": row[2],
                    "label_subtype": row[3],
                    "source": row[4],
                    "is_sanctioned": bool(row[5]) if len(row) > 5 else False,
                }
            )

    tx_count_by_funder: dict[str, int] = {}
    if funders:
        tx_rows = await run_sync(_query_funder_tx_counts, funders, chain)
        for row in tx_rows or []:
            addr = str(row[0] or "").lower()
            if addr:
                tx_count_by_funder[addr] = int(row[1] or 0)

    # ── 5. Per-holder detail + aggregation ──
    holder_details: list[dict[str, Any]] = []
    now_ts = time.time()
    for h_addr in holder_addrs:
        fh = first_tx_by_holder.get(h_addr, {})
        fi = funder_by_holder.get(h_addr, {})
        funder = fi.get("funder")
        if funder:
            funding_type, funder_label = _classify_funder(
                funder,
                labels_map.get(funder, []),
                tx_count_by_funder.get(funder, 0),
                chain,
            )
        else:
            funding_type, funder_label = "other", None

        first_ts = to_epoch(fh.get("first_ts"))
        age_days = max(0.0, (now_ts - first_ts) / 86400.0) if first_ts is not None else 0.0

        holder_details.append(
            {
                "holder": h_addr,
                "age_days": round(age_days, 1),
                "fresh": age_days < _FRESH_WALLET_AGE_DAYS,
                "first_seen_at": to_iso(first_ts) if first_ts is not None else None,
                "first_tx_token": fh.get("first_token"),
                "first_tx_is_token": bool(fh.get("first_token") and fh["first_token"] == token_address),
                "funder": funder,
                "funder_label": funder_label,
                "funding_type": funding_type,
            }
        )

    return _aggregate_wallet_metrics(
        holder_details,
        token_address,
        chain,
        coverage_start_ts=coverage_start_ts,
        coverage_days=coverage_days,
    )


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 2
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_freshness(address: str, chain: str) -> dict[str, Any]:
    """Query wallet stats + first funder, then score freshness."""
    stats_rows = await run_sync(_query_wallet_stats, address, chain)
    funder_row = await run_sync(_query_wallet_first_funder, address, chain)

    first_seen_ts: float | None = None
    total_txs = 0
    if stats_rows:
        row = stats_rows[0]
        first_seen_ts = to_epoch(row[0]) if len(row) > 0 else None
        total_txs = int(row[1] or 0) if len(row) > 1 else 0

    if first_seen_ts is None and total_txs == 0:
        return _freshness_unavailable(address, chain, reason="no on-chain activity found")

    funder: str | None = None
    first_funder_label: str | None = None
    if funder_row:
        funder = str(funder_row[0] or "").lower() or None
        if funder:
            label_rows = await run_sync(_query_batch_labels, [funder])
            labels: list[dict[str, Any]] = []
            for row in label_rows or []:
                addr = str(row[0] or "").lower()
                if addr == funder:
                    labels.append(
                        {
                            "label_name": row[1],
                            "label_category": row[2],
                            "label_subtype": row[3],
                            "source": row[4],
                            "is_sanctioned": bool(row[5]) if len(row) > 5 else False,
                        }
                    )
            funder_type, first_funder_label = _classify_funder(funder, labels, total_txs, chain)
            funded_by_cex = funder_type == "exchange_funded"
        else:
            funded_by_cex = False
    else:
        funded_by_cex = False

    age_days = 0.0
    if first_seen_ts is not None:
        age_days = max(0.0, (time.time() - first_seen_ts) / 86400.0)

    score = _compute_freshness_score(age_days, total_txs, funded_by_cex)
    classification = _classify_freshness(score)

    return {
        "check": "wallet_freshness",
        "result": "scanned",
        "source": "own",
        "address": address,
        "chain": chain,
        "age_days": round(age_days, 1),
        "total_txs": total_txs,
        "funded_by_cex": funded_by_cex,
        "first_funder": funder,
        "first_funder_label": first_funder_label,
        "first_seen_at": to_iso(first_seen_ts) if first_seen_ts is not None else None,
        "freshness_score": score,
        "classification": classification,
        "note": "age, tx count, and CEX funding weighted into a 0-100 freshness score",
    }


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION — Metric 3
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_overlap(token_a: str, token_b: str, chain: str) -> dict[str, Any]:
    """Compute holder-set overlap between two tokens."""
    holders_a_task = asyncio.create_task(run_sync(_query_holder_set, token_a, chain))
    holders_b_task = asyncio.create_task(run_sync(_query_holder_set, token_b, chain))
    scam_task = asyncio.create_task(run_sync(_query_token_scam_status, token_b, chain))

    holders_a_rows, holders_b_rows, token_b_scam = await asyncio.gather(
        holders_a_task,
        holders_b_task,
        scam_task,
    )

    holders_a = {str(r[0] or "").lower() for r in holders_a_rows or [] if r and r[0]}
    holders_b = {str(r[0] or "").lower() for r in holders_b_rows or [] if r and r[0]}

    if not holders_a:
        return _overlap_unavailable(token_a, token_b, chain, reason="no holder data for token_a")

    common = holders_a & holders_b
    overlap_pct = round(len(common) / len(holders_a) * 100, 1)

    risk = (
        "HIGH"
        if (token_b_scam and overlap_pct > _OVERLAP_SCAM_PCT)
        else ("MEDIUM" if overlap_pct > _OVERLAP_SCAM_PCT else "LOW")
    )

    return {
        "check": "holder_overlap",
        "result": "scanned",
        "source": "own",
        "token_a": token_a,
        "token_b": token_b,
        "chain": chain,
        "holders_a": len(holders_a),
        "holders_b": len(holders_b),
        "common_holders": len(common),
        "overlap_pct": overlap_pct,
        "token_b_is_scam": bool(token_b_scam),
        "risk_assessment": risk,
        "note": "overlap = |holders_A ∩ holders_B| / |holders_A|; >30% with known scam = red flag",
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _CACHE,
        _CACHE_MAX,
        _CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_CACHE)

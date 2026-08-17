# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/holder_clusters.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
C2 — BubbleMaps-style Holder Clustering (v1)
=============================================
Phase 5 RugMunchScan capability — holder concentration + cluster map from
our own ClickHouse data (BubbleMaps replacement).

Builds a BubbleMaps-style visualization payload:
  - ``holders``     — holder set with net balances, first-seen, labels, cluster ids
  - ``metrics``     — top-10 share, Gini, HHI, risk class, total holders
  - ``clusters``    — holder clusters from three detection passes
  - ``concentration`` — LOW / MEDIUM / HIGH / EXTREME risk level

Cluster detection passes (spec C2):
  1. **Shared funder** — holders whose first in-transfer came from the same
     funder address (``evm_transfers``). Groups >2 flagged. DEX pool contracts,
     the token contract, and burn/mint addresses are excluded as funders.
  2. **Same-block buy** — holders whose first DEX buy landed in the same block
     (``dex_events`` first swap per address, grouped by block). Simultaneous
     entries (>=3 buyers) flagged as bundle/sybil signals.
  3. **Bundle** — bundle-buy groups surfaced from ``own_bundle_detect`` output
     (atomic same-block buys), cross-referenced to actual holder addresses.

Holder labeling:
  - Batch query ``wallet_labels`` (label_category / label_subtype / label_name)
  - Classify each holder: CEX, DEX, bridge, mixer, team, deployer, sniper,
    bot, whale, normal (deployer-link + first-block timing used for team/sniper).

Access path (electrs-inspired):
  - Holder balances are computed from ``evm_transfers`` deltas — sum(in) minus
    sum(out) per address — no separate balance table is stored.
  - Results cached in Redis: 5 min TTL for active tokens (any transfer in the
    last 24h), 1h TTL for inactive tokens. In-process dict mirror as fallback.

Every ClickHouse query is bounded by ``max_execution_time`` (time-boxed — see
Cross-Cutting Principles). Clusters carry evidence (funder tx hashes, block
numbers, bundle tx hashes).

Usage:
    from rugmunchbot._vendor.scanner_engines.holder_clusters import analyze_holder_clusters

    result = await analyze_holder_clusters("0x...", "ethereum")
    # result = {
    #     "holders": [{"address", "balance", "balance_pct", "label",
    #                  "cluster_id", "first_seen"}],
    #     "metrics": {"top10_pct", "gini", "hhi", "class", "total_holders"},
    #     "clusters": [{"id", "addresses", "funder", "first_block",
    #                   "reason", "confidence"}],
    #     "concentration": {"level": "LOW"|"MEDIUM"|"HIGH"|"EXTREME", ...},
    # }

Data Sources:
  Primary: wallet_memory.evm_transfers (balances, first-seen, funders)
  Primary: wallet_memory.dex_events (first-buy blocks, pool contracts)
  Primary: wallet_memory.wallet_labels (39.4M entity labels)
  Primary: wallet_memory.deployer_events (deployer + deploy block for
           team/sniper classification)

Degodding split (2026-08-13): the pure cluster-pass / labeling / metric
helpers moved to ``engines/_holder_clusters_helpers.py`` and are
re-exported here. The ClickHouse query layer stays in this module (tests
patch ``holder_clusters._get_ch_client`` end-to-end).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.lite_chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.scanner_engines._common import (
    _query_batch_labels,
    _query_contract_addresses,
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
    to_epoch,
    to_iso,
)
from rugmunchbot._vendor.scanner_engines._holder_clusters_helpers import (  # noqa: F401
    # Re-exported for tests that reference them as hc.<name>
    # (see tests/unit/domain/scanner/test_holder_clusters.py).
    _BOT_KEYWORDS,
    _BRIDGE_KEYWORDS,
    _BUNDLE_CLUSTER_MIN,
    _CEX_KEYWORDS,
    _CONCENTRATION_DESCRIPTIONS,
    _DEAD_ADDR,
    _DEX_KEYWORDS,
    _FUNDER_CLUSTER_MIN,
    _MIXER_KEYWORDS,
    _SAME_BLOCK_CLUSTER_MIN,
    _SNIPER_WINDOW_BLOCKS,
    _SNIPER_WINDOW_SECONDS,
    _TEAM_KEYWORDS,
    _WHALE_PCT_THRESHOLD,
    _ZERO_ADDR,
    _build_bundle_clusters,
    _build_funder_clusters,
    _build_same_block_clusters,
    _classify_concentration,
    _classify_holder,
    _compute_gini,
    _compute_hhi,
)

# Legacy module-local names kept for consistency with the other engines.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_CACHE_PREFIX = "holder_clusters"
REDIS_TTL_ACTIVE = 300  # 5 minutes — active tokens (transfers in last 24h)
REDIS_TTL_INACTIVE = 3600  # 1 hour — inactive tokens
_CLUSTER_CACHE: dict[str, dict[str, Any]] = {}
_CLUSTER_CACHE_TTL = 120  # 2 minutes in-process mirror
_CLUSTER_CACHE_MAX = 200

# ── Query / analysis limits (time-boxing) ─────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10
_MAX_HOLDERS = 100  # balance query limit (top-N by net balance)
_TOP_HOLDERS_OUTPUT = 50  # holders emitted in the payload
_MAX_BUYERS = 500  # first-buy rows considered for same-block clustering
# Row budget for the raw transfer/dex scans feeding the balance, first-in and
# first-buy aggregations. Large tokens can have 1B+ evm_transfers rows; an
# unbounded scan times out under max_execution_time. Each inner branch is
# ordered by recency and capped here so the GROUP BY sees a bounded window
# (most-recent transfers dominate current balances). Approximate for very
# large tokens — intentional degradation over timeouts.
_MAX_TRANSFERS_SCAN = 200_000

# Cluster / classification thresholds live in ``_holder_clusters_helpers.py``
# (pure logic, re-exported above).


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def analyze_holder_clusters(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """BubbleMaps-style holder clustering from own ClickHouse data.

    Computes holder balances from ``evm_transfers`` deltas (electrs-inspired
    access path — no separate balance table), runs the three cluster detection
    passes, batch-labels holders via ``wallet_labels``, and emits a
    BubbleMaps-style visualization payload.

    Args:
        token_address: Token contract address.
        chain: Chain name (ethereum, bsc, arbitrum, ...).
        force_fresh: Bypass all caches.

    Returns:
        {
            "holders": [{address, balance, balance_pct, label, cluster_id, first_seen}],
            "metrics": {top10_pct, gini, hhi, class, total_holders},
            "clusters": [{id, addresses, funder, first_block, reason, confidence}],
            "concentration": {level, top10_pct, top3_pct, hhi, description},
        }
    """
    token_address = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _error_result(token_address, chain_norm, "token_address and chain are required")

    # ── 1. Redis fast cache ──
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm}:{token_address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("holder_clusters_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)

    # ── 2. In-process mirror cache ──
    if not force_fresh and cache_key in _CLUSTER_CACHE:
        entry = _CLUSTER_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CLUSTER_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full ClickHouse computation ──
    try:
        result = await _compute_clusters(token_address, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("holder_clusters_query_failed: %s", exc)
        result = _unavailable_result(token_address, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned" and result.get("holders"):
        ttl = REDIS_TTL_ACTIVE if result.get("active_24h") else REDIS_TTL_INACTIVE
        await _redis_set(cache_key, result, ttl)
        result["_cached_at"] = time.time()
        _CLUSTER_CACHE[cache_key] = result
        if len(_CLUSTER_CACHE) > _CLUSTER_CACHE_MAX:
            _CLUSTER_CACHE.pop(next(iter(_CLUSTER_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    metrics = result.get("metrics", {})
    logger.info(
        "holder_clusters_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        holders=metrics.get("total_holders", 0),
        clusters=len(result.get("clusters", [])),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# COMPUTATION
# ═══════════════════════════════════════════════════════════════════════════


async def _compute_clusters(token_address: str, chain: str) -> dict[str, Any]:
    """Run the balance / first-seen / first-buy queries and assemble the payload."""
    # ── Batch 1: independent ClickHouse queries (parallel) ──
    balances_task = asyncio.create_task(run_sync(_query_holder_balances, token_address, chain))
    first_in_task = asyncio.create_task(run_sync(_query_first_in_transfers, token_address, chain))
    first_buy_task = asyncio.create_task(run_sync(_query_first_buy_blocks, token_address, chain))
    deploy_task = asyncio.create_task(run_sync(_query_deploy_info, token_address, chain))
    active_task = asyncio.create_task(run_sync(_query_is_active, token_address, chain))
    holder_task = asyncio.create_task(_get_holder_analysis_gini(token_address, chain))

    (balance_rows, first_in_rows, first_buy_rows, deploy_info, is_active, holder_gini) = await asyncio.gather(
        balances_task,
        first_in_task,
        first_buy_task,
        deploy_task,
        active_task,
        holder_task,
    )

    if not balance_rows:
        return _unavailable_result(token_address, chain, reason="no holder balance data")

    balances: dict[str, float] = {}
    for row in balance_rows:
        addr = str(row[0] or "").lower()
        if addr:
            balances[addr] = float(row[1] or 0)

    if not balances:
        return _unavailable_result(token_address, chain, reason="no positive holder balances")

    total_tracked = sum(balances.values())

    # ── First in-transfer map (funder + evidence) ──
    first_in: dict[str, dict[str, Any]] = {}
    for row in first_in_rows or []:
        h_addr = str(row[0] or "").lower()
        if not h_addr:
            continue
        first_in[h_addr] = {
            "funder": str(row[1] or "").lower() or None,
            "first_tx": str(row[2] or ""),
            "first_block": row[3],
            "first_seen": row[4],
        }

    # ── First-buy map (dex_events) ──
    first_buy: dict[str, dict[str, Any]] = {}
    for row in first_buy_rows or []:
        buyer = str(row[0] or "").lower()
        if not buyer:
            continue
        first_buy[buyer] = {
            "first_block": row[1],
            "first_tx": str(row[2] or ""),
        }

    deployer = (deploy_info or {}).get("deployer")
    deploy_block = (deploy_info or {}).get("block_number")
    deploy_ts = (deploy_info or {}).get("deploy_ts")

    # ── Batch 2: pool-contract detection + labels (needs holder list) ──
    holder_addrs = list(balances.keys())
    pool_task = asyncio.create_task(run_sync(_query_contract_addresses, holder_addrs, chain))
    label_task = asyncio.create_task(run_sync(_query_batch_labels, holder_addrs))
    pool_rows: Any
    label_rows: Any
    pool_rows, label_rows = await asyncio.gather(pool_task, label_task, return_exceptions=True)

    pool_addresses: set[str] = set()
    if not isinstance(pool_rows, BaseException) and pool_rows:
        pool_addresses = {str(r[0] or "").lower() for r in pool_rows if r and r[0]}

    labels_map: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(label_rows, BaseException) and label_rows:
        for row in label_rows:
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

    # ── Holder output ──
    holders_out: list[dict[str, Any]] = []
    pct_by_addr: dict[str, float] = {}
    sorted_balances = sorted(balances.items(), key=lambda kv: kv[1], reverse=True)
    for addr, bal in sorted_balances[:_TOP_HOLDERS_OUTPUT]:
        pct = (bal / total_tracked * 100) if total_tracked > 0 else 0.0
        pct_by_addr[addr] = round(pct, 2)
        info = first_in.get(addr, {})
        first_seen = info.get("first_seen")
        # Block-based sniper detection in _classify_holder needs first_block
        # (within 2 blocks of deploy). Coerce to int for arithmetic safety.
        first_block = info.get("first_block")
        if first_block is not None:
            try:
                first_block = int(first_block)
            except (TypeError, ValueError):
                first_block = None
        holders_out.append(
            {
                "address": addr,
                "balance": bal,
                "balance_pct": round(pct, 2),
                "label": _classify_holder(
                    {
                        "address": addr,
                        "balance_pct": pct,
                        "labels": labels_map.get(addr, []),
                        "funder": info.get("funder"),
                        "first_seen": to_epoch(first_seen),
                        "first_block": first_block,
                    },
                    {"deployer": deployer, "deploy_block": deploy_block, "deploy_ts": deploy_ts},
                ),
                "cluster_id": None,
                "first_seen": to_iso(first_seen),
            }
        )

    # ── Cluster detection passes ──
    clusters = _build_funder_clusters(first_in, deployer, pool_addresses, token_address)
    clusters.extend(_build_same_block_clusters(first_buy))
    bundle_result = await _get_bundle_analysis(token_address, chain)
    clusters.extend(_build_bundle_clusters(bundle_result, first_in))

    # Attach combined_pct from holder balances (addresses present in the top-N output).
    for cluster in clusters:
        cluster["combined_pct"] = round(sum(pct_by_addr.get(a, 0.0) for a in cluster.get("addresses", [])), 2)

    # Assign the strongest cluster_id to each holder for display.
    best_cluster: dict[str, dict[str, Any]] = {}
    for cluster in clusters:
        cid = str(cluster.get("id") or "")
        conf = float(cluster.get("confidence") or 0)
        for addr in cluster.get("addresses", []):
            if addr not in best_cluster or conf > float(best_cluster[addr].get("confidence") or 0):
                best_cluster[addr] = {"id": cid, "confidence": conf}
    for holder_rec in holders_out:
        match = best_cluster.get(str(holder_rec["address"]))
        if match:
            holder_rec["cluster_id"] = match["id"]

    # ── Concentration metrics ──
    top10_pct = sum(pct_by_addr.get(a, 0.0) for a, _ in sorted_balances[:10])
    top3_pct = sum(pct_by_addr.get(a, 0.0) for a, _ in sorted_balances[:3])
    top10_pct = round(top10_pct, 2)
    top3_pct = round(top3_pct, 2)
    level = _classify_concentration(top10_pct)
    hhi = _compute_hhi(list(balances.values()))
    gini = holder_gini if holder_gini and holder_gini > 0 else _compute_gini(list(balances.values()))
    gini = round(gini, 4)

    confidence = round(min(0.95, 0.45 + 0.02 * len(clusters) + (0.25 if len(balances) >= 10 else 0.0)), 2)

    return {
        "check": "holder_clusters",
        "result": "scanned",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "holders": holders_out,
        "metrics": {
            "top10_pct": top10_pct,
            "gini": gini,
            "hhi": hhi,
            "class": level,
            "total_holders": len(balances),
        },
        "clusters": clusters,
        "concentration": {
            "level": level,
            "top10_pct": top10_pct,
            "top3_pct": top3_pct,
            "hhi": hhi,
            "description": _CONCENTRATION_DESCRIPTIONS[level],
        },
        "confidence": confidence,
        "deployer": deployer,
        "active_24h": bool(is_active),
        "note": "holder balances computed from evm_transfers deltas (sum in - sum out)",
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (all sync, bounded by max_execution_time)
# ═══════════════════════════════════════════════════════════════════════════


def _query_holder_balances(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """Net balance per address from evm_transfers deltas (electrs-inspired).

    The inner UNION ALL branches are each ordered by recency and capped at
    ``_MAX_TRANSFERS_SCAN`` rows so huge token tables (1B+ rows) return a
    bounded top-N result instead of timing out under max_execution_time.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                addr,
                sum(balance) AS balance
            FROM (
                (
                    SELECT
                        to_address AS addr,
                        toFloat64OrZero(value) AS balance
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                    ORDER BY block_timestamp DESC
                    LIMIT %(scan_limit)s
                )
                UNION ALL
                (
                    SELECT
                        from_address AS addr,
                        -toFloat64OrZero(value) AS balance
                    FROM wallet_memory.evm_transfers
                    WHERE token_address = %(token)s
                      AND chain = %(chain)s
                      AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                    ORDER BY block_timestamp DESC
                    LIMIT %(scan_limit)s
                )
            )
            WHERE addr != ''
            GROUP BY addr
            HAVING balance > 0
            ORDER BY balance DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": _MAX_HOLDERS, "scan_limit": _MAX_TRANSFERS_SCAN},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("holder_balances_query_failed: %s", exc)
        return None


def _query_first_in_transfers(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """First in-transfer per holder — funder, evidence tx, block, timestamp.

    The scan is bounded to the ``_MAX_TRANSFERS_SCAN`` most-recent in-transfers
    so large token tables do not time out under max_execution_time.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                holder,
                argMin(from_address, block_timestamp) AS funder,
                argMin(tx_hash, block_timestamp) AS first_tx,
                argMin(block_number, block_timestamp) AS first_block,
                min(block_timestamp) AS first_seen
            FROM (
                SELECT
                    to_address AS holder,
                    from_address,
                    tx_hash,
                    block_number,
                    block_timestamp
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s
                  AND chain = %(chain)s
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                              '0x000000000000000000000000000000000000dead')
                ORDER BY block_timestamp DESC
                LIMIT %(scan_limit)s
            )
            GROUP BY holder
            """,
            {"token": token_address, "chain": chain, "scan_limit": _MAX_TRANSFERS_SCAN},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("first_in_transfers_query_failed: %s", exc)
        return None


def _query_first_buy_blocks(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """First DEX buy per address from dex_events (recipient preferred over sender).

    The dex_events scan is bounded to the ``_MAX_TRANSFERS_SCAN`` most-recent
    swap rows so large token tables do not time out under max_execution_time.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                buyer,
                min(block_number) AS first_block,
                argMin(tx_hash, block_timestamp) AS first_tx
            FROM (
                SELECT
                    if(recipient NOT IN ('', '0x0000000000000000000000000000000000000000'),
                       recipient, sender) AS buyer,
                    block_number,
                    block_timestamp,
                    tx_hash
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND (token0 = %(token)s OR token1 = %(token)s)
                  AND type = 'swap'
                  AND sender NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
                ORDER BY block_timestamp DESC
                LIMIT %(scan_limit)s
            )
            WHERE buyer != ''
            GROUP BY buyer
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": _MAX_BUYERS, "scan_limit": _MAX_TRANSFERS_SCAN},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("first_buy_blocks_query_failed: %s", exc)
        return None


def _query_deploy_info(token_address: str, chain: str) -> dict[str, Any] | None:
    """Deployer + deploy block/ts from deployer_events (earliest row)."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                argMin(deployer, block_timestamp) AS deployer,
                argMin(block_number, block_timestamp) AS block_number,
                argMin(tx_hash, block_timestamp) AS tx_hash,
                min(block_timestamp) AS deploy_ts
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s
              AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        if not rows or not rows[0] or not rows[0][0]:
            return None
        row = rows[0]
        return {
            "deployer": str(row[0] or "").lower(),
            "block_number": row[1],
            "tx_hash": str(row[2] or ""),
            "deploy_ts": to_epoch(row[3]),
        }
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("deploy_info_query_failed: %s", exc)
        return None


def _query_is_active(token_address: str, chain: str) -> bool:
    """True when the token had any transfer in the last 24h (drives Redis TTL).

    Uses ``LIMIT 1`` as an existence probe so large token tables short-circuit
    instead of counting every recent row under max_execution_time.
    """
    client = _get_ch_client()
    if client is None:
        return False
    try:
        rows = client.execute(
            """
            SELECT 1
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_timestamp >= now() - INTERVAL 24 HOUR
            LIMIT 1
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return bool(rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("is_active_query_failed: %s", exc)
        return False


async def _get_holder_analysis_gini(token_address: str, chain: str) -> float:
    """Reuse the canonical holder engine for the Gini coefficient (spec C2).

    Routes through ``unified._data_sources.get_token_holders`` — the single
    shared entry point for holder data (canonical ``own_holder_analysis``).
    """
    try:
        from rugmunchbot._vendor.unified_scanner._data_sources import get_token_holders

        result = await get_token_holders(token_address, chain)
        if result and result.get("source") == "own":
            return float(result.get("gini_coefficient") or 0.0)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("holder_analysis_gini_failed: %s", exc)
    return 0.0


async def _get_bundle_analysis(token_address: str, chain: str) -> dict[str, Any] | None:
    """Reuse own_bundle_detect output for the bundle pass."""
    try:
        from rugmunchbot._vendor.scanner_engines.own_bundle_detect import get_own_bundle_analysis

        result = await get_own_bundle_analysis(token_address, chain)
        if result and result.get("source") == "own":
            return result
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("bundle_analysis_failed: %s", exc)
    return None


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _unavailable_result(token_address: str, chain_norm: str, reason: str = "clickhouse unavailable") -> dict[str, Any]:
    """Graceful degradation result."""
    return {
        "check": "holder_clusters",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "holders": [],
        "metrics": {"top10_pct": 0.0, "gini": 0.0, "hhi": 0.0, "class": "LOW", "total_holders": 0},
        "clusters": [],
        "concentration": {
            "level": "LOW",
            "top10_pct": 0.0,
            "top3_pct": 0.0,
            "hhi": 0.0,
            "description": _CONCENTRATION_DESCRIPTIONS["LOW"],
        },
        "confidence": 0.0,
        "deployer": None,
        "active_24h": False,
        "note": reason,
    }


def _error_result(token_address: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result."""
    return {
        "check": "holder_clusters",
        "result": "error",
        "error": message,
        "token_address": token_address,
        "chain": chain_norm,
        "holders": [],
        "metrics": {"top10_pct": 0.0, "gini": 0.0, "hhi": 0.0, "class": "LOW", "total_holders": 0},
        "clusters": [],
        "concentration": {
            "level": "LOW",
            "top10_pct": 0.0,
            "top3_pct": 0.0,
            "hhi": 0.0,
            "description": _CONCENTRATION_DESCRIPTIONS["LOW"],
        },
        "confidence": 0.0,
        "deployer": None,
        "active_24h": False,
        "note": message,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _CLUSTER_CACHE,
        _CLUSTER_CACHE_MAX,
        _CLUSTER_CACHE_TTL,
        extra={
            "redis_ttl_active_seconds": REDIS_TTL_ACTIVE,
            "redis_ttl_inactive_seconds": REDIS_TTL_INACTIVE,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_CLUSTER_CACHE)

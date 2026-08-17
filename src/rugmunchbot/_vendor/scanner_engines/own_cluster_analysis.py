# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_cluster_analysis.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Own Cluster Analysis — Funding-Source Clusters + Coordinated-Buy Detection
===========================================================================
Upgrades CLUSTER detection beyond simple bundle wallet counts. Two signals
that are stronger than a raw count:

  * FUNDING-SOURCE CLUSTER — first (launch-window) buyers are grouped by
    their FIRST FUNDER (the wallet that funded them before they bought).
    One funder → N wallets is a stronger coordinated signal than a simple
    bundle count, because it shows the wallets were armed by one operator.
  * COORDINATED-BUY detection — first-buyers who buy in the SAME BLOCK and
    share a funder (or a funding chain, where the funder is itself a launch
    buyer) get a ``coordinated`` verdict; otherwise ``organic``.

Beyond 1-hop clustering (BubbleMaps / DevsNightmare depth):
  * 2-HOP FUNDING CHAIN — the funder's own funder (grandfunder) is resolved
    so layered arms-dealer patterns surface (``funding_chain_depth`` 0/1/2
    + ``two_hop_chain_buyers``).
  * TIME NODES — launch buyers are bucketed by the HOUR their funding event
    landed; buckets holding >= 2 buyers count as "time nodes"
    (``time_nodes``, BubbleMaps-style).
  * SUPPLY GRAB — the cluster's current combined balance vs tracked supply
    distinguishes "bundled and holding" (still holds a large % of supply)
    from "bundled and dumped" (exited most of what it bought)
    (``supply_grab.status``).
  * BUNDLE VERDICT LINE — a richer one-line verdict (``bundle_line``):
    ``🔴 Bundle: 14 wallets · same-slot 9 · 2-hop funder chain · holding
    62.0% (dumped 12.0%) · 3 time-nodes``. Fail-open to ``"—"`` when no
    bundle evidence exists.

Data sources (reused — no new services):
  - wallet_memory.evm_transfers + deployer_events via
    ``_own_bundle_queries._query_launch_cluster`` (first-buyers + block),
  - first-funder lookup via ``_funding_queries._query_first_funders``
    (argMin incoming transfer across ALL tokens — the wallet that funded
    each buyer before they bought; same query drives the 2-hop lookup and
    the funding timestamps),
  - current holdings via ``_query_tracked_balances``
    (wallet_memory.holder_balances FINAL — the supply-grab dimension).

Fail-open by design: returns an ``unavailable`` payload with
``cluster_line: "—"`` when ClickHouse has no launch-buyer / funding data.
Never fabricates clusters for missing data.

Usage:
    from rugmunchbot._vendor.scanner_engines.own_cluster_analysis import get_own_cluster_analysis

    result = await get_own_cluster_analysis("0x...", "ethereum")
    # result = {
    #     "check": "cluster_analysis",
    #     "result": "scanned" | "unavailable",
    #     "source": "own",
    #     "cluster_line": "🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated",
    #     "distinct_funders": int,
    #     "funded_wallets": int,
    #     "largest_cluster_size": int,
    #     "clusters": [{"funder": str, "wallet_count": int, "wallets": [str]}],
    #     "coordinated": bool,
    #     "coordinated_buyers": int,
    #     "funding_chain_buyers": int,
    #     "verdict": "coordinated" | "organic" | "unknown",
    #     "bundle_line": "🔴 Bundle: ..." | "—",
    #     "same_slot_buyers": int,
    #     "funding_chain_depth": int,
    #     "two_hop_chain_buyers": int,
    #     "time_nodes": int,
    #     "supply_grab": {"status": str, "held_pct": float, "dumped_pct": float, "exited_wallets": int},
    # }
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, cast

from rugmunchbot._vendor.logging_shim import get_logger
from app.domains.databus.providers.chains.lite_chain_indexer import _get_ch_client
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._funding_queries import (
    _QUERY_TIMEOUT_SECONDS,
    _query_first_funders,
)
from rugmunchbot._vendor.scanner_engines._own_bundle_common import _normalize_chain
from rugmunchbot._vendor.scanner_engines._own_bundle_queries import _query_launch_cluster

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_CLUSTER_CACHE: dict[str, dict[str, Any]] = {}
_CLUSTER_CACHE_TTL = 600  # 10 minutes — matches the bundle cache
_CLUSTER_CACHE_MAX = 100

#: Max funder clusters surfaced in the result (top-N by wallet count).
_MAX_CLUSTERS = 5

#: Minimum combined held % of tracked supply for a coordinated cluster to be
#: called a SUPPLY GRAB (BubbleMaps-style "bundled and holding").
_SUPPLY_GRAB_MIN_HELD_PCT = 10.0
#: Minimum dumped share (of what the cluster bought) to call it "bundled and
#: dumped" instead of "holding".
_SUPPLY_GRAB_MIN_DUMPED_PCT = 50.0
#: A launch buyer whose current balance is at or below this is "exited" (dumped).
_EXITED_BALANCE_EPSILON = 1e-12
#: Time-node bucket: buyers funded within the same hour form one "time node".
_TIME_NODE_BUCKET = "%Y-%m-%d %H:00"


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_cluster_analysis(
    token_address: str,
    chain: str,
    deployer: str | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Funding-source clusters + coordinated-buy verdict for a token.

    Groups first (launch-window) buyers by their first funder and decides
    whether the early buying was coordinated (same block + shared funder)
    or organic. Best-effort: returns an ``unavailable`` payload with a
    ``cluster_line`` of ``"—"`` when ClickHouse has no launch-buyer or
    funding data. Never raises.

    Args:
        token_address: Token address (lowercased internally).
        chain: Chain name (ethereum / bsc / polygon / ...; solana has no
            evm_transfers funding rows, so it degrades to ``unavailable``).
        deployer: Optional deployer address — treated as a known non-funder
            (cluster results are unchanged; kept for signature symmetry).
        force_fresh: Bypass the in-process cache.

    Returns:
        Dict with ``cluster_line`` (compact 🕸️ summary) plus cluster detail
        and the coordinated / organic verdict. Fail-open to ``"—"`` when
        launch-buyer / funding data is not reachable.
    """
    token_address = (token_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain_norm}"
    if not force_fresh and cache_key in _CLUSTER_CACHE:
        cached = _CLUSTER_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _CLUSTER_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "cluster_analysis",
        "result": "unavailable",
        "source": "own",
        "cluster_line": "\u2014",  # —
        "distinct_funders": 0,
        "funded_wallets": 0,
        "largest_cluster_size": 0,
        "clusters": [],
        "coordinated": False,
        "coordinated_buyers": 0,
        "funding_chain_buyers": 0,
        "verdict": "unknown",
        # ── Beyond 1-hop clustering (BubbleMaps/DevsNightmare depth) ──
        "bundle_line": "\u2014",  # richer 🔴 Bundle verdict (fail-open "—")
        "same_slot_buyers": 0,  # busiest same-block buyer count
        "funding_chain_depth": 0,  # 0 / 1 / 2 (funder's-funder reach)
        "two_hop_chain_buyers": 0,  # buyers whose grandfunder is a launch buyer / shared
        "time_nodes": 0,  # distinct funding-hour buckets with >= 2 buyers
        "supply_grab": {
            "status": "none",  # "holding" | "dumped" | "none"
            "held_pct": 0.0,
            "dumped_pct": 0.0,
            "exited_wallets": 0,
        },
    }

    try:
        # ── 1. Launch-window first buyers (with their block numbers) ──
        launch = await run_sync(_query_launch_cluster, token_address, chain_norm)
        launch_buyers = []
        if isinstance(launch, dict):
            launch_buyers = [b for b in (launch.get("launch_buyers") or []) if isinstance(b, dict)]

        if not launch_buyers:
            result["note"] = "Funding-source clustering unavailable - no launch-buyer data for this token/chain"
            _cache_cluster(cache_key, result)
            return result

        # ── 2. First funder per first-buyer (the wallet that funded them) ──
        buyer_addrs = list(dict.fromkeys(str(b.get("buyer", "") or "").lower() for b in launch_buyers))
        funder_rows = await run_sync(_query_first_funders, buyer_addrs, chain_norm)
        funder_map: dict[str, str] = {}
        funder_ts_map: dict[str, Any] = {}
        for row in funder_rows or []:
            if len(row) >= 2 and row[0] and row[1]:
                funder_map[str(row[0]).lower()] = str(row[1]).lower()
                if len(row) >= 4 and row[3] is not None:
                    funder_ts_map[str(row[0]).lower()] = row[3]

        if not funder_map:
            result["note"] = "Funding-source clustering unavailable - no funding data for this chain"
            _cache_cluster(cache_key, result)
            return result

        # ── 2b. 2-hop funding chain: the funder's own funder (grandfunder) ──
        # One operator arming N funders shows up as a shared grandfunder; a
        # grandfunder who is itself a launch buyer is a two-level funding
        # chain into the token. Best-effort — empty map degrades depth to 1.
        grandfunder_map: dict[str, str] = {}
        try:
            funder_addrs = list(dict.fromkeys(f for f in funder_map.values() if f))
            if funder_addrs:
                gf_rows = await run_sync(_query_first_funders, funder_addrs, chain_norm)
                for row in gf_rows or []:
                    if len(row) >= 2 and row[0] and row[1]:
                        grandfunder_map[str(row[0]).lower()] = str(row[1]).lower()
        except Exception as exc:  # pragma: no cover - defensive; never break the scan
            logger.debug("cluster_analysis_grandfunder_failed: %s", exc)

        # ── 2c. Current holdings of the launch buyers (supply-grab dimension) ──
        # How much of the supply the cluster still holds vs how much it exited
        # distinguishes "bundled and holding" from "bundled and dumped".
        balances_map: dict[str, float] = {}
        total_tracked = 0.0
        try:
            balance_rows = await run_sync(_query_tracked_balances, token_address, chain_norm)
            for row in balance_rows or []:
                addr = str(row[0] or "").lower()
                if addr:
                    balances_map[addr] = float(row[1] or 0)
            total_tracked = sum(balances_map.values())
        except Exception as exc:  # pragma: no cover - defensive; never break the scan
            logger.debug("cluster_analysis_balances_failed: %s", exc)

        # ── 3. Build funding-source clusters + coordinated verdict ──
        clusters = _build_funding_clusters(launch_buyers, funder_map)
        coordinated, coordinated_buyers, chain_buyers = _detect_coordinated_buys(launch_buyers, funder_map)

        distinct_funders = len(clusters)
        funded_wallets = sum(c["wallet_count"] for c in clusters)
        largest = clusters[0]["wallet_count"] if clusters else 0
        verdict = "coordinated" if coordinated else "organic"

        # ── 4. Deeper bundle dimensions (beyond 1-hop clustering) ──
        same_slot = max((int(c.get("buyer_count", 0) or 0) for c in (launch.get("cluster_blocks") or [])), default=0)
        time_nodes = _group_time_nodes(launch_buyers, funder_ts_map)
        funding_depth, two_hop_buyers = _detect_funding_chain_depth(launch_buyers, funder_map, grandfunder_map)
        supply_grab = _compute_supply_grab(launch_buyers, balances_map, total_tracked)
        bundle_line = _build_bundle_verdict_line(
            funded_wallets,
            same_slot,
            funding_depth,
            supply_grab,
            time_nodes,
            coordinated,
        )

        result.update(
            {
                "result": "scanned",
                "cluster_line": _build_cluster_line(distinct_funders, funded_wallets, largest, verdict),
                "distinct_funders": distinct_funders,
                "funded_wallets": funded_wallets,
                "largest_cluster_size": largest,
                "clusters": clusters[:_MAX_CLUSTERS],
                "coordinated": coordinated,
                "coordinated_buyers": coordinated_buyers,
                "funding_chain_buyers": chain_buyers,
                "verdict": verdict,
                "bundle_line": bundle_line,
                "same_slot_buyers": same_slot,
                "funding_chain_depth": funding_depth,
                "two_hop_chain_buyers": two_hop_buyers,
                "time_nodes": time_nodes,
                "supply_grab": supply_grab,
            }
        )
    except Exception as exc:  # pragma: no cover - defensive; never break a scan
        logger.debug("cluster_analysis_failed for %s: %s", token_address[:16], exc)
        result["note"] = "Cluster detail unavailable - data source unreachable"

    _cache_cluster(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_cluster_analysis_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        scanned=result["result"] == "scanned",
        verdict=result["verdict"],
        largest=result["largest_cluster_size"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PURE CLUSTERING LOGIC (unit-testable without ClickHouse)
# ═══════════════════════════════════════════════════════════════════════════


def _build_funding_clusters(
    launch_buyers: list[dict[str, Any]],
    funder_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Group first-buyers by their FIRST FUNDER.

    Returns clusters sorted by wallet count descending:
    ``[{"funder": str, "wallet_count": int, "wallets": [str]}, ...]``.
    Buyers with no known funder are omitted (they contribute nothing to a
    funding-source cluster).
    """
    groups: dict[str, list[str]] = {}
    for b in launch_buyers:
        buyer = str(b.get("buyer", "") or "").lower()
        funder = (funder_map.get(buyer) or "").lower()
        if not buyer or not funder:
            continue
        groups.setdefault(funder, []).append(buyer)

    clusters = [
        {"funder": funder, "wallet_count": len(wallets), "wallets": wallets} for funder, wallets in groups.items()
    ]
    clusters.sort(key=lambda c: (c["wallet_count"], c["funder"]), reverse=True)
    return clusters


def _detect_coordinated_buys(
    launch_buyers: list[dict[str, Any]],
    funder_map: dict[str, str],
) -> tuple[bool, int, int]:
    """Detect coordinated buys: same block + shared first funder.

    A coordinated pair is two first-buyers that BOTH:
      (a) bought in the same block, and
      (b) share the same first funder (the wallet that funded them).

    Also flags a weaker two-hop signal — a "funding chain" — where a
    buyer's first funder is itself a first-buyer of the token (the funder
    bought first, then armed other launch wallets).

    Returns ``(coordinated, coordinated_wallet_count, funding_chain_wallet_count)``.
    """
    by_block: dict[int, list[str]] = {}
    for b in launch_buyers:
        blk = int(b.get("block_number", 0) or 0)
        buyer = str(b.get("buyer", "") or "").lower()
        if blk and buyer:
            by_block.setdefault(blk, []).append(buyer)

    launch_buyer_set = {str(b.get("buyer", "") or "").lower() for b in launch_buyers}

    # ── Same-block + shared-funder wallets ──
    coordinated_wallets: set[str] = set()
    for _blk, buyers in by_block.items():
        if len(buyers) < 2:
            continue
        funder_groups: dict[str, list[str]] = {}
        for buyer in buyers:
            funder = (funder_map.get(buyer) or "").lower()
            if funder:
                funder_groups.setdefault(funder, []).append(buyer)
        for group in funder_groups.values():
            if len(group) >= 2:
                coordinated_wallets.update(group)

    coordinated = len(coordinated_wallets) >= 2

    # ── Funding-chain wallets: their first funder is itself a launch buyer ──
    funding_chain: set[str] = set()
    for buyer, funder in funder_map.items():
        f = (funder or "").lower()
        if f and f in launch_buyer_set and f != buyer:
            funding_chain.add(buyer)

    return coordinated, len(coordinated_wallets), len(funding_chain)


def _build_cluster_line(
    distinct_funders: int,
    funded_wallets: int,
    largest_cluster_size: int,
    verdict: str,
) -> str:
    """Compact 🕸️ summary, e.g. ``🕸️ 3 funders → 14 wallets · largest cluster 9 · coordinated``."""
    return (
        f"\U0001f578\ufe0f {distinct_funders} funders \u2192 {funded_wallets} wallets"
        f" \u00b7 largest cluster {largest_cluster_size} \u00b7 {verdict}"
    )


def _group_time_nodes(
    launch_buyers: list[dict[str, Any]],
    funder_ts_map: dict[str, Any],
) -> int:
    """Count BubbleMaps-style "time nodes": funding-hour buckets with >= 2 buyers.

    Each launch buyer is bucketed by the HOUR its first funding event landed
    (from ``funder_ts_map`` — the funding transfer's timestamp). A bucket
    holding >= 2 buyers means an operator armed several wallets around the
    same time — one "time node". Returns the number of such buckets (0 when
    no timestamps are available).
    """
    hour_counts: dict[str, int] = {}
    for b in launch_buyers:
        buyer = str(b.get("buyer", "") or "").lower()
        ts = funder_ts_map.get(buyer)
        bucket = _bucket_hour(ts)
        if bucket:
            hour_counts[bucket] = hour_counts.get(bucket, 0) + 1
    return sum(1 for n in hour_counts.values() if n >= 2)


def _bucket_hour(ts: Any) -> str | None:
    """Bucket a funding timestamp to its hour key (``YYYY-MM-DD HH:00``).

    Accepts datetimes (ClickHouse ``DateTime``) and ISO strings; returns
    ``None`` for missing/unparseable timestamps so time-node counting skips
    them rather than fabricating a bucket.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=None).strftime(_TIME_NODE_BUCKET)
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None).strftime(_TIME_NODE_BUCKET)
    except (TypeError, ValueError):
        return None


def _detect_funding_chain_depth(
    launch_buyers: list[dict[str, Any]],
    funder_map: dict[str, str],
    grandfunder_map: dict[str, str],
) -> tuple[int, int]:
    """Funding-chain depth: 0 (none) / 1 (funder is a launch buyer) / 2 (grandfunder).

    Two independent 2-hop signals:
      (a) the funder's own funder (grandfunder) is itself a launch buyer —
          a two-level funding chain INTO the token;
      (b) >= 2 distinct funders share the same grandfunder — one operator
          armed multiple funders (layered arms-dealer pattern).

    Returns ``(depth, two_hop_buyer_count)``. Depth is 2 when any 2-hop
    signal fires, 1 when the existing 1-hop signal fires, else 0.
    """
    launch_buyer_set = {str(b.get("buyer", "") or "").lower() for b in launch_buyers}

    two_hop: set[str] = set()
    # Distinct funders per grandfunder — two wallet-counts under the same
    # funder do NOT count as a shared layer-2 operator.
    gf_funders: dict[str, set[str]] = {}
    for funder in funder_map.values():
        gf = (grandfunder_map.get(funder) or "").lower()
        if gf:
            gf_funders.setdefault(gf, set()).add(funder)

    for buyer, funder in funder_map.items():
        gf = (grandfunder_map.get(funder) or "").lower()
        if not gf:
            continue
        # (a) grandfunder bought the token itself → layered funding into it.
        if gf in launch_buyer_set and gf != buyer and gf != funder:
            two_hop.add(buyer)
        # (b) grandfunder armed >= 2 DISTINCT funders → shared layer-2 operator.
        if len(gf_funders.get(gf, set())) >= 2:
            two_hop.add(buyer)

    if two_hop:
        return 2, len(two_hop)

    # 1-hop: the buyer's funder is itself a launch buyer (existing signal).
    if any((funder or "").lower() in launch_buyer_set for funder in funder_map.values()):
        return 1, 0
    return 0, 0


def _compute_supply_grab(
    launch_buyers: list[dict[str, Any]],
    balances_map: dict[str, float],
    total_tracked: float,
) -> dict[str, Any]:
    """Supply-grab verdict: did the coordinated cluster keep or dump its stake?

    The cluster's current combined balance is measured against the tracked
    supply (holder_balances). ``status``:
      - ``holding`` — the cluster still holds >= ``_SUPPLY_GRAB_MIN_HELD_PCT``
        of tracked supply ("bundled and holding"),
      - ``dumped`` — the cluster exited >= ``_SUPPLY_GRAB_MIN_DUMPED_PCT`` of
        what it bought ("bundled and dumped"),
      - ``none`` — neither (no meaningful supply grab / no balance data).

    ``dumped_pct`` is the share of the cluster's launch-window buy amounts
    that now sit at a ~zero balance (same raw-value scale, so the ratio is
    valid even when raw units differ from holder_balances).
    """
    bought: dict[str, float] = {}
    for b in launch_buyers:
        buyer = str(b.get("buyer", "") or "").lower()
        amt = float(b.get("amount", 0) or 0)
        if buyer:
            bought[buyer] = amt

    # No balance snapshot at all → we cannot distinguish "holding" from
    # "exited". Fail-open to "none" instead of calling the cluster dumped.
    if not balances_map:
        return {
            "status": "none",
            "held_pct": 0.0,
            "dumped_pct": 0.0,
            "exited_wallets": 0,
        }

    held_total = sum(balances_map.get(buyer, 0.0) for buyer in bought)
    held_pct = held_total / total_tracked * 100 if total_tracked > 0 else 0.0

    total_bought = sum(bought.values())
    exited_amt = sum(amt for buyer, amt in bought.items() if balances_map.get(buyer, 0.0) <= _EXITED_BALANCE_EPSILON)
    dumped_pct = exited_amt / total_bought * 100 if total_bought > 0 else 0.0
    exited_wallets = sum(1 for buyer in bought if balances_map.get(buyer, 0.0) <= _EXITED_BALANCE_EPSILON)

    if held_pct >= _SUPPLY_GRAB_MIN_HELD_PCT:
        status = "holding"
    elif dumped_pct >= _SUPPLY_GRAB_MIN_DUMPED_PCT and total_bought > 0:
        status = "dumped"
    else:
        status = "none"

    return {
        "status": status,
        "held_pct": round(held_pct, 1),
        "dumped_pct": round(dumped_pct, 1),
        "exited_wallets": exited_wallets,
    }


def _build_bundle_verdict_line(
    n_wallets: int,
    same_slot: int,
    funding_depth: int,
    supply_grab: dict[str, Any],
    time_nodes: int,
    coordinated: bool,
) -> str:
    """Richer 🔴 Bundle verdict, e.g.:

    ``🔴 Bundle: 14 wallets · same-slot 9 · 2-hop funder chain · holding 62.0% (dumped 12.0%) · 3 time-nodes``

    Honest + fail-open: returns ``"—"`` when there is no bundle evidence at
    all (no coordinated cluster, no supply grab, no time nodes) — never a
    fabricated bundle. Parts with no data are omitted (e.g. no ``(dumped
    Y%)`` when nothing was dumped).
    """
    has_bundle_signal = coordinated or (supply_grab.get("status") != "none") or time_nodes > 0
    if not has_bundle_signal:
        return "\u2014"

    parts = [f"\U0001f534 Bundle: {n_wallets} wallets"]
    if same_slot > 0:
        parts.append(f"same-slot {same_slot}")
    parts.append(f"{funding_depth}-hop funder chain" if funding_depth >= 2 else "1-hop funder chain")

    held = float(supply_grab.get("held_pct", 0) or 0)
    dumped = float(supply_grab.get("dumped_pct", 0) or 0)
    if held > 0 or dumped > 0:
        dumped_txt = f" (dumped {dumped:.1f}%)" if dumped > 0 else ""
        parts.append(f"holding {held:.1f}%{dumped_txt}")

    if time_nodes > 0:
        parts.append(f"{time_nodes} time-nodes")

    return " \u00b7 ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERY LAYER (module-local, graceful)
# ═══════════════════════════════════════════════════════════════════════════


def _query_tracked_balances(
    token_address: str,
    chain: str,
    limit: int = 1000,
) -> list[tuple[Any, ...]] | None:
    """Positive holder balances for a token (current holdings snapshot).

    EVM: pre-aggregated ``holder_balances FINAL`` (falls back to a bounded
    ``evm_transfers`` delta scan when the indexer has not seen the token).
    Solana: ``solana_spl_balance_changes``. Returns ``(address, balance)``
    rows, or ``None`` when ClickHouse is unreachable / no data.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        if chain == "solana":
            rows = client.execute(
                """
                SELECT if(owner != '', owner, token_account) AS holder, sum(toFloat64OrZero(delta)) AS balance
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                GROUP BY holder
                HAVING balance > 0
                ORDER BY balance DESC
                LIMIT %(limit)s
                """,
                {"token": token_address, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        else:
            rows = client.execute(
                """
                SELECT address, balance FROM wallet_memory.holder_balances FINAL
                WHERE chain = %(chain)s AND token_address = %(token)s AND balance > 0
                ORDER BY balance DESC
                LIMIT %(limit)s
                """,
                {"token": token_address, "chain": chain, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            if not rows:
                rows = client.execute(
                    """
                    SELECT addr, balance FROM (
                        SELECT
                            addr,
                            sum(net) AS balance
                        FROM (
                            SELECT to_address AS addr, toFloat64OrZero(value) AS net
                            FROM wallet_memory.evm_transfers
                            WHERE token_address = %(token)s
                              AND chain = %(chain)s
                              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                            UNION ALL
                            SELECT from_address AS addr, -toFloat64OrZero(value) AS net
                            FROM wallet_memory.evm_transfers
                            WHERE token_address = %(token)s
                              AND chain = %(chain)s
                              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                        )
                        WHERE addr != ''
                        GROUP BY addr
                        HAVING sum(net) > 0
                    )
                    ORDER BY balance DESC
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "chain": chain, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
        return cast("list[tuple[Any, ...]] | None", rows) if rows else None
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("cluster_tracked_balances_query_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_cluster(key: str, data: dict[str, Any]) -> None:
    data["_cached_at"] = time.time()
    _CLUSTER_CACHE[key] = data
    if len(_CLUSTER_CACHE) > _CLUSTER_CACHE_MAX:
        _CLUSTER_CACHE.pop(next(iter(_CLUSTER_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _CLUSTER_CACHE,
        _CLUSTER_CACHE_MAX,
        _CLUSTER_CACHE_TTL,
        extra={"max_clusters": _MAX_CLUSTERS},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_CLUSTER_CACHE)

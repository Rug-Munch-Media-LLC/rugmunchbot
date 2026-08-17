# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_cross_token_link.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Own Cross-Token Link — Recurring-Cluster + Serial-Launcher Detection
=====================================================================
Beyond single-token clustering: detects ASSOCIATED WALLETS across MULTIPLE
tokens — the "same syndicate launches tokens repeatedly" signal competitors
miss.

Two cross-token signals:

  * RECURRING-CLUSTER — the token's launch-window buyers/funders (the
    funding cluster) are checked against PRIOR token launches: if >= 2 of
    the cluster's wallets also bought the same prior token, that prior token
    is a recurring launch for the syndicate. Surfaced as:

        🕸️ Recurring cluster: 7 wallets also bought 3 prior launches

  * SERIAL-LAUNCHER linkage — if the deployer (or its first funder)
    deployed/participated in prior tokens, they are linked explicitly:

        🔗 Deployer linked to 4 prior launches: 0xabc…, 0xdef…

Data sources (reused — no new services):
  - launch-window buyers + deploy block   (``_own_bundle_queries``),
  - first funders of the launch buyers    (``_funding_queries``),
  - prior-token launches via
    ``wallet_memory.deployer_events`` joined with ``evm_transfers`` (which
    wallets bought each prior token BEFORE this token's launch),
  - deployer prior-token history          (``_deployer_fingerprint_queries``),
  - knowledge-graph HOLDS edges (Neo4j/Redis) as a secondary path when the
    ClickHouse transfer history is unavailable.

Fail-open by design: returns an ``unavailable`` payload with ``"—"`` lines
when ClickHouse / the knowledge graph has no cross-token data. Never
fabricates prior-launch linkage for missing data.

Usage:
    from rugmunchbot._vendor.scanner_engines.own_cross_token_link import get_own_cross_token_link

    result = await get_own_cross_token_link("0x...", "ethereum", deployer="0x...")
    # result = {
    #     "check": "cross_token_link",
    #     "result": "scanned" | "unavailable",
    #     "source": "own",
    #     "recurring_cluster_line": "🕸️ Recurring cluster: 7 wallets also bought 3 prior launches" | "—",
    #     "recurring_wallets": int,
    #     "prior_launch_count": int,
    #     "prior_launch_tokens": [{"token": str, "wallet_count": int, "wallets": [str]}],
    #     "deployer_link_line": "🔗 Deployer linked to 4 prior launches: 0xabc…, 0xdef…" | "—",
    #     "deployer_prior_count": int,
    #     "deployer_prior_tokens": [str],
    #     "funder_link_line": "🔗 Funder also funded 2 other deployers" | "—",
    #     "funder_deployer_count": int,
    # }
"""

from __future__ import annotations

import time
from typing import Any, cast

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.lite_chain_indexer import _get_ch_client
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_queries import (
    _NATIVE_TOKEN_MARKERS,
    _QUERY_TIMEOUT_SECONDS,
    _query_funder,
    _query_per_token_deployments,
    _query_same_funder_deployers,
)
from rugmunchbot._vendor.scanner_engines._funding_queries import _query_first_funders
from rugmunchbot._vendor.scanner_engines._own_bundle_common import _normalize_chain
from rugmunchbot._vendor.scanner_engines._own_bundle_queries import _query_launch_cluster

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_CROSS_TOKEN_CACHE: dict[str, dict[str, Any]] = {}
_CROSS_TOKEN_CACHE_TTL = 600  # 10 minutes — cross-token history changes slowly
_CROSS_TOKEN_CACHE_MAX = 100

#: Max cluster wallets (buyers + funders) checked for prior-launch overlap.
_MAX_CLUSTER_WALLETS = 40
#: A prior token counts as "recurring" when >= this many cluster wallets bought it.
_MIN_RECURRING_WALLETS = 2
#: Max prior-launch tokens surfaced in the result.
_MAX_PRIOR_TOKENS = 10
#: Max prior-launch tokens named in the compact link line (A, B, C, ...).
_MAX_LINKED_IN_LINE = 3


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_cross_token_link(
    token_address: str,
    chain: str,
    deployer: str | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Cross-token association detection (recurring cluster + serial launcher).

    Checks the token's funding-cluster wallets against prior token launches
    (recurring-cluster signal) and links the deployer (and its funder) to any
    prior tokens they deployed / armed (serial-launcher signal). Best-effort:
    returns an ``unavailable`` payload with ``"—"`` lines when no cross-token
    history exists. Never raises.

    Args:
        token_address: Token address (lowercased internally).
        chain: Chain name (ethereum / bsc / polygon / ...).
        deployer: Optional deployer address — enables the serial-launcher link.
        force_fresh: Bypass the in-process cache.

    Returns:
        Dict with ``recurring_cluster_line`` (🕸️) and ``deployer_link_line``
        (🔗) plus the underlying counts. Fail-open to ``"—"``.
    """
    token_address = (token_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    deployer_norm = (deployer or "").lower().strip() or None
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain_norm}:{deployer_norm or 'none'}"
    if not force_fresh and cache_key in _CROSS_TOKEN_CACHE:
        cached = _CROSS_TOKEN_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _CROSS_TOKEN_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "cross_token_link",
        "result": "unavailable",
        "source": "own",
        "recurring_cluster_line": "\u2014",  # —
        "recurring_wallets": 0,
        "prior_launch_count": 0,
        "prior_launch_tokens": [],
        "deployer_link_line": "\u2014",  # —
        "deployer_prior_count": 0,
        "deployer_prior_tokens": [],
        "funder_link_line": "\u2014",  # —
        "funder_deployer_count": 0,
        "note": None,
    }

    try:
        # ── 1. Cluster wallets: launch-window first buyers + their funders ──
        launch = await run_sync(_query_launch_cluster, token_address, chain_norm)
        deploy_block = 0
        launch_buyers: list[dict[str, Any]] = []
        if isinstance(launch, dict):
            deploy_block = int(launch.get("deploy_block") or 0)
            launch_buyers = [b for b in (launch.get("launch_buyers") or []) if isinstance(b, dict)]

        buyer_addrs = list(dict.fromkeys(str(b.get("buyer", "") or "").lower() for b in launch_buyers))
        funder_rows = await run_sync(_query_first_funders, buyer_addrs, chain_norm)
        funder_addrs: list[str] = []
        for row in funder_rows or []:
            if len(row) >= 2 and row[1]:
                funder_addrs.append(str(row[1]).lower())
        cluster_wallets = list(dict.fromkeys([*buyer_addrs, *funder_addrs]))[:_MAX_CLUSTER_WALLETS]

        # ── 2. Recurring-cluster: which prior launches did these wallets buy? ──
        recurring = await _recurring_cluster(cluster_wallets, chain_norm, token_address, deploy_block)
        if recurring:
            prior_tokens, recurring_wallet_set = recurring
            result["prior_launch_tokens"] = prior_tokens[:_MAX_PRIOR_TOKENS]
            result["prior_launch_count"] = len(prior_tokens)
            result["recurring_wallets"] = len(recurring_wallet_set)
            result["recurring_cluster_line"] = _build_recurring_line(len(recurring_wallet_set), len(prior_tokens))
        elif cluster_wallets:
            result["note"] = (
                "[REVIEW] no prior-launch overlap found for the cluster wallets "
                "(no deployer_events/transfer history or knowledge-graph coverage)"
            )

        # ── 3. Serial-launcher: deployer (and its funder) prior-token links ──
        if deployer_norm:
            prior_tokens = await run_sync(_query_per_token_deployments, deployer_norm, chain_norm)
            deployer_priors = [
                t.get("token_address")
                for t in (prior_tokens or [])
                if str(t.get("token_address") or "").lower() != token_address
            ]
            if deployer_priors:
                short_ids = [_short_id(t) for t in deployer_priors]
                result["deployer_prior_count"] = len(deployer_priors)
                result["deployer_prior_tokens"] = short_ids
                result["deployer_link_line"] = _build_deployer_link_line(len(deployer_priors), short_ids)

            funder = await run_sync(_query_funder, deployer_norm, chain_norm)
            if funder and funder.get("from_address"):
                same_funder = await run_sync(_query_same_funder_deployers, funder["from_address"], chain_norm)
                if same_funder and int(same_funder) >= 2:
                    other = max(int(same_funder) - 1, 0)
                    result["funder_deployer_count"] = other
                    result["funder_link_line"] = _build_funder_link_line(other)

        scanned = (
            result["recurring_cluster_line"] != "\u2014"
            or result["deployer_link_line"] != "\u2014"
            or result["funder_link_line"] != "\u2014"
        )
        result["result"] = "scanned" if scanned else "unavailable"
    except Exception as exc:  # pragma: no cover - defensive; never break a scan
        logger.debug("cross_token_link_failed for %s: %s", token_address[:16], exc)
        result["note"] = "[REVIEW] cross-token link data source unreachable"

    _cache_cross_token(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_cross_token_link_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        scanned=result["result"] == "scanned",
        recurring_wallets=result["recurring_wallets"],
        prior_launches=result["prior_launch_count"],
        deployer_priors=result["deployer_prior_count"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-TOKEN DETECTION
# ═══════════════════════════════════════════════════════════════════════════


async def _recurring_cluster(
    cluster_wallets: list[str],
    chain: str,
    token_address: str,
    deploy_block: int,
) -> tuple[list[dict[str, Any]], set[str]] | None:
    """Prior launches the cluster wallets also bought (recurring-cluster signal).

    Primary path: ClickHouse ``deployer_events`` joined with ``evm_transfers``
    — prior tokens (deployed launches) that >= ``_MIN_RECURRING_WALLETS`` of the
    cluster's wallets bought BEFORE this token's deploy block. Secondary path:
    the knowledge graph's wallet→token HOLDS edges (used when ClickHouse has no
    transfer history). Returns ``(prior_tokens, recurring_wallet_set)`` or
    ``None`` when no recurrence exists / no data is reachable.

    ``prior_tokens`` items: ``{"token": str, "wallet_count": int, "wallets": [str]}``.
    """
    if not cluster_wallets:
        return None

    # ── Primary: ClickHouse deployer_events + evm_transfers ──
    rows = await run_sync(_query_recurring_prior_tokens, cluster_wallets, chain, token_address, deploy_block)
    if rows:
        prior_tokens: list[dict[str, Any]] = [
            {
                "token": str(r[0] or "").lower(),
                "wallet_count": int(r[1] or 0),
                "wallets": sorted({str(w).lower() for w in (r[2] or [])}),
            }
            for r in rows
            if r and r[0]
        ]
        if prior_tokens:
            recurring_wallets: set[str] = {w for t in prior_tokens for w in t["wallets"]}
            return prior_tokens, recurring_wallets

    # ── Secondary: knowledge-graph HOLDS edges (Neo4j/Redis) ──
    kg_rows = await _kg_prior_token_overlap(cluster_wallets)
    if kg_rows:
        prior_tokens = [
            {"token": token, "wallet_count": count, "wallets": wallets} for token, count, wallets in kg_rows
        ]
        if prior_tokens:
            recurring_wallets = {w for t in prior_tokens for w in t["wallets"]}
            return prior_tokens, recurring_wallets

    return None


# ═══════════════════════════════════════════════════════════════════════════
# PURE LINE BUILDERS (unit-testable without ClickHouse)
# ═══════════════════════════════════════════════════════════════════════════


def _build_recurring_line(recurring_wallets: int, prior_launch_count: int) -> str:
    """Compact 🕸️ recurring-cluster line, e.g.:

    ``🕸️ Recurring cluster: 7 wallets also bought 3 prior launches``

    Fail-open: returns ``"—"`` when there is no recurrence (0 wallets / 0 launches).
    """
    if recurring_wallets <= 0 or prior_launch_count <= 0:
        return "\u2014"
    launch_noun = "launch" if prior_launch_count == 1 else "launches"
    return (
        f"\U0001f578\ufe0f Recurring cluster: {recurring_wallets} wallets"
        f" also bought {prior_launch_count} prior {launch_noun}"
    )


def _build_deployer_link_line(prior_count: int, short_ids: list[str]) -> str:
    """Compact 🔗 serial-launcher line, e.g.:

    ``🔗 Deployer linked to 4 prior launches: 0xabc…, 0xdef…``

    Shows up to ``_MAX_LINKED_IN_LINE`` token ids, then "+N more". Fail-open:
    returns ``"—"`` when the deployer has no prior launches.
    """
    if prior_count <= 0 or not short_ids:
        return "\u2014"
    shown = short_ids[:_MAX_LINKED_IN_LINE]
    more = len(short_ids) - len(shown)
    suffix = f" +{more} more" if more > 0 else ""
    return f"\U0001f517 Deployer linked to {prior_count} prior launches: {', '.join(shown)}{suffix}"


def _build_funder_link_line(other_deployers: int) -> str:
    """Compact 🔗 funder-arming line, e.g.:

    ``🔗 Funder also funded 2 other deployers``

    Fail-open: returns ``"—"`` when the funder armed no other deployers.
    """
    if other_deployers <= 0:
        return "\u2014"
    noun = "deployer" if other_deployers == 1 else "deployers"
    return f"\U0001f517 Funder also funded {other_deployers} other {noun}"


def _short_id(address: str, keep: int = 6) -> str:
    """Short address id for compact lines: ``0xabc…`` (first ``keep`` chars)."""
    addr = str(address or "").lower().strip()
    if len(addr) <= keep + 1:
        return addr or "\u2014"
    return f"{addr[:keep]}\u2026"


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERY LAYER (module-local, graceful)
# ═══════════════════════════════════════════════════════════════════════════


def _query_recurring_prior_tokens(
    wallets: list[str],
    chain: str,
    current_token: str,
    before_block: int,
    limit: int = 20,
) -> list[tuple[Any, ...]] | None:
    """Prior launches (deployer_events tokens) that >= 2 cluster wallets bought.

    Joins ``evm_transfers`` (which cluster wallets bought what) with
    ``deployer_events`` (tokens that were actually deployed launches) and keeps
    only buys of OTHER tokens that happened BEFORE this token's deploy block —
    i.e. prior launches the syndicate's wallets also appeared in. Returns rows
    ``(prior_token, wallet_count, wallets)``, or ``None`` when ClickHouse is
    unreachable / no data.
    """
    client = _get_ch_client()
    if client is None or not wallets:
        return None
    try:
        rows = client.execute(
            """
            SELECT
                t.token_address AS prior_token,
                uniqExact(t.from_address) AS wallet_count,
                groupUniqArray(t.from_address) AS wallets
            FROM wallet_memory.evm_transfers AS t
            INNER JOIN (
                SELECT token_address
                FROM wallet_memory.deployer_events
                WHERE chain = %(chain)s
                  AND token_address != %(token)s
                GROUP BY token_address
            ) AS d ON d.token_address = t.token_address
            WHERE t.chain = %(chain)s
              AND t.from_address IN %(wallets)s
              AND t.token_address != %(token)s
              AND lower(t.token_address) NOT IN %(native)s
              AND t.block_number < %(before_block)s
            GROUP BY t.token_address
            HAVING uniqExact(t.from_address) >= %(min_wallets)s
            ORDER BY wallet_count DESC
            LIMIT %(limit)s
            """,
            {
                "chain": chain,
                "token": current_token,
                "wallets": tuple(wallets),
                "native": tuple(_NATIVE_TOKEN_MARKERS),
                "before_block": before_block,
                "min_wallets": _MIN_RECURRING_WALLETS,
                "limit": limit,
            },
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows) if rows else None
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("cross_token_recurring_query_failed: %s", exc)
        return None


async def _kg_prior_token_overlap(
    wallets: list[str],
    limit: int = 20,
) -> list[tuple[str, int, list[str]]] | None:
    """Prior-token overlap via knowledge-graph HOLDS edges (secondary path).

    For each cluster wallet, reads the graph's wallet→token HOLDS neighbors and
    counts wallets per prior token; tokens held by >= ``_MIN_RECURRING_WALLETS``
    cluster wallets are recurring. Returns ``[(token, wallet_count, wallets)]``
    or ``None`` when the graph is unreachable / no overlap. Best-effort — never
    raises.
    """
    try:
        from app.knowledge_graph.engine import get_knowledge_graph
        from app.knowledge_graph.models import EdgeType, NodeType

        kg = await get_knowledge_graph()
        token_wallets: dict[str, list[str]] = {}
        for wallet in wallets:
            neighbors = await kg.get_neighbors(
                NodeType.WALLET,
                wallet,
                relation=EdgeType.HOLDS,
                direction="outgoing",
                limit=50,
            )
            for neighbor in neighbors:
                if neighbor.get("node_type") != NodeType.TOKEN:
                    continue
                token = str(neighbor.get("node_id") or "").lower()
                if token:
                    token_wallets.setdefault(token, []).append(wallet)

        results = [(tok, len(ws), ws) for tok, ws in token_wallets.items() if len(ws) >= _MIN_RECURRING_WALLETS]
        results.sort(key=lambda r: r[1], reverse=True)
        return results[:limit] or None
    except Exception as exc:  # pragma: no cover - defensive; never break the scan
        logger.debug("cross_token_kg_overlap_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_cross_token(key: str, data: dict[str, Any]) -> None:
    data["_cached_at"] = time.time()
    _CROSS_TOKEN_CACHE[key] = data
    if len(_CROSS_TOKEN_CACHE) > _CROSS_TOKEN_CACHE_MAX:
        _CROSS_TOKEN_CACHE.pop(next(iter(_CROSS_TOKEN_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _CROSS_TOKEN_CACHE,
        _CROSS_TOKEN_CACHE_MAX,
        _CROSS_TOKEN_CACHE_TTL,
        extra={"min_recurring_wallets": _MIN_RECURRING_WALLETS},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_CROSS_TOKEN_CACHE)

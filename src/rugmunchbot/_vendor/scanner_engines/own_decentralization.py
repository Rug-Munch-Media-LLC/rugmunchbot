# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_decentralization.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Decentralization — BubbleMaps-Style Holder Cluster Map + Score
===================================================================
Competitor feature for the deep scan: turns our own holder / funding data
into a BubbleMaps-style "cluster map" plus a 0-100 DECENTRALIZATION score.

Two compact, text-only renderings (no image rendering — honest, no new
services):

  * ``bubble_line`` — biggest holders as sized "bubbles" (emoji-scaled by
    %: 🟢 <1% / 🟡 1-5% / 🟠 5-10% / 🔴 >10%), followed by funding-cluster
    grouping (🕸️ cluster sizes) and a "time nodes" count (⏱️ wallets funded
    at the same time — same block + shared funder, i.e. coordinated buys):
        🫧 🔴🟠🟡🟡🟢🟢🟢🟢 | 🕸️ 5·3·2 | ⏱️ 2 same-time
  * ``decentralization_line`` — the score with a plain-language verdict:
        🫧 Decentralization: 34/100 (concentrated)

The 0-100 score is composed from three components (all own data):

  * Holder concentration  (0-40)  — top-10 / top-20 supply share
  * Funding diversity     (0-30)  — distinct first-funder clusters
  * Whale share           (0-30)  — supply held by >= 5% wallets

Clustering preference (fail-open): Neo4j entity edges
(SAME_AS / FUNDED_BY_SAME / CLONE_OF / BEHAVIORAL_MATCH) are tried first —
when the graph is reachable AND links at least two of the top holders, those
connected components become the clusters. Otherwise the already-built
ClickHouse first-funder clusters (``own_cluster_analysis``) are used. When
neither has data, the engine fails open to ``"—"`` and never fabricates a
score.

Reuses (never duplicates):
  - ``own_wallet_map.get_own_wallet_map`` — top holders + roles + % (holder
    data, ClickHouse first / Birdeye fallback)
  - ``own_cluster_analysis.get_own_cluster_analysis`` — first-funder
    clusters + coordinated (same-time) buys

Usage:
    from rugmunchbot._vendor.scanner_engines.own_decentralization import get_own_decentralization

    result = await get_own_decentralization("0x...", "ethereum")
    # result = {
    #     "check": "decentralization",
    #     "result": "scanned" | "unavailable",
    #     "source": "own",
    #     "decentralization_score": 34,
    #     "decentralization_label": "concentrated",
    #     "decentralization_line": "🫧 Decentralization: 34/100 (concentrated)",
    #     "bubble_line": "🫧 🔴🟠🟡🟡🟢🟢🟢🟢 | 🕸️ 5·3·2 | ⏱️ 2 same-time",
    #     "breakdown": {"top10_pct": ..., "whale_share": ..., "cluster_source": ...},
    #     "confidence": 0.8,
    # }
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
)
from rugmunchbot._vendor.scanner_engines._own_bundle_common import _normalize_chain
from rugmunchbot._vendor.scanner_engines.own_cluster_analysis import get_own_cluster_analysis
from rugmunchbot._vendor.scanner_engines.own_wallet_map import get_own_wallet_map

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_DECENTRALIZATION_CACHE: dict[str, dict[str, Any]] = {}
_DECENTRALIZATION_CACHE_TTL = 300  # 5 minutes — holder data changes slowly
_DECENTRALIZATION_CACHE_MAX = 100

#: Max holder bubbles rendered in the compact map line.
_MAX_BUBBLES = 10
#: Max cluster sizes rendered in the map line.
_MAX_CLUSTER_SIZES = 3
#: Whale threshold — matches own_wallet_map's whale/fish boundary.
_WHALE_PCT = 5.0

#: Bubble emoji by percentage bucket (BubbleMaps-style sizing).
_BUBBLE_TINY = "\U0001f7e2"  # 🟢 < 1%
_BUBBLE_SMALL = "\U0001f7e1"  # 🟡 1-5%
_BUBBLE_LARGE = "\U0001f7e0"  # 🟠 5-10%
_BUBBLE_HUGE = "\U0001f534"  # 🔴 > 10%

#: Neo4j relationship types used for wallet clustering (entity graph edges —
#: the transfer/relationship edges the on-prem Neo4j graph actually stores).
_NEO4J_REL_TYPES = "SAME_AS|FUNDED_BY_SAME|CLONE_OF|BEHAVIORAL_MATCH"


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_decentralization(
    token_address: str,
    chain: str,
    deployer: str | None = None,
    force_fresh: bool = False,
    wallet_map: dict[str, Any] | None = None,
    cluster_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """BubbleMaps-style cluster map + decentralization score for a token.

    Reuses ``own_wallet_map`` (top holders + roles) and
    ``own_cluster_analysis`` (first-funder clusters + same-time buys). When
    ``wallet_map`` / ``cluster_analysis`` are supplied (the deep-scan pipeline
    already fetched them), they are reused instead of re-querying. Neo4j
    entity edges are preferred for cluster grouping when reachable; the
    ClickHouse first-funder clusters are the fallback. Fail-open to ``"—"``
    when no holder data exists. Never raises.

    Args:
        token_address: Token address (lowercased internally).
        chain: Chain name (ethereum / bsc / solana / ...).
        deployer: Optional deployer address (passed through to the engines).
        force_fresh: Bypass the in-process cache.
        wallet_map: Pre-fetched ``get_own_wallet_map`` result (pipeline reuse).
        cluster_analysis: Pre-fetched ``get_own_cluster_analysis`` result.

    Returns:
        Dict with ``decentralization_line``, ``bubble_line``, the 0-100
        score and a ``breakdown``. Fail-open to ``"—"`` when holder data is
        not reachable.
    """
    token_address = (token_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain_norm}:{(deployer or '').lower()}"
    if not force_fresh and cache_key in _DECENTRALIZATION_CACHE:
        cached = _DECENTRALIZATION_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _DECENTRALIZATION_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    fail_open = _fail_open_result()

    try:
        if wallet_map is None:
            wallet_map = await get_own_wallet_map(token_address, chain_norm, deployer=deployer, force_fresh=force_fresh)
        if cluster_analysis is None:
            cluster_analysis = await get_own_cluster_analysis(
                token_address, chain_norm, deployer=deployer, force_fresh=force_fresh
            )
    except Exception as exc:  # pragma: no cover - defensive; never break a scan
        logger.debug("decentralization_input_fetch_failed: %s", exc)
        _cache_decentralization(cache_key, fail_open)
        return fail_open

    holders = (wallet_map or {}).get("classified") or []
    if not holders or str((wallet_map or {}).get("result")) != "scanned":
        # Fail-open: no holder data -> cannot build a bubble map or a score.
        _cache_decentralization(cache_key, fail_open)
        return fail_open

    # ── Neo4j-first clustering (entity/transfer-edge graph) ──
    holder_addrs = [str(h.get("address") or "") for h in holders if h.get("address")]
    try:
        neo4j_clusters, neo4j_used = await _query_neo4j_clusters(holder_addrs)
    except Exception as exc:  # pragma: no cover - defensive; never break a scan
        logger.debug("decentralization_neo4j_failed: %s", exc)
        neo4j_clusters, neo4j_used = [], False
    if neo4j_used and cluster_analysis is not None:
        cluster_analysis = _replace_clusters(cluster_analysis, neo4j_clusters)
    elif neo4j_used and cluster_analysis is None:
        cluster_analysis = {"clusters": neo4j_clusters, "distinct_funders": len(neo4j_clusters)}

    result = build_decentralization(wallet_map, cluster_analysis)
    if neo4j_used:
        result["breakdown"]["cluster_source"] = "neo4j"

    _cache_decentralization(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_decentralization_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:12],
        chain=chain_norm,
        score=result["decentralization_score"],
        line=result["bubble_line"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PURE SCORING + RENDERING (unit-testable without ClickHouse/Neo4j)
# ═══════════════════════════════════════════════════════════════════════════


def build_decentralization(
    wallet_map: dict[str, Any],
    cluster_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score + render from wallet-map and cluster-engine outputs (pure).

    Expects the outputs of ``get_own_wallet_map`` (``classified`` holders with
    ``percentage``) and ``get_own_cluster_analysis`` (``clusters``,
    ``coordinated_buyers``, ...). Tolerates a missing / ``unavailable``
    cluster payload — the score then rests on holder concentration + whale
    share alone. Raises nothing.
    """
    holders = wallet_map.get("classified") or []
    cluster = cluster_analysis or {}

    sorted_holders = sorted(holders, key=lambda h: float(h.get("percentage") or 0), reverse=True)
    top10_pct = round(sum(float(h.get("percentage") or 0) for h in sorted_holders[:10]), 2)
    top20_pct = round(sum(float(h.get("percentage") or 0) for h in sorted_holders[:20]), 2)
    whale_share = round(
        sum(float(h.get("percentage") or 0) for h in sorted_holders if float(h.get("percentage") or 0) >= _WHALE_PCT),
        2,
    )
    whale_count = sum(1 for h in sorted_holders if float(h.get("percentage") or 0) >= _WHALE_PCT)

    clusters = [c for c in (cluster.get("clusters") or []) if isinstance(c, dict)]
    distinct_funders = _to_int(cluster.get("distinct_funders"))
    funded_wallets = _to_int(cluster.get("funded_wallets"))
    largest_cluster_size = _to_int(cluster.get("largest_cluster_size"))
    time_nodes = _to_int(cluster.get("coordinated_buyers"))
    funding_chain_buyers = _to_int(cluster.get("funding_chain_buyers"))

    components = {
        "concentration": round(_score_concentration(top10_pct, top20_pct), 1),
        "funding_diversity": round(_score_funding_diversity(distinct_funders, funded_wallets, largest_cluster_size), 1),
        "whale_share": round(_score_whale_share(whale_share), 1),
    }
    score = max(0, min(100, round(sum(components.values()))))
    label = _decentralization_label(score)

    breakdown = {
        "top10_pct": top10_pct,
        "top20_pct": top20_pct,
        "whale_share": whale_share,
        "whale_count": whale_count,
        "distinct_funders": distinct_funders,
        "funded_wallets": funded_wallets,
        "largest_cluster_size": largest_cluster_size,
        "time_nodes": time_nodes,
        "funding_chain_buyers": funding_chain_buyers,
        "cluster_source": "clickhouse" if clusters else "none",
        "components": components,
    }

    return {
        "check": "decentralization",
        "result": "scanned",
        "source": "own",
        "decentralization_score": score,
        "decentralization_label": label,
        "decentralization_line": _build_decentralization_line(score, label),
        "bubble_line": f"\U0001fae7 {_build_bubble_line(sorted_holders, clusters, time_nodes)}",
        "breakdown": breakdown,
        "confidence": 0.8,
    }


def _score_concentration(top10_pct: float, top20_pct: float) -> float:
    """0-40 holder-concentration component.

    Penalty-free up to 20% top-10 / 40% top-20 supply; each point above costs
    linearly (top-10 weighted heavier). 100% top-10 collapses the component.
    """
    p10 = max(0.0, (top10_pct or 0.0) - 20.0)
    p20 = max(0.0, (top20_pct or 0.0) - 40.0)
    return max(0.0, 40.0 - p10 * 0.5 - p20 * 0.25)


def _score_funding_diversity(
    distinct_funders: int,
    funded_wallets: int,
    largest_cluster_size: int,
) -> float:
    """0-30 funding-diversity component.

    More distinct first-funders = more decentralized funding. A single
    funder arming >= 60% of the funded wallets halves the component
    (dominance penalty).
    """
    if distinct_funders <= 0:
        return 0.0
    if distinct_funders >= 10:
        base = 30.0
    elif distinct_funders >= 6:
        base = 24.0
    elif distinct_funders >= 4:
        base = 18.0
    elif distinct_funders >= 3:
        base = 12.0
    elif distinct_funders >= 2:
        base = 6.0
    else:
        base = 2.0
    if funded_wallets > 0 and largest_cluster_size > 0 and largest_cluster_size / funded_wallets >= 0.6:
        base *= 0.5
    return base


def _score_whale_share(whale_share: float) -> float:
    """0-30 whale-share component (supply held by >= 5% wallets)."""
    ws = whale_share or 0.0
    if ws <= 10.0:
        return 30.0
    if ws <= 25.0:
        return 20.0
    if ws <= 40.0:
        return 12.0
    if ws <= 55.0:
        return 6.0
    return 0.0


def _decentralization_label(score: int) -> str:
    """Plain-language verdict for a 0-100 score."""
    if score >= 67:
        return "healthy"
    if score >= 34:
        return "moderate"
    return "concentrated"


def _build_decentralization_line(score: int, label: str) -> str:
    """``🫧 Decentralization: 34/100 (concentrated)``."""
    return f"\U0001fae7 Decentralization: {score}/100 ({label})"


def _bubble_emoji(pct: float) -> str:
    """Sized bubble for a holder percentage (🟢<1% / 🟡1-5% / 🟠5-10% / 🔴>10%)."""
    if pct > 10.0:
        return _BUBBLE_HUGE
    if pct >= 5.0:
        return _BUBBLE_LARGE
    if pct >= 1.0:
        return _BUBBLE_SMALL
    return _BUBBLE_TINY


def _build_bubble_line(
    holders: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    time_nodes: int,
) -> str:
    """Compact BubbleMaps-style line.

    ``🫧 <bubbles> | 🕸️ <cluster sizes> | ⏱️ <N> same-time``. Cluster and
    time-node segments are omitted when absent — the bubbles alone stay.
    """
    bubbles = [_bubble_emoji(float(h.get("percentage") or 0)) for h in holders[:_MAX_BUBBLES]]
    if len(holders) > _MAX_BUBBLES:
        bubbles.append(f"+{len(holders) - _MAX_BUBBLES}")
    parts: list[str] = ["".join(bubbles)]

    if clusters:
        sizes = [str(_to_int(c.get("wallet_count"))) for c in clusters[:_MAX_CLUSTER_SIZES]]
        parts.append("\U0001f578\ufe0f " + "\u00b7".join(sizes))

    if time_nodes > 0:
        parts.append(f"\u23f1\ufe0f {time_nodes} same-time")

    return " \u2502 ".join(parts)


def _to_int(value: Any) -> int:
    """Graceful int conversion (0 on junk)."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# NEO4J CLUSTERING (transfer/entity-edge preference)
# ═══════════════════════════════════════════════════════════════════════════


async def _query_neo4j_clusters(holder_addrs: list[str]) -> tuple[list[dict[str, Any]], bool]:
    """Cluster top holders via Neo4j relationship edges.

    Returns ``(clusters, used)``. ``used`` is True only when Neo4j was
    reachable AND linked at least two of the given holders into a
    multi-wallet cluster. When False the caller falls back to the ClickHouse
    first-funder clusters. Fail-open: any Neo4j error returns ``([], False)``.
    """
    holders = [a.lower() for a in holder_addrs if a]
    if not holders:
        return [], False

    try:
        from app.domains.wallet.neo4j_client import get_neo4j_driver
    except ImportError:
        return [], False

    driver = get_neo4j_driver()
    if driver is None:
        return [], False

    def _run() -> list[tuple[str, list[str]]]:
        with driver.session() as session:
            result = session.run(
                f"""
                MATCH (w:Wallet)
                WHERE w.address IN $holders
                WITH collect(DISTINCT w.address) AS present
                UNWIND present AS a
                MATCH (wa:Wallet {{address: a}})
                OPTIONAL MATCH path = (wa)-[:{_NEO4J_REL_TYPES}*1..2]-(wb:Wallet)
                WHERE wb.address IN present AND wb.address <> wa.address
                WITH a, collect(DISTINCT wb.address) AS linked
                RETURN a, linked
                """,
                holders=holders,
            )
            return [(str(rec["a"]), [str(x) for x in (rec["linked"] or [])]) for rec in result]

    try:
        import asyncio

        rows = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.debug("decentralization_neo4j_cluster_failed: %s", exc)
        return [], False

    if not rows:
        return [], False

    clusters = _connected_components(rows)
    if not clusters:
        return [], False
    return clusters, True


def _connected_components(rows: list[tuple[str, list[str]]]) -> list[dict[str, Any]]:
    """Union-find connected components over holder -> linked-holder pairs.

    Returns clusters (``{"funder", "wallet_count", "wallets", "source"}``)
    with at least two wallets, sorted by size descending. Pure — unit-tested
    without a live graph.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, linked in rows:
        for b in linked:
            union(a, b)

    groups: dict[str, list[str]] = {}
    for a, _linked in rows:
        groups.setdefault(find(a), []).append(a)

    clusters = [
        {
            "funder": f"neo4j-{root[:10]}",
            "wallet_count": len(wallets),
            "wallets": wallets,
            "source": "neo4j",
        }
        for root, wallets in groups.items()
        if len(wallets) >= 2
    ]
    clusters.sort(key=lambda c: (c["wallet_count"], c["funder"]), reverse=True)
    return clusters


def _replace_clusters(cluster_analysis: dict[str, Any], clusters: list[dict[str, Any]]) -> dict[str, Any]:
    """Swap Neo4j clusters into a cluster-engine payload (keeps the rest)."""
    out = dict(cluster_analysis)
    out["clusters"] = clusters
    out["distinct_funders"] = len(clusters)
    out["largest_cluster_size"] = clusters[0]["wallet_count"] if clusters else 0
    return out


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _fail_open_result() -> dict[str, Any]:
    """Standard fail-open payload (no data -> never a fabricated score)."""
    return {
        "check": "decentralization",
        "result": "unavailable",
        "source": "own",
        "decentralization_score": None,
        "decentralization_label": None,
        "decentralization_line": "\u2014",  # —
        "bubble_line": "\u2014",  # —
        "breakdown": {},
        "confidence": 0.0,
    }


def _cache_decentralization(key: str, data: dict[str, Any]) -> None:
    """Cache decentralization data with timestamp."""
    data["_cached_at"] = time.time()
    _DECENTRALIZATION_CACHE[key] = data
    if len(_DECENTRALIZATION_CACHE) > _DECENTRALIZATION_CACHE_MAX:
        _DECENTRALIZATION_CACHE.pop(next(iter(_DECENTRALIZATION_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _DECENTRALIZATION_CACHE,
        _DECENTRALIZATION_CACHE_MAX,
        _DECENTRALIZATION_CACHE_TTL,
        extra={"max_bubbles": _MAX_BUBBLES, "max_cluster_sizes": _MAX_CLUSTER_SIZES},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_DECENTRALIZATION_CACHE)

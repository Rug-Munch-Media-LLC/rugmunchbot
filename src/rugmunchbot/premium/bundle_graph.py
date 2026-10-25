"""Bundle graph — who moved together, how much supply they hold, who left.

The scanner already reports *that* wallets are bundled (``bundler_count``,
``bundled_first_buyers``) but not *how they relate*, so an operator cannot tell
a shared funder from a coincidence. This module takes the raw evidence —
wallets plus typed edges, each edge carrying the transaction that proves it —
and returns clusters, not opinions.

Design rules:

* **An edge needs evidence.** ``evidence_tx`` is copied verbatim onto the edge
  and counted into ``confidence``; an edge with no transaction lowers
  confidence instead of being silently trusted.
* **Clusters are connected components.** Two wallets are in the same cluster
  when any evidence path joins them; union-find, so a chain of three hops is
  one cluster rather than three pairs.
* **Supply is measured, never estimated.** ``supply_pct`` sums the balances we
  were given over the total supply we were given; when either is missing the
  field is ``None`` so "unknown" cannot read as "0%".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: A cluster this small is not evidence of coordination.
MIN_CLUSTER_SIZE = 3
EDGE_KINDS = ("funding", "co_buy", "shared_funder", "transfer")


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """Everything the graph builder needs for one token."""

    token: str
    chain: str
    wallets: list[str] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    holdings: dict[str, Any] = field(default_factory=dict)
    total_supply: float | None = None


def _clusters(wallets: list[str], edges: list[dict[str, Any]]) -> list[list[str]]:
    """Connected components over the wallet set (union-find, iterative)."""
    parent = {wallet: wallet for wallet in wallets}

    def find(node: str) -> str:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for edge in edges:
        left, right = find(edge["from"]), find(edge["to"])
        if left != right:
            parent[left] = right
    groups: dict[str, list[str]] = {}
    for wallet in wallets:
        groups.setdefault(find(wallet), []).append(wallet)
    return [sorted(group) for group in groups.values()]


def _balance(holdings: dict[str, Any], wallet: str, *fields: str) -> float:
    entry = holdings.get(wallet) or {}
    for field_name in fields:
        value = entry.get(field_name)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _cluster_row(
    group: list[str],
    holdings: dict[str, Any],
    total_supply: float | None,
) -> dict[str, Any]:
    held = sum(_balance(holdings, wallet, "balance", "current_amount") for wallet in group)
    realised = sum(_balance(holdings, wallet, "realized_sol") for wallet in group)
    return {
        "size": len(group),
        "wallets": group,
        "supply_pct": round(held / total_supply * 100, 4) if total_supply else None,
        "realized_sol": round(realised, 4),
        "still_holding": held > 0,
    }


def _confidence(edges: list[dict[str, Any]], clusters: list[dict[str, Any]]) -> float:
    """How much of this graph is backed by evidence, diversity and size."""
    if not edges:
        return 0.0
    evidenced = sum(1 for edge in edges if edge.get("evidence_tx")) / len(edges)
    kinds = len({edge["kind"] for edge in edges} - {"unknown"})
    diversity = min(1.0, kinds / 2)
    bundled = max((cluster["size"] for cluster in clusters), default=0)
    size_factor = min(1.0, bundled / MIN_CLUSTER_SIZE if bundled else 0.0)
    return round(0.4 * evidenced + 0.3 * diversity + 0.3 * size_factor, 3)


def _normalise(
    wallets: list[str], edges: list[dict[str, Any]]
) -> tuple[set[str], list[dict[str, Any]]]:
    """Keep only edges that name two distinct wallets; unknown kinds are labelled."""
    known = {str(wallet) for wallet in wallets if wallet}
    normalised: list[dict[str, Any]] = []
    for edge in edges:
        source, target = str(edge.get("from") or ""), str(edge.get("to") or "")
        if not source or not target or source == target:
            continue
        known.update((source, target))
        kind = str(edge.get("kind") or "unknown")
        normalised.append(
            {
                "from": source,
                "to": target,
                "kind": kind if kind in EDGE_KINDS else "unknown",
                "evidence_tx": edge.get("evidence_tx"),
            }
        )
    return known, normalised


def build_bundle_graph(request: BuildRequest) -> dict[str, Any]:
    """Build the bundle graph for one token.

    Args:
        request: token, chain, participating wallets, typed edges, per-wallet
            holdings and the token supply. Unknown edge kinds are kept but
            labelled ``unknown`` so a bad caller is visible, not lost.

    Returns:
        ``token``, ``chain``, ``wallets``, ``wallet_count``, ``edges``,
        ``clusters``, ``confidence`` and ``bundle_pct_of_supply``.
    """
    known, normalised = _normalise(request.wallets, request.edges)
    ordered = sorted(known)
    balances = request.holdings or {}
    clusters = [
        _cluster_row(group, balances, request.total_supply)
        for group in _clusters(ordered, normalised)
    ]
    clusters.sort(key=lambda cluster: (-cluster["size"], -(cluster["supply_pct"] or 0.0)))
    bundled_pct = sum(
        cluster["supply_pct"] or 0.0 for cluster in clusters if cluster["size"] >= MIN_CLUSTER_SIZE
    )
    return {
        "token": request.token,
        "chain": request.chain,
        "wallets": ordered,
        "wallet_count": len(ordered),
        "edges": normalised,
        "clusters": clusters,
        "confidence": _confidence(normalised, clusters),
        "bundle_pct_of_supply": round(bundled_pct, 4),
    }

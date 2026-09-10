"""Deterministic fund-flow summary for /trace (bot-owned).

Moved out of the frozen ``_vendor/scanner_engines/wallet_flow`` copy during the
scanner-engine cutover. The canonical engine (``rugmunch-scanner``) exposes
``build_flow_graph`` but no bot-facing summary, so the summary lives here as a
pure function over the graph payload (no ClickHouse access, no detection).

The graph itself — hop-1/hop-2 ClickHouse queries, USD normalization, node
classification — comes from
``rugmunch_scanner.unified.engines.wallet_flow``.
"""

from __future__ import annotations

from typing import Any

from rugmunch_scanner.unified.engines.wallet_flow import build_flow_graph

__all__ = ["build_flow_summary"]

# Matches the canonical engine's node cap (kept local so we do not import a
# private constant across the package boundary).
_DEFAULT_MAX_NODES = 60


async def build_flow_summary(
    wallet: str,
    chain: str,
    hops: int = 2,
    max_nodes: int = _DEFAULT_MAX_NODES,
    force_fresh: bool = False,
) -> dict[str, Any] | None:
    """Deterministic fund-flow summary for /trace (own ClickHouse data).

    Runs the canonical :func:`build_flow_graph` (evm_transfers + wallet_labels
    + dex_events) and derives a compact, AI-safe summary:

      - in/out USD + tx counts (net position)
      - top-5 counterparties by USD, labeled + typed (CEX/DEX/bridge/mixer)
      - exchange vs non-exchange share (USD-based and tx-based)
      - wash signal: reciprocal (A→B AND B→A) transfer pairs

    Returns ``None`` when the underlying graph is not scanned (ClickHouse down,
    no transfer history, invalid inputs) so callers fail open.
    """
    graph = await build_flow_graph(
        wallet, chain, hops=hops, max_nodes=max_nodes, force_fresh=force_fresh
    )
    if not isinstance(graph, dict) or graph.get("result") != "scanned":
        return None
    return _summarize_flow(graph)


def _node_info(graph: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Map node id -> (type, label) for counterparty classification."""
    return {
        str(node.get("id") or ""): (str(node.get("type") or ""), str(node.get("label") or ""))
        for node in graph.get("nodes") or []
    }


def _accumulate_edges(
    edges: list[Any],
    wallet: str,
    node_info: dict[str, tuple[str, str]],
) -> tuple[
    dict[str, float],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    set[tuple[str, str]],
    dict[tuple[str, str], int],
]:
    """Fold edges into in/out totals and inbound/outbound counterparty buckets."""
    totals: dict[str, float] = {
        "in_usd": 0.0,
        "out_usd": 0.0,
        "in_tx": 0,
        "out_tx": 0,
        "exchange_in_usd": 0.0,
        "exchange_out_usd": 0.0,
        "exchange_tx": 0,
    }
    cp_in: dict[str, dict[str, Any]] = {}
    cp_out: dict[str, dict[str, Any]] = {}
    edge_pairs: set[tuple[str, str]] = set()
    edge_tx_by_pair: dict[tuple[str, str], int] = {}

    for edge in edges:
        frm = str(edge.get("from") or "").lower()
        to = str(edge.get("to") or "").lower()
        usd = float(edge.get("usd") or 0)
        tx = int(edge.get("tx_count") or 0)
        edge_pairs.add((frm, to))
        edge_tx_by_pair[(frm, to)] = edge_tx_by_pair.get((frm, to), 0) + tx

        if to == wallet:
            totals["in_usd"] += usd
            totals["in_tx"] += tx
            cp_type, cp_label = node_info.get(frm, ("", ""))
            bucket = cp_in.setdefault(
                frm,
                {
                    "address": frm,
                    "usd": 0.0,
                    "tx_count": 0,
                    "type": cp_type,
                    "label": cp_label or None,
                },
            )
            bucket["usd"] += usd
            bucket["tx_count"] += tx
            if cp_type == "CEX":
                totals["exchange_in_usd"] += usd
                totals["exchange_tx"] += tx
        elif frm == wallet:
            totals["out_usd"] += usd
            totals["out_tx"] += tx
            cp_type, cp_label = node_info.get(to, ("", ""))
            bucket = cp_out.setdefault(
                to,
                {
                    "address": to,
                    "usd": 0.0,
                    "tx_count": 0,
                    "type": cp_type,
                    "label": cp_label or None,
                },
            )
            bucket["usd"] += usd
            bucket["tx_count"] += tx
            if cp_type == "CEX":
                totals["exchange_out_usd"] += usd
                totals["exchange_tx"] += tx

    return totals, cp_in, cp_out, edge_pairs, edge_tx_by_pair


def _top_counterparties(counterparties: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Top-5 counterparties by (USD, tx_count), rounded for the prompt."""
    ranked = sorted(counterparties.values(), key=lambda c: (c["usd"], c["tx_count"]), reverse=True)
    return [
        {
            "address": c["address"],
            "usd": round(c["usd"], 2),
            "tx_count": c["tx_count"],
            "type": c["type"],
            "label": c["label"],
        }
        for c in ranked[:5]
    ]


def _summarize_flow(graph: dict[str, Any]) -> dict[str, Any]:
    """Derive the compact flow summary from a scanned wallet_flow graph."""
    wallet = str(graph.get("wallet") or "").lower()
    edges = graph.get("edges") or []
    node_info = _node_info(graph)
    totals, cp_in, cp_out, edge_pairs, edge_tx_by_pair = _accumulate_edges(edges, wallet, node_info)

    # Wash (round-trip) signal: reciprocal pairs A->B AND B->A present.
    # Canonical (f < t) direction so one round trip counts as ONE pair.
    wash_pairs = sorted((f, t) for (f, t) in edge_pairs if f < t and (t, f) in edge_pairs)
    wash_tx = sum(
        edge_tx_by_pair.get((f, t), 0) + edge_tx_by_pair.get((t, f), 0) for f, t in wash_pairs
    )

    in_usd = totals["in_usd"]
    out_usd = totals["out_usd"]
    total_usd = in_usd + out_usd
    total_tx = int(totals["in_tx"] + totals["out_tx"])
    usd_available = total_usd > 0
    exchange_usd = totals["exchange_in_usd"] + totals["exchange_out_usd"]

    return {
        "wallet": wallet,
        "chain": graph.get("chain") or "",
        "hops": graph.get("hops") or 1,
        "in_usd": round(in_usd, 2),
        "out_usd": round(out_usd, 2),
        "net_usd": round(out_usd - in_usd, 2),
        "in_tx_count": int(totals["in_tx"]),
        "out_tx_count": int(totals["out_tx"]),
        "total_tx_count": total_tx,
        "usd_unavailable": not usd_available,
        "counterparty_in": _top_counterparties(cp_in),
        "counterparty_out": _top_counterparties(cp_out),
        "exchange": {
            "in_usd": round(totals["exchange_in_usd"], 2),
            "out_usd": round(totals["exchange_out_usd"], 2),
            "tx_count": int(totals["exchange_tx"]),
            "share_pct": round(exchange_usd / total_usd * 100, 1) if usd_available else 0.0,
            "share_tx_pct": round(totals["exchange_tx"] / total_tx * 100, 1)
            if total_tx > 0
            else 0.0,
        },
        "wash": {
            "reciprocal_pairs": len(wash_pairs),
            "tx_count": wash_tx,
            "signal": wash_tx >= 3,
        },
        "edge_count": len(edges),
        "node_count": len(graph.get("nodes") or []),
        "note": graph.get("note"),
    }

"""RugMap cluster summary — pure reducer over the RugMaps graph payload.

``/rugmap`` already renders a PNG from ``/api/v1/rugmaps/token-graph``. This
module turns the SAME payload into the numbers the caption needs (top
clusters, bundle confidence, fresh wallets, AI opinion). It is a summary of
the graph — not a second map engine: no fetching, no layout, no pixels.

Missing data is never invented: every absent metric carries a ``note``
saying why it is absent, and the caption prints "n/a" plus that reason.
"""

from __future__ import annotations

from typing import Any

#: Risk ordering used to pick a cluster's worst member (3 = worst).
_RISK_RANK = {"high": 3, "medium": 2, "low": 1, "unknown": 0}

_MAX_CLUSTERS = 3


def _node_id(node: dict[str, Any]) -> str:
    """Return a node's wallet id, or "" when it carries none."""
    return str(node.get("address") or node.get("id") or "")


def _endpoint(link: dict[str, Any], key: str) -> str:
    """Return a link endpoint id (links may inline the node as a dict)."""
    value = link.get(key)
    if isinstance(value, dict):
        return _node_id(value)
    return str(value or "")


def _components(ids: list[str], links: list[dict[str, Any]]) -> list[list[str]]:
    """Group wallet ids into connected components (union-find, no 3rd party)."""
    parent = {i: i for i in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for link in links:
        a, b = _endpoint(link, "source"), _endpoint(link, "target")
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

    groups: dict[str, list[str]] = {}
    for i in ids:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _cluster_rows(nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarise the biggest connected wallet groups, largest first."""
    by_id = {_node_id(n): n for n in nodes if _node_id(n)}
    groups = [g for g in _components(list(by_id), links) if len(g) > 1]
    rows: list[dict[str, Any]] = []
    for group in sorted(groups, key=len, reverse=True)[:_MAX_CLUSTERS]:
        members = [by_id[i] for i in group]
        labels = [str(m.get("label") or "") for m in members if m.get("label")]
        pct = sum(float(m.get("pct") or m.get("supply_pct") or 0) for m in members)
        risk = max(
            (str(m.get("risk_level") or "unknown").lower() for m in members),
            key=lambda lvl: _RISK_RANK.get(lvl, 0),
        )
        rows.append(
            {
                "label": (labels[0] if labels else "Linked wallets")[:24],
                "wallets": len(group),
                "share_pct": round(pct, 1) if pct else None,
                "risk": risk,
            }
        )
    return rows


def _bundle_block(analysis: dict[str, Any], databus: dict[str, Any]) -> dict[str, Any]:
    """Bundle confidence: backend figure first, else derived, else explained."""
    raw = databus.get("bundle_confidence")
    if isinstance(raw, (int, float)):
        return {"confidence": int(raw), "note": "backend bundle metric"}
    flagged = analysis.get("bundle_wallets") or []
    if flagged:
        return {
            "confidence": min(90, 55 + 10 * len(flagged)),
            "note": f"derived from {len(flagged)} flagged bundler wallet(s)",
        }
    if databus.get("bundle_detected"):
        return {"confidence": 60, "note": "backend flagged a bundle, no score"}
    return {"confidence": None, "note": "backend returned no bundle metric"}


def _fresh_block(databus: dict[str, Any]) -> dict[str, Any]:
    """Share of wallets created shortly before launch."""
    raw = databus.get("fresh_wallet_pct")
    if isinstance(raw, (int, float)):
        return {"pct": round(float(raw), 1), "note": None}
    return {"pct": None, "note": "backend returned no fresh-wallet metric"}


def _ai_block(analysis: dict[str, Any]) -> dict[str, Any]:
    """AI/data-engine opinion carried by the token-graph response."""
    verdict = str(analysis.get("verdict") or "").upper() or None
    if not verdict:
        return {
            "verdict": None,
            "confidence": None,
            "summary": None,
            "source": None,
            "note": "token-graph returned no analysis block",
        }
    summary = str(analysis.get("summary") or "").strip()
    return {
        "verdict": verdict,
        "confidence": analysis.get("confidence"),
        "summary": summary[:160] or None,
        "source": str(analysis.get("source") or analysis.get("ai_model") or "rmi"),
        "note": None,
    }


def build_cluster_summary(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    analysis: dict[str, Any] | None = None,
    databus: dict[str, Any] | None = None,
    graph_source: str | None = None,
) -> dict[str, Any]:
    """Reduce one RugMaps graph payload into caption-ready metrics."""
    analysis = analysis or {}
    databus = databus or {}
    clusters = _cluster_rows(nodes, links)
    return {
        "clusters": clusters,
        "cluster_note": None if clusters else "no wallet group has a funding link",
        "bundle": _bundle_block(analysis, databus),
        "fresh": _fresh_block(databus),
        "ai": _ai_block(analysis),
        "source": graph_source or "rugmaps/token-graph",
        "node_count": len(nodes),
        "link_count": len(links),
    }


__all__ = ["build_cluster_summary"]

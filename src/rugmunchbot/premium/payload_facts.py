"""Read the bundle/cohort inputs out of a scanner payload, defensively.

The unified scanner returns whatever its modules managed to compute: on a
degraded provider the premium blobs are ``null``. This module never invents a
wallet or an edge to fill that hole — it returns empty collections, and every
downstream metric that depends on missing data stays ``None`` rather than
becoming a confident zero.
"""

from __future__ import annotations

from typing import Any


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in (value or []) if isinstance(row, dict)]


def _wallet(row: dict[str, Any]) -> str:
    return str(row.get("wallet") or row.get("address") or row.get("pubkey") or "")


def holders(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Top holders with their share, bundling flag and realised SOL."""
    bundles = {
        wallet
        for bundle in _rows((payload.get("bundle_analysis") or {}).get("bundles"))
        for wallet in (bundle.get("wallets") or [])
    }
    out: list[dict[str, Any]] = []
    for row in _rows(payload.get("top_holders")):
        wallet = _wallet(row)
        if not wallet:
            continue
        out.append(
            {
                "wallet": wallet,
                "pct": row.get("pct") if row.get("pct") is not None else row.get("percentage"),
                "realized_sol": row.get("realized_sol"),
                "bundled": wallet in bundles or bool(row.get("bundled")),
            }
        )
    return out


def first_buyers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """First buyers in launch order, with slot/rank and initial share."""
    out: list[dict[str, Any]] = []
    for index, row in enumerate(_rows(payload.get("first_buyers")), start=1):
        wallet = _wallet(row)
        if not wallet:
            continue
        out.append(
            {
                "wallet": wallet,
                "rank": row.get("rank") if isinstance(row.get("rank"), int) else index,
                "slot": row.get("slot"),
                "initial_pct": row.get("pct")
                if row.get("pct") is not None
                else row.get("initial_pct"),
                "sniper": row.get("sniper"),
            }
        )
    return out


def edges(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Typed edges that the scanner actually evidenced.

    Accepted shapes are the ones our engines emit (``funding_analysis`` with
    ``shared_funder``/``edges``, or ``bundle_analysis.bundles[].edges``). Any
    edge without an ``evidence_tx`` is still returned — the graph builder
    counts it against confidence instead of dropping it silently.
    """
    found: list[dict[str, Any]] = []
    funding = payload.get("funding_analysis") or {}
    for row in _rows(funding.get("edges")):
        found.append(row)
    shared = funding.get("shared_funder") or {}
    if isinstance(shared, dict):
        for wallet, funder in shared.items():
            if wallet and funder:
                found.append({"from": str(funder), "to": str(wallet), "kind": "shared_funder"})
    for bundle in _rows((payload.get("bundle_analysis") or {}).get("bundles")):
        for row in _rows(bundle.get("edges")):
            found.append(row)
    out: list[dict[str, Any]] = []
    for row in found:
        source = str(row.get("from") or row.get("funder") or "")
        target = str(row.get("to") or row.get("wallet") or "")
        if source and target and source != target:
            out.append(
                {
                    "from": source,
                    "to": target,
                    "kind": row.get("kind") or "funding",
                    "evidence_tx": row.get("evidence_tx") or row.get("tx") or row.get("signature"),
                }
            )
    return out


def holdings(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Wallet → still-held / realised amounts, from holders and first buyers."""
    out: dict[str, dict[str, Any]] = {}
    for row in holders(payload):
        entry = out.setdefault(row["wallet"], {})
        if row.get("pct") is not None:
            entry["current_pct"] = row["pct"]
            entry["balance"] = float(row["pct"])
        if row.get("realized_sol") is not None:
            entry["realized_sol"] = float(row["realized_sol"])
    for row in first_buyers(payload):
        entry = out.setdefault(row["wallet"], {})
        if row.get("initial_pct") is not None:
            entry["initial_pct"] = row["initial_pct"]
    return out

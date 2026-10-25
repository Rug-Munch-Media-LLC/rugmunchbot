"""Glue between the unified scanner and the premium derivations.

One place decides what "the facts for this token" means, so the export
endpoint, the watch cycle and the tests all derive the same graph from the
same payload instead of each assembling it slightly differently.

The scan is requested at the cheapest tier that still reports holders and
authorities; premium modules are read when a caller's scan already ran them,
never re-purchased here.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.premium import payload_facts, scan_port
from rugmunchbot.premium.bundle_graph import BuildRequest, build_bundle_graph
from rugmunchbot.premium.cohorts import cohort_rollup
from rugmunchbot.premium.snapshot import snapshot_from_scan


def labels_for(wallets: set[str]) -> dict[str, Any]:
    """KOL identities among these wallets, from the bot's identity store.

    ``pnl_sol`` is whatever the store knows — currently ``None`` because the
    bot DB does not track per-KOL PnL. It is passed through as ``None`` on
    purpose: a zero would read as "broke even".
    """
    try:
        from rugmunchbot.db.kol._helpers import wallet_entries
        from rugmunchbot.db.kol.identity import list_kol_identities

        entries = list_kol_identities(limit=500)
    except Exception:  # the KOL store is optional; no labels is a valid answer
        return {}
    labels: dict[str, Any] = {}
    for entry in entries:
        handle = entry.get("screen_name") or entry.get("handle")
        for held in wallet_entries(entry.get("wallets")):
            address = held.get("wallet") or held.get("address")
            if address in wallets:
                labels[str(address)] = {
                    "handle": handle,
                    "x_url": f"https://x.com/{handle}" if handle else None,
                    "pnl_sol": entry.get("profit"),
                }
    return labels


def derive(scan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return ``(snapshot, bundle_graph, cohort_rollup)`` for one scan payload."""
    snapshot = snapshot_from_scan(scan)
    holders = payload_facts.holders(scan)
    buyers = payload_facts.first_buyers(scan)
    wallets = {row["wallet"] for row in [*holders, *buyers]}
    graph = build_bundle_graph(
        BuildRequest(
            token=snapshot["token"],
            chain=snapshot["chain"],
            wallets=sorted(wallets),
            edges=payload_facts.edges(scan),
            holdings=payload_facts.holdings(scan),
            total_supply=scan.get("total_supply"),
        )
    )
    return snapshot, graph, cohort_rollup(holders, buyers, labels_for(wallets))


async def scan_payload(
    token: str,
    chain: str,
    *,
    scanner: scan_port.ScanProvider | None = None,
) -> dict[str, Any]:
    """Run the scanner and return it in the dict shape the exporters expect.

    *scanner* is the injected port (the composition root registers the real one
    once). Callers may pass their own for a test or a backtest; premium never
    imports ``rugmunchbot.scanner`` / ``rugmunchbot.tools`` itself.
    """
    # The bot's canonical scan client already returns the unified payload dict;
    # ``None`` means the backend refused, and an empty dict keeps the exporters
    # honest about having no facts rather than inventing a shape.
    return await scan_port.resolve(scanner)(token, chain) or {}

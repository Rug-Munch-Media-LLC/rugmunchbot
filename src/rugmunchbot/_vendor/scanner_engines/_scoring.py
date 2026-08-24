# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_scoring.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pure scoring / aggregation math for funding_analysis (degodding wave 6).

Extracted from ``engines/funding_analysis.py`` so the unit-testable
"scoring" section (token-level wallet-freshness aggregation + thresholds)
lives beside the other pure engine helpers instead of the ClickHouse
orchestration layer.

Every function here is pure with respect to the query layer: inputs are
already-fetched holder details, outputs are plain dicts. No ClickHouse or
Redis access. Imports are one-directional: ``funding_analysis`` imports
these helpers; the helpers never import ``funding_analysis``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.scanner_engines._funding_results import _wallet_metrics_unavailable

# ── Token-level wallet freshness metrics (compute_wallet_metrics) ────────
_FRESH_WALLET_AGE_DAYS = 30  # holder wallet < 30 days old => "fresh"


def _aggregate_wallet_metrics(
    holder_details: list[dict[str, Any]],
    token_address: str,
    chain: str,
    coverage_start_ts: str | None = None,
    coverage_days: float | None = None,
) -> dict[str, Any]:
    """Aggregate per-holder freshness details into token-level percentages.

    Pure logic (unit-testable without ClickHouse):
      - ``fresh_wallet_pct`` — holders with wallet age < 30 days
      - ``cex_funded_pct`` — holders whose first funder is a CEX wallet
      - ``first_tx_pct`` — holders whose first-ever on-chain tx is this token

    ``coverage_start_ts`` / ``coverage_days`` describe how far back the
    indexer's transfer history goes for this token. When the window starts
    long after the token launched, fresh/brand-new percentages are artifacts
    of the gap and MUST be caveated by the caller (see the bot formatter).
    """
    analyzed = len(holder_details)
    if not analyzed:
        return _wallet_metrics_unavailable(token_address, chain, reason="no holder data")

    fresh_count = sum(1 for d in holder_details if d.get("fresh"))
    cex_count = sum(1 for d in holder_details if d.get("funding_type") == "exchange_funded")
    first_tx_count = sum(1 for d in holder_details if d.get("first_tx_is_token"))

    return {
        "check": "wallet_metrics",
        "result": "scanned",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "holders_analyzed": analyzed,
        "fresh_wallet_pct": round(fresh_count / analyzed * 100, 1),
        "cex_funded_pct": round(cex_count / analyzed * 100, 1),
        "first_tx_pct": round(first_tx_count / analyzed * 100, 1),
        "fresh_wallet_count": fresh_count,
        "cex_funded_count": cex_count,
        "first_tx_count": first_tx_count,
        "holder_details": holder_details,
        "coverage_start_ts": coverage_start_ts,
        "coverage_days": coverage_days,
        "note": (
            "fresh_wallet_pct = holders with wallet <30 days old; "
            "cex_funded_pct = holders whose first funder is a CEX wallet; "
            "first_tx_pct = holders whose first-ever on-chain tx is this token; "
            "coverage_start_ts = start of the indexer's transfer window for this token "
            "(fresh stats unreliable when it postdates the token launch)"
        ),
    }

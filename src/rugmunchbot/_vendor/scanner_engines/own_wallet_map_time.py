# Split from the vendored own_wallet_map.py during the de-godding program
# 2026-08-22. Blocks moved VERBATIM by AST line-span slicing — see the
# facade own_wallet_map.py for provenance and re-exports.
# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_wallet_map.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Wallet Map — First-20 Buyer/Holder Role Classification
============================================================
Competitor feature for the deep scan: classifies the first ~20 buyers /
holders of a token into emoji roles so the report can show at a glance
who owns the early supply:

  - 🛠️  Dev      — the deployer wallet itself
  - 🐳  Whale    — large holder (>= 5% of tracked supply)
  - 🐟  Fish     — medium holder (1%..5%)
  - 🍤  Shrimp   — small holder (< 1%)
  - 🌱  Fresh    — brand-new wallet (first on-chain tx < 7 days ago)
  - 📦  Bundle   — wallet sharing its first funder with >= 1 other holder

Classification uses holder size (balance %), buy-time (first on-chain
tx timestamp), and funding source (first funder). Data comes from our
own ClickHouse first (holder_balances / evm_transfers /
solana_spl_balance_changes via ``_funding_queries``), with a Birdeye
holder-distribution fallback when ClickHouse has no record.

Fail-open by design: returns ``wallet_map_line: "—"`` when holder /
funding data is not reachable. Never fabricates roles for missing data.

Usage:
    from rugmunchbot._vendor.scanner_engines.own_wallet_map import get_own_wallet_map

    result = await get_own_wallet_map("0x...", "ethereum", deployer="0x...")
    # result = {
    #     "check": "wallet_map",
    #     "result": "scanned",
    #     "wallet_map_line": "🛠️2 🐳1 🐟8 🍤7 🌱1 📦1",
    #     "roles": {"dev": 2, "whale": 1, "fish": 8, "shrimp": 7, "fresh": 1, "bundle": 1},
    #     "classified": [...],
    #     "buyer_timing": {
    #         "result": "scanned",
    #         "fresh_wallet_pct": 62, "fresh_wallet_count": 8,
    #         "recently_funded_pct": 71, "recently_funded_count": 10,
    #         "fresh_window_hours": 24, "funded_window_hours": 6,
    #         "breakdown": [...],
    #     },
    #     "fresh_wallet_line": "🌱 Fresh wallets: 62% (created <24h before buy)",
    #     "recently_funded_line": "💸 Recently funded: 71% (funded <6h before buy)",
    #     "cex_funding": {
    #         "breakdown": {"Binance": 3, "Coinbase": 1},
    #         "unknown_count": 4,
    #         "total_classified_funders": 8,
    #     },
    #     "cex_funding_line": "🏦 CEX funding: Binance 38% · Coinbase 12% · unknown 50%",
    #     "deployer_funding": {"source": "cex", "label": "Binance"},
    #     "deployer_funding_line": "🏦 Deployer funded by: Binance",
    # }
    #
    # ``buyer_timing`` is the deeper fresh-wallet / recently-funded analysis:
    # what fraction of the first-buyers were armed right before they bought.
    # Percentages are honest — the denominator is only holders with BOTH
    # required timestamps, and absent data renders "—", never a fabricated 0%.
    #
    # ``cex_funding`` / ``cex_funding_line`` answer "who's behind this": each
    # holder's first funder is classified against the internal entity registry
    # (~28 CEX hot wallets) — the "% funded by each CEX" breakdown with the
    # non-CEX (OTC / fresh / unlabeled) bucket reported as ``unknown``.
    # ``deployer_funding`` / ``deployer_funding_line`` say what funded the
    # deployer (CEX vs mixer vs fresh vs unknown). Both are honest: missing
    # funder data renders "—", never a fabricated percentage.

Data Sources:
  Primary: wallet_memory.holder_balances / evm_transfers /
           solana_spl_balance_changes (via _funding_queries)
  Fallback: Birdeye token overview holder distribution
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _is_fresh(first_ts: Any, cutoff: datetime) -> bool:
    """True when the wallet's first on-chain tx is newer than ``cutoff``."""
    if isinstance(first_ts, datetime):
        naive = first_ts.replace(tzinfo=None) if first_ts.tzinfo else first_ts
        return naive > cutoff
    try:
        parsed = datetime.fromisoformat(str(first_ts).replace("Z", "+00:00"))
        naive = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        return naive > cutoff
    except (TypeError, ValueError):
        return False


def _to_iso(value: Any) -> str | None:
    """ISO string for datetime values, else None (graceful)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_naive_dt(value: Any) -> datetime | None:
    """Normalize a ClickHouse/ISO datetime to naive UTC (None on garbage)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return None


def _hours_between(earlier: Any, later: Any) -> float | None:
    """Whole hours from ``earlier`` to ``later`` (None when either is missing)."""
    a, b = _to_naive_dt(earlier), _to_naive_dt(later)
    if a is None or b is None:
        return None
    try:
        return (b - a).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None



# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_wallet_map_time.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Buyer-timing analysis + cache management (de-godding 2026-08-23)."""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._funding_queries import _query_funder_tx_counts
from rugmunchbot._vendor.scanner_engines.own_wallet_map_support import (
    _to_iso,
    _FRESH_WALLET_HOURS,
    _MAX_HOLDERS,
    _RECENT_FUNDED_HOURS,
    _ROLE_ORDER,
    _WALLET_MAP_CACHE_MAX,
    _WALLET_MAP_CACHE_TTL,
    _hours_between,
    _is_fresh,
    _to_naive_dt,
)

_WALLET_MAP_CACHE: dict[str, dict[str, Any]] = {}

async def _is_fresh_funder(funder: str, chain_norm: str) -> bool:
    """True when the funder has < 10 total on-chain transfers (fresh heuristic)."""
    try:
        rows = await run_sync(_query_funder_tx_counts, [funder], chain_norm)
        if rows and rows[0] and rows[0][1] is not None:
            return int(rows[0][1]) < 10
    except Exception as exc:
        logger.debug("wallet_map_funder_tx_count_failed: %s", exc)
    return False

def _compute_buyer_timing(holders: list[dict[str, Any]]) -> dict[str, Any]:
    """Fresh-wallet + recently-funded % over the classified first-buyers.

    Reads ``first_tx_ts`` (wallet birth), ``funding_ts`` (first funding
    event) and ``buy_ts`` (first buy of the scanned token) off each holder.

    Returns a payload with per-holder breakdown + aggregate percentages:
    ``fresh_wallet_pct`` / ``recently_funded_pct`` are ``None`` (rendered
    "—") when no holder in that cohort has both required timestamps.
    """
    fresh_denom = 0
    fresh_count = 0
    funded_denom = 0
    funded_count = 0
    breakdown: list[dict[str, Any]] = []

    for h in holders:
        addr = str(h.get("address") or "")
        birth = _to_naive_dt(h.get("first_tx_ts"))
        funding = _to_naive_dt(h.get("funding_ts"))
        buy = _to_naive_dt(h.get("buy_ts"))
        entry: dict[str, Any] = {"address": addr, "buy_ts": _to_iso(h.get("buy_ts"))}

        birth_buy_h = _hours_between(birth, buy)
        if birth is not None and buy is not None and birth_buy_h is not None:
            fresh_denom += 1
            is_fresh = 0.0 <= birth_buy_h <= _FRESH_WALLET_HOURS
            if is_fresh:
                fresh_count += 1
            entry["fresh_pre_buy"] = is_fresh
            entry["wallet_age_at_buy_hours"] = round(birth_buy_h, 1)

        funding_buy_h = _hours_between(funding, buy)
        if funding is not None and buy is not None and funding_buy_h is not None:
            funded_denom += 1
            is_funded = 0.0 <= funding_buy_h <= _RECENT_FUNDED_HOURS
            if is_funded:
                funded_count += 1
            entry["recently_funded"] = is_funded
            entry["fund_to_buy_hours"] = round(funding_buy_h, 1)

        breakdown.append(entry)

    return {
        "result": "scanned" if (fresh_denom or funded_denom) else "unavailable",
        "fresh_wallet_count": fresh_count,
        "fresh_wallet_denominator": fresh_denom,
        "fresh_wallet_pct": round(fresh_count / fresh_denom * 100) if fresh_denom else None,
        "fresh_window_hours": _FRESH_WALLET_HOURS,
        "recently_funded_count": funded_count,
        "recently_funded_denominator": funded_denom,
        "recently_funded_pct": round(funded_count / funded_denom * 100) if funded_denom else None,
        "funded_window_hours": _RECENT_FUNDED_HOURS,
        "breakdown": breakdown,
    }

def _build_timing_lines(timing: dict[str, Any]) -> tuple[str, str]:
    """Display lines for the deep scan, e.g. ``🌱 Fresh wallets: 62% (created <24h before buy)``.

    Returns ``("—", "—")`` when the corresponding percentage is absent —
    the deep-scan renderer then omits the line entirely.
    """
    fresh_pct = timing.get("fresh_wallet_pct")
    funded_pct = timing.get("recently_funded_pct")
    fresh_line = "\u2014"
    funded_line = "\u2014"
    if fresh_pct is not None:
        fresh_line = (
            f"\U0001f331 Fresh wallets: {int(fresh_pct)}% "
            f"(created <{int(timing.get('fresh_window_hours') or _FRESH_WALLET_HOURS)}h before buy)"
        )
    if funded_pct is not None:
        funded_line = (
            f"\U0001f4b8 Recently funded: {int(funded_pct)}% "
            f"(funded <{int(timing.get('funded_window_hours') or _RECENT_FUNDED_HOURS)}h before buy)"
        )
    return fresh_line, funded_line

def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _WALLET_MAP_CACHE,
        _WALLET_MAP_CACHE_MAX,
        _WALLET_MAP_CACHE_TTL,
        extra={"max_holders": _MAX_HOLDERS},
    )

def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_WALLET_MAP_CACHE)

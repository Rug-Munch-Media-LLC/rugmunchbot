# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_price_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pure helpers for the price/decimals oracle (Batch 13 degodding).

Extracted from ``price_oracle.py``: chain/token normalization and the
weighted-price collection loop shared by the VWAP paths
(``_vwap_price_from_swaps`` and ``_own_price_vwap``). No I/O, no cache
state, no ClickHouse/Redis dependencies.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.scanner_engines._price_adapters import _CHAIN_MAP


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to ClickHouse / provider format."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _normalize_token(token_address: str, chain: str) -> str:
    """Normalize token address for lookups.

    EVM addresses are lowercased; Solana base58 addresses are
    case-sensitive and preserved as-is.
    """
    token_address = str(token_address).strip()
    if chain == "solana":
        return token_address
    return token_address.lower()


def _collect_weighted_prices(
    swaps: list[dict[str, Any]],
    token: str,
    *,
    pair_cache: dict[str, tuple[Any, ...]] | None = None,
    strict_match: bool = True,
) -> tuple[list[tuple[float, float]], dict[str, int]]:
    """Collect (price, weight) pairs + paired-token counts from raw swaps.

    Shared by both VWAP paths in ``price_oracle.py``. Accepts an optional
    ``pair_cache`` (contract -> (token0, token1)) used to resolve swaps with
    empty token0/token1 columns (own_dex_data historical rows).

    ``strict_match`` mirrors the original loop semantics: ``True`` skips
    swaps where neither token0 nor token1 is the requested token
    (``_vwap_price_from_swaps``); ``False`` matches the legacy
    ``_own_price_vwap`` behavior of trusting the caller's filtered swap set.
    """
    prices_weighted: list[tuple[float, float]] = []
    paired_counts: dict[str, int] = {}
    token_lower = str(token).lower()

    for swap in swaps:
        t0 = str(swap.get("token0") or "").lower()
        t1 = str(swap.get("token1") or "").lower()
        if not t0 or not t1:
            if pair_cache:
                cached = pair_cache.get(str(swap.get("contract_address") or "").lower())
                if cached:
                    t0, t1 = (cached[0].lower(), cached[1].lower())
            if not t0 or not t1:
                continue

        is_token0 = t0 == token_lower
        if strict_match and not is_token0 and t1 != token_lower:
            continue
        paired = t1 if is_token0 else t0
        if not paired:
            continue
        paired_counts[paired] = paired_counts.get(paired, 0) + 1

        try:
            if is_token0:
                amt_in = float(swap.get("amount0_in") or 0)
                amt_out = float(swap.get("amount0_out") or 0)
                paired_in = float(swap.get("amount1_in") or 0)
                paired_out = float(swap.get("amount1_out") or 0)
            else:
                amt_in = float(swap.get("amount1_in") or 0)
                amt_out = float(swap.get("amount1_out") or 0)
                paired_in = float(swap.get("amount0_in") or 0)
                paired_out = float(swap.get("amount0_out") or 0)
        except (ValueError, TypeError):
            continue

        weight = amt_in + amt_out
        if weight <= 0:
            continue
        # buy: our token out, paired in; sell: our token in, paired out
        if amt_out > 0 and paired_in > 0:
            price = paired_in / amt_out
        elif amt_in > 0 and paired_out > 0:
            price = paired_out / amt_in
        else:
            continue
        if 0 < price < 1_000_000_000:
            prices_weighted.append((price, weight))

    return prices_weighted, paired_counts

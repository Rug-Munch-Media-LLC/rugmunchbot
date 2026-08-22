# DE-GODDED 2026-08-22: fetch/enrich/classify logic moved verbatim to
# own_wallet_map_time.py + own_wallet_map_intel.py; this module stays the
# public facade (orchestrator + cache + re-exports). Tripwire in
# scripts/refresh-vendors.sh --check guards against vendor-refresh regrowth.
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

import time
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
)

# Verbatim-split re-exports (2026-08-22): implementations live in sibling
# modules; this facade keeps the public import path stable.
from rugmunchbot._vendor.scanner_engines.own_wallet_map_intel import (  # noqa: F401
    _CEX_DISPLAY,
    _FISH_PCT,
    _FRESH_WALLET_DAYS,
    _KNOWN_ROUTER_WALLETS,
    _MAX_HOLDERS,
    _ROLE_ORDER,
    _WHALE_PCT,
    ROLE_EMOJI,
    _bundle_funders,
    _cex_display_name,
    _classify_deployer_funding,
    _classify_funder_cex,
    _classify_funder_mixer,
    _classify_holders,
    _compute_cex_funding,
    _enrich_buy_ts,
    _enrich_holders,
    _fetch_birdeye_holders,
    _fetch_holders,
    _fetch_wallet_funder,
    _is_fresh_funder,
)
from rugmunchbot._vendor.scanner_engines.own_wallet_map_time import (
    _hours_between,
    _to_iso,
    _to_naive_dt,
)

logger = get_logger(__name__)


_WALLET_MAP_CACHE: dict[str, dict[str, Any]] = {}


_WALLET_MAP_CACHE_TTL = 300  # 5 minutes — holder data changes slowly


_WALLET_MAP_CACHE_MAX = 100


_FRESH_WALLET_HOURS = 24


_RECENT_FUNDED_HOURS = 6


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return (chain or "").lower().strip()


async def get_own_wallet_map(
    token_address: str,
    chain: str,
    deployer: str | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Classify the first ~20 buyers/holders into emoji roles.

    Args:
        token_address: Token address (lowercased internally).
        chain: Chain name (ethereum / bsc / solana / ...).
        deployer: Optional deployer address — matched for the 🛠️ Dev role.
        force_fresh: Bypass the in-process cache.

    Returns:
        Dict with ``wallet_map_line`` (compact emoji map) plus the role
        counts and per-holder classification. Fail-open to ``"—"`` when
        holder / funding data is not reachable.
    """
    token_address = (token_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain_norm}:{(deployer or '').lower()}"
    if not force_fresh and cache_key in _WALLET_MAP_CACHE:
        cached = _WALLET_MAP_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _WALLET_MAP_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "wallet_map",
        "result": "unavailable",
        "wallet_map_line": "\u2014",  # —
        "roles": dict.fromkeys(_ROLE_ORDER, 0),
        "classified": [],
        "buyer_timing": {},
        "fresh_wallet_line": "\u2014",
        "recently_funded_line": "\u2014",
        "cex_funding": {"breakdown": {}, "unknown_count": 0, "total_classified_funders": 0},
        "cex_funding_line": "\u2014",  # —
        "deployer_funding": {},
        "deployer_funding_line": "\u2014",  # —
        "confidence": 0.0,
    }

    try:
        holders = await _fetch_holders(token_address, chain_norm)
    except Exception as exc:
        logger.debug("wallet_map_holder_fetch_failed: %s", exc)
        holders = []

    if not holders:
        logger.debug("no holder data for %s", token_address[:12])
        return result

    # ── Enrich: buy-time (wallet birth) + funding source ──
    try:
        first_tx, funders, funding_ts = await _enrich_holders([h["address"] for h in holders], chain_norm)
    except Exception as exc:
        logger.debug("wallet_map_enrich_failed: %s", exc)
        first_tx, funders, funding_ts = {}, {}, {}

    # ── Enrich: first buy of THIS token per holder (drives buyer timing) ──
    try:
        buy_ts = await _enrich_buy_ts(token_address, [h["address"] for h in holders], chain_norm)
    except Exception as exc:
        logger.debug("wallet_map_buy_ts_failed: %s", exc)
        buy_ts = {}

    for h in holders:
        addr = h["address"]
        h["first_tx_ts"] = first_tx.get(addr)
        h["funder"] = funders.get(addr)
        h["funding_ts"] = funding_ts.get(addr)
        h["buy_ts"] = buy_ts.get(addr)

    classified = _classify_holders(holders, deployer=deployer)
    roles = dict.fromkeys(_ROLE_ORDER, 0)
    for entry in classified:
        roles[entry["role"]] += 1

    # ── Buyer timing: fresh-wallet % + recently-funded % (honest, "—" absent) ──
    timing = _compute_buyer_timing(holders)
    fresh_line, funded_line = _build_timing_lines(timing)

    # ── CEX funding: which exchanges funded the holders' wallets (honest) ──
    cex_breakdown, cex_unknown, cex_total = _compute_cex_funding(classified, chain_norm)
    # ── Deployer funding source: CEX vs mixer vs fresh vs unknown (one line) ──
    deployer_funding = await _classify_deployer_funding(deployer, chain_norm, classified)

    result.update(
        {
            "result": "scanned",
            "wallet_map_line": _build_map_line(roles),
            "roles": roles,
            "classified": classified,
            "buyer_timing": timing,
            "fresh_wallet_line": fresh_line,
            "recently_funded_line": funded_line,
            "cex_funding": {
                "breakdown": cex_breakdown,
                "unknown_count": cex_unknown,
                "total_classified_funders": cex_total,
            },
            "cex_funding_line": _build_cex_funding_line(cex_breakdown, cex_unknown, cex_total),
            "deployer_funding": {k: v for k, v in deployer_funding.items() if k != "deployer_funding_line"},
            "deployer_funding_line": deployer_funding.get("deployer_funding_line", "\u2014"),
            "confidence": 0.8,
        }
    )

    _cache_map(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_wallet_map_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:12],
        chain=chain_norm,
        line=result["wallet_map_line"],
    )
    return result


def _build_cex_funding_line(breakdown: dict[str, int], unknown: int, total: int) -> str:
    """Compact funding line, e.g. ``🏦 CEX funding: Binance 38% · Coinbase 12% · unknown 50%``.

    Top CEXes (max 4) + the unknown bucket. Fail-open to "—" when no holder
    has a resolvable first-funder.
    """
    if total <= 0:
        return "\u2014"
    parts: list[str] = []
    for cex, count in sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)[:4]:
        parts.append(f"{cex} {round(count / total * 100)}%")
    if unknown:
        parts.append(f"unknown {round(unknown / total * 100)}%")
    if not parts:
        return "\u2014"
    return "\U0001f3e6 CEX funding: " + " \u00b7 ".join(parts)


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


def _build_map_line(roles: dict[str, int]) -> str:
    """Compact emoji map line, e.g. ``🛠️2 🐳1 🐟8 🍤7 🌱1 📦1``."""
    parts = [f"{ROLE_EMOJI[role]}{roles.get(role, 0)}" for role in _ROLE_ORDER]
    return " ".join(parts)


def _cache_map(key: str, data: dict[str, Any]) -> None:
    """Cache wallet map data with timestamp."""
    data["_cached_at"] = time.time()
    _WALLET_MAP_CACHE[key] = data
    if len(_WALLET_MAP_CACHE) > _WALLET_MAP_CACHE_MAX:
        _WALLET_MAP_CACHE.pop(next(iter(_WALLET_MAP_CACHE)), None)


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



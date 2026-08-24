# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_wallet_map.py (frozen copy; do not edit).
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
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._funding_queries import (
    _query_first_funders,
    _query_holder_first_buy,
    _query_holder_first_tx,
    _query_top_holders,
    _query_wallet_first_funder,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────

# ── Role definitions ──────────────────────────────────────────────────────

#: A holder whose wallet was born (first on-chain tx) within this window is "fresh".
#: Buyer-timing window: a first-buyer's wallet counts as "fresh" when its
#: first-ever on-chain tx happened within this many hours BEFORE the buy.
#: Buyer-timing window: a first-buyer counts as "recently funded" when its
#: first funding event happened within this many hours BEFORE the buy.
#: Whale / fish size thresholds (percentage of tracked supply).
#: Max holders classified (the "first 20").

#: DEX router / aggregator contracts that routinely appear as a holder's first
#: funder (the buy went through the router). They are infrastructure, not
#: evidence of a coordinated bundle — a shared router funder must never
#: classify holders as 📦. Lowercased (matches ``_enrich_holders`` output).




#: Human display names for CEX registry keys (entity_registry / known_entities).



# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


from rugmunchbot._vendor.scanner_engines.own_wallet_map_intel import (  # noqa: E402,F401
    _build_cex_funding_line,
    _bundle_funders,
    _cex_display_name,
    _classify_funder_cex,
    _classify_funder_mixer,
    _classify_holders,
    _compute_cex_funding,
)
from rugmunchbot._vendor.scanner_engines.own_wallet_map_support import (  # noqa: E402,F401
    _FRESH_WALLET_HOURS,
    _MAX_HOLDERS,
    _RECENT_FUNDED_HOURS,
    _ROLE_ORDER,
    _WALLET_MAP_CACHE_MAX,
    _WALLET_MAP_CACHE_TTL,
    ROLE_EMOJI,
    _is_fresh,
    _normalize_chain,
    _to_naive_dt,
)
from rugmunchbot._vendor.scanner_engines.own_wallet_map_time import (  # noqa: E402,F401
    _WALLET_MAP_CACHE,
    _build_timing_lines,
    _compute_buyer_timing,
    _is_fresh_funder,
    clear_cache,
    get_cache_stats,
)


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


# ═══════════════════════════════════════════════════════════════════════════
# DATA ACCESS
# ═══════════════════════════════════════════════════════════════════════════


async def _fetch_holders(token_address: str, chain_norm: str) -> list[dict[str, Any]]:
    """Top-N holder list: ClickHouse first, Birdeye distribution fallback.

    Returns a list of ``{"address", "balance", "percentage"}`` dicts sorted
    by balance descending (max ``_MAX_HOLDERS``). Empty list when no data.
    """
    holders: list[dict[str, Any]] = []

    try:
        rows = await run_sync(_query_top_holders, token_address, chain_norm, _MAX_HOLDERS)
        if rows:
            total = sum(float(r[1] or 0) for r in rows)
            if total > 0:
                for addr, balance in rows:
                    addr_s = str(addr or "").lower()
                    if not addr_s:
                        continue
                    holders.append(
                        {
                            "address": addr_s,
                            "balance": float(balance or 0),
                            "percentage": round(float(balance or 0) / total * 100, 2),
                        }
                    )
    except Exception as exc:
        logger.debug("wallet_map_top_holders_failed: %s", exc)

    if holders:
        return holders[:_MAX_HOLDERS]

    # ── Fallback: Birdeye token overview holder distribution ──
    return await _fetch_birdeye_holders(token_address)


async def _fetch_birdeye_holders(token_address: str) -> list[dict[str, Any]]:
    """Birdeye token-overview holder distribution (graceful fallback)."""
    try:
        from app.birdeye_client import BirdeyeClient

        client = BirdeyeClient()
        try:
            overview = await client.get_token_overview(token_address)
        finally:
            await client.close()

        if not isinstance(overview, dict):
            return []
        nested = overview.get("data")
        data: dict[str, Any] = nested if isinstance(nested, dict) else overview
        dist = data.get("holderDistribution") or []
        total_supply = float(data.get("totalSupply", 1) or 1)

        holders: list[dict[str, Any]] = []
        for entry in dist:
            addr = str(entry.get("address") or entry.get("owner") or "").lower()
            if not addr:
                continue
            balance = float(entry.get("amount", entry.get("balance", 0)) or 0)
            pct = round(balance / total_supply * 100, 2) if total_supply else 0.0
            holders.append({"address": addr, "balance": balance, "percentage": pct})
        return holders[:_MAX_HOLDERS]
    except Exception as exc:
        logger.debug("wallet_map_birdeye_holders_failed: %s", exc)
        return []


async def _enrich_holders(
    holder_addrs: list[str], chain_norm: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Buy-time (first on-chain tx), funding source + funding event time.

    Returns ``(first_tx_map, funder_map, funding_ts_map)`` keyed by holder
    address. All three degrade to ``{}`` when ClickHouse is unreachable or
    returns nothing. ``funding_ts_map`` is the holder's first-ever incoming
    transfer timestamp (the funding event) — drives the recently-funded
    buyer-timing metric.
    """
    first_tx_map: dict[str, Any] = {}
    funder_map: dict[str, Any] = {}
    funding_ts_map: dict[str, Any] = {}

    if not holder_addrs or not chain_norm:
        return first_tx_map, funder_map, funding_ts_map

    try:
        first_tx_rows = await run_sync(_query_holder_first_tx, holder_addrs, chain_norm)
        for row in first_tx_rows or []:
            if len(row) >= 2 and row[0]:
                first_tx_map[str(row[0]).lower()] = row[1]
    except Exception as exc:
        logger.debug("wallet_map_first_tx_failed: %s", exc)

    try:
        funder_rows = await run_sync(_query_first_funders, holder_addrs, chain_norm)
        for row in funder_rows or []:
            if len(row) >= 2 and row[0]:
                funder_map[str(row[0]).lower()] = str(row[1] or "").lower()
                if len(row) >= 4 and row[3] is not None:
                    funding_ts_map[str(row[0]).lower()] = row[3]
    except Exception as exc:
        logger.debug("wallet_map_funders_failed: %s", exc)

    return first_tx_map, funder_map, funding_ts_map


async def _enrich_buy_ts(token_address: str, holder_addrs: list[str], chain_norm: str) -> dict[str, Any]:
    """First buy of THIS token per holder (the reference point for timing).

    Returns a ``{holder: first_buy_ts}`` map, or ``{}`` when ClickHouse is
    unreachable / returns nothing. Never raises.
    """
    buy_ts_map: dict[str, Any] = {}
    if not holder_addrs or not chain_norm:
        return buy_ts_map
    try:
        buy_rows = await run_sync(_query_holder_first_buy, token_address, chain_norm, holder_addrs)
        for row in buy_rows or []:
            if len(row) >= 2 and row[0]:
                buy_ts_map[str(row[0]).lower()] = row[1]
    except Exception as exc:
        logger.debug("wallet_map_buy_ts_failed: %s", exc)
    return buy_ts_map


# ═══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════






# ═══════════════════════════════════════════════════════════════════════════
# CEX FUNDING CLASSIFICATION (who funded the wallets — "who's behind this")
# ═══════════════════════════════════════════════════════════════════════════










async def _fetch_wallet_funder(deployer: str, chain_norm: str) -> str | None:
    """First-ever funder of a wallet (EVM; bounded, graceful)."""
    try:
        rows = await run_sync(_query_wallet_first_funder, deployer, chain_norm)
        if rows and rows[0] and rows[0][0]:
            return str(rows[0][0]).lower()
    except Exception as exc:
        logger.debug("wallet_map_deployer_funder_failed: %s", exc)
    return None




async def _classify_deployer_funding(
    deployer: str | None,
    chain_norm: str,
    classified: list[dict[str, Any]],
) -> dict[str, Any]:
    """What funded the DEPLOYER: CEX / mixer / fresh / unknown — one line.

    Reuses the deployer's funder from the classified holder list when the
    deployer is still a top holder; otherwise queries the deployer's first
    funder directly. Fail-open: returns ``{}`` (and a "—" line via the
    caller's default) when no deployer or no funder is resolvable.
    """
    result: dict[str, Any] = {
        "source": None,
        "label": None,
        "deployer_funding_line": "\u2014",
    }
    if not deployer:
        return {}

    funder = next(
        (e.get("funder") for e in classified if e.get("role") == "dev" and e.get("funder")),
        None,
    )
    if not funder:
        funder = await _fetch_wallet_funder(deployer, chain_norm)
    if not funder:
        return {}

    cex = _classify_funder_cex(funder, chain_norm)
    if cex:
        result.update(
            {
                "source": "cex",
                "label": cex,
                "deployer_funding_line": f"\U0001f3e6 Deployer funded by: {cex}",
            }
        )
        return result

    mixer = _classify_funder_mixer(funder, chain_norm)
    if mixer:
        result.update(
            {
                "source": "mixer",
                "label": mixer,
                "deployer_funding_line": f"\U0001f300 Deployer funded by: mixer ({mixer})",
            }
        )
        return result

    if await _is_fresh_funder(funder, chain_norm):
        result.update(
            {
                "source": "fresh",
                "label": "fresh",
                "deployer_funding_line": "\U0001f331 Deployer funded by: fresh wallet",
            }
        )
        return result

    result.update(
        {
            "source": "unknown",
            "label": "unknown",
            "deployer_funding_line": "\U0001f937 Deployer funded by: unknown wallet",
        }
    )
    return result






# ═══════════════════════════════════════════════════════════════════════════
# BUYER TIMING (fresh-wallet % + recently-funded %)
# ═══════════════════════════════════════════════════════════════════════════
# Deeper first-buyer analysis for the deep scan: what fraction of the
# classified first-buyers were armed right before they bought?
#
#   * FRESH WALLET — the wallet's first-ever on-chain tx happened within
#     ``_FRESH_WALLET_HOURS`` BEFORE its first buy of the scanned token.
#   * RECENTLY FUNDED — the wallet's first funding event happened within
#     ``_RECENT_FUNDED_HOURS`` BEFORE the buy (funder → buy latency).
#
# Both are honest about coverage: each denominator is only the holders that
# HAVE both timestamps — missing data is never assumed to be organic. When
# no holder has usable timestamps the payload degrades to ``result:
# "unavailable"`` and the renderer shows "—".










def _build_map_line(roles: dict[str, int]) -> str:
    """Compact emoji map line, e.g. ``🛠️2 🐳1 🐟8 🍤7 🌱1 📦1``."""
    parts = [f"{ROLE_EMOJI[role]}{roles.get(role, 0)}" for role in _ROLE_ORDER]
    return " ".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_map(key: str, data: dict[str, Any]) -> None:
    """Cache wallet map data with timestamp."""
    data["_cached_at"] = time.time()
    _WALLET_MAP_CACHE[key] = data
    if len(_WALLET_MAP_CACHE) > _WALLET_MAP_CACHE_MAX:
        _WALLET_MAP_CACHE.pop(next(iter(_WALLET_MAP_CACHE)), None)





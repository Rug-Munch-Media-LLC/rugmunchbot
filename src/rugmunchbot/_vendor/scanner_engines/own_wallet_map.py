# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_wallet_map.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
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
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._funding_queries import (
    _query_first_funders,
    _query_funder_tx_counts,
    _query_holder_first_buy,
    _query_holder_first_tx,
    _query_top_holders,
    _query_wallet_first_funder,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_WALLET_MAP_CACHE: dict[str, dict[str, Any]] = {}
_WALLET_MAP_CACHE_TTL = 300  # 5 minutes — holder data changes slowly
_WALLET_MAP_CACHE_MAX = 100

# ── Role definitions ──────────────────────────────────────────────────────
ROLE_EMOJI: dict[str, str] = {
    "dev": "\U0001f6e0\ufe0f",  # 🛠️
    "whale": "\U0001f433",  # 🐳
    "fish": "\U0001f41f",  # 🐟
    "shrimp": "\U0001f964",  # 🍤
    "fresh": "\U0001f331",  # 🌱
    "bundle": "\U0001f4e6",  # 📦
}
_ROLE_ORDER: tuple[str, ...] = ("dev", "whale", "fish", "shrimp", "fresh", "bundle")

#: A holder whose wallet was born (first on-chain tx) within this window is "fresh".
_FRESH_WALLET_DAYS = 7
#: Buyer-timing window: a first-buyer's wallet counts as "fresh" when its
#: first-ever on-chain tx happened within this many hours BEFORE the buy.
_FRESH_WALLET_HOURS = 24
#: Buyer-timing window: a first-buyer counts as "recently funded" when its
#: first funding event happened within this many hours BEFORE the buy.
_RECENT_FUNDED_HOURS = 6
#: Whale / fish size thresholds (percentage of tracked supply).
_WHALE_PCT = 5.0
_FISH_PCT = 1.0
#: Max holders classified (the "first 20").
_MAX_HOLDERS = 20

#: DEX router / aggregator contracts that routinely appear as a holder's first
#: funder (the buy went through the router). They are infrastructure, not
#: evidence of a coordinated bundle — a shared router funder must never
#: classify holders as 📦. Lowercased (matches ``_enrich_holders`` output).
_KNOWN_ROUTER_WALLETS: frozenset[str] = frozenset(
    {
        # Uniswap V2 Router 02 (Ethereum)
        "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
        # Uniswap V3 SwapRouter / SwapRouter02 (Ethereum, Arbitrum, ...)
        "0xe592427a0aece92de3edee1f18e0157c05861564",
        "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
        # SwapRouter02 (Base)
        "0x2626664c2603336e57b271c5c0b26f421741e481",
        # SushiSwap Router
        "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",
        # PancakeSwap V2 Router / V3 SwapRouter (BSC)
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
        # 1inch aggregator routers (v4 / v5-v6)
        "0x1111111254fb6c44bac0bed2854e76f90643097d",
        "0x1111111254eeb25477b68fb85ed929f73a960582",
        # 0x (ZRX) Exchange Proxy
        "0xdef1c0ded9bec7f1a1670819833240f027b25eff",
        # Quickswap V2 / V3 (Polygon)
        "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff",
        "0xf5b509bb0909a69b1c207e495f45e8c9d590a6e9",
        # Camelot Router (Arbitrum)
        "0xc873fecbd354f5a56e00e710b90ef4201db2448d",
        # Solana: Jupiter v6 / Raydium (AmmV4 / CLMM) / Pump.fun programs
        "jup6lkzbzsj1jkkwapdhny74zcz3tluzoi5qnyvtav4",
        "675kpx9mhtjs2zt1qfr1nyhuzlexfqm9h24wfsut1mp8",
        "cammczo5yl8w4vff8kvhrk22ggusp5vta7grrkgrwqk",
        "6ef8rrecthr5dkzon8nwu78hrvfckubj14m5ubewf6p",
    }
)


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return (chain or "").lower().strip()


#: Human display names for CEX registry keys (entity_registry / known_entities).
_CEX_DISPLAY: dict[str, str] = {
    "binance": "Binance",
    "coinbase": "Coinbase",
    "kraken": "Kraken",
    "okx": "OKX",
    "okex": "OKX",
    "bybit": "Bybit",
    "kucoin": "KuCoin",
    "gateio": "Gate.io",
    "gate_io": "Gate.io",
    "mexc": "MEXC",
    "huobi": "Huobi",
    "htx": "HTX",
    "crypto.com": "Crypto.com",
    "crypto_com": "Crypto.com",
    "bitfinex": "Bitfinex",
    "gemini": "Gemini",
    "ftx": "FTX",
    "labeled_cex": "CEX",
}


def _cex_display_name(key: str | None) -> str:
    """Human display name for a CEX registry key (binance -> Binance)."""
    if not key:
        return "CEX"
    norm = str(key).lower().replace("_", " ")
    if norm in _CEX_DISPLAY:
        return _CEX_DISPLAY[norm]
    return " ".join(w.capitalize() for w in norm.split())


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


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


def _classify_holders(holders: list[dict[str, Any]], deployer: str | None = None) -> list[dict[str, Any]]:
    """Assign exactly one role per holder (first match wins by precedence).

    Precedence: Dev > Bundle > Fresh > Whale > Fish > Shrimp. "Fresh" only
    applies to sub-whale holders — a brand-new 6% wallet is still a whale.
    """
    deployer_l = (deployer or "").lower().strip()
    bundle_funders = _bundle_funders(holders)
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - timedelta(days=_FRESH_WALLET_DAYS)

    classified: list[dict[str, Any]] = []
    for h in holders:
        addr = h["address"]
        pct = float(h.get("percentage", 0) or 0)
        first_ts = h.get("first_tx_ts")
        funder = h.get("funder")

        if deployer_l and addr == deployer_l:
            role = "dev"
        elif funder and funder in bundle_funders:
            role = "bundle"
        elif first_ts is not None and _is_fresh(first_ts, cutoff) and pct < _WHALE_PCT:
            role = "fresh"
        elif pct >= _WHALE_PCT:
            role = "whale"
        elif pct >= _FISH_PCT:
            role = "fish"
        else:
            role = "shrimp"

        classified.append(
            {
                "address": addr,
                "role": role,
                "percentage": pct,
                "first_tx_ts": _to_iso(first_ts),
                "funder": funder,
                "emoji": ROLE_EMOJI[role],
            }
        )
    return classified


def _bundle_funders(holders: list[dict[str, Any]]) -> set[str]:
    """Funding sources shared by >= 2 of the classified holders.

    Known DEX routers/aggregators are excluded: when several holders bought
    through the same router, it shows up as everyone's first funder without
    implying a coordinated bundle.
    """
    counts: dict[str, int] = {}
    for h in holders:
        funder = h.get("funder")
        if funder and funder not in _KNOWN_ROUTER_WALLETS:
            counts[funder] = counts.get(funder, 0) + 1
    return {funder for funder, n in counts.items() if n >= 2}


# ═══════════════════════════════════════════════════════════════════════════
# CEX FUNDING CLASSIFICATION (who funded the wallets — "who's behind this")
# ═══════════════════════════════════════════════════════════════════════════


def _classify_funder_cex(funder_addr: str | None, chain_norm: str) -> str | None:
    """Exchange display name when the funder is a known CEX hot wallet.

    Checks the internal ``entity_registry`` (~28 CEX hot-wallet labels) first,
    then falls back to the per-chain ``known_entities`` label table for wider
    coverage. Returns ``None`` when the funder is not a recognized CEX — the
    honest OTC / fresh / unknown bucket.
    """
    if not funder_addr:
        return None
    try:
        from app.entity_registry import (
            ENTITY_TYPE_CEX_HOT_WALLET,
            ENTITY_TYPE_EXCHANGE,
            get_entity_registry,
        )

        match = get_entity_registry().classify_address(funder_addr)
        if match and match.entity_type in (ENTITY_TYPE_CEX_HOT_WALLET, ENTITY_TYPE_EXCHANGE):
            return _cex_display_name(match.entity_name)
    except Exception as exc:
        logger.debug("wallet_map_cex_registry_failed: %s", exc)
    try:
        from rugmunchbot._vendor.scanner_engines.known_entities import get_exchange_name

        name = get_exchange_name(funder_addr, chain_norm)
        if name and str(name).lower() != "unknown":
            return _cex_display_name(str(name))
    except Exception as exc:
        logger.debug("wallet_map_cex_known_entities_failed: %s", exc)
    return None


def _classify_funder_mixer(funder_addr: str | None, chain_norm: str) -> str | None:
    """Mixer name when the funder is a known mixer (Tornado Cash, ...)."""
    if not funder_addr:
        return None
    try:
        from app.entity_registry import ENTITY_TYPE_MIXER, get_entity_registry

        match = get_entity_registry().classify_address(funder_addr)
        if match and match.entity_type == ENTITY_TYPE_MIXER:
            return str(match.entity_name)
    except Exception as exc:
        logger.debug("wallet_map_mixer_registry_failed: %s", exc)
    try:
        from rugmunchbot._vendor.scanner_engines.known_entities import label_wallet

        label = label_wallet(funder_addr, chain_norm)
        if label.get("type") == "mixer":
            return str(label.get("name") or "mixer")
    except Exception as exc:
        logger.debug("wallet_map_mixer_known_entities_failed: %s", exc)
    return None


def _compute_cex_funding(
    classified: list[dict[str, Any]],
    chain_norm: str,
) -> tuple[dict[str, int], int, int]:
    """Per-CEX first-funder counts over the classified holders.

    Each holder with a known first-funder is counted once under its CEX
    (a Binance hot wallet funding 5 holders counts 5 times — the "% of
    wallets funded by Binance" semantics). Funders that are not a recognized
    CEX land in the ``unknown`` bucket (OTC / fresh / unlabeled).

    Returns ``(breakdown, unknown_count, total)`` where ``total`` is the
    number of classified holders with a non-null first-funder.
    """
    breakdown: dict[str, int] = {}
    unknown = 0
    total = 0
    for entry in classified:
        funder = entry.get("funder")
        if not funder:
            continue
        total += 1
        cex = _classify_funder_cex(funder, chain_norm)
        if cex:
            breakdown[cex] = breakdown.get(cex, 0) + 1
        else:
            unknown += 1
    return breakdown, unknown, total


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


async def _fetch_wallet_funder(deployer: str, chain_norm: str) -> str | None:
    """First-ever funder of a wallet (EVM; bounded, graceful)."""
    try:
        rows = await run_sync(_query_wallet_first_funder, deployer, chain_norm)
        if rows and rows[0] and rows[0][0]:
            return str(rows[0][0]).lower()
    except Exception as exc:
        logger.debug("wallet_map_deployer_funder_failed: %s", exc)
    return None


async def _is_fresh_funder(funder: str, chain_norm: str) -> bool:
    """True when the funder has < 10 total on-chain transfers (fresh heuristic)."""
    try:
        rows = await run_sync(_query_funder_tx_counts, [funder], chain_norm)
        if rows and rows[0] and rows[0][1] is not None:
            return int(rows[0][1]) < 10
    except Exception as exc:
        logger.debug("wallet_map_funder_tx_count_failed: %s", exc)
    return False


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


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


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

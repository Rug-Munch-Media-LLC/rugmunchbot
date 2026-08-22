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

from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
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
from rugmunchbot._vendor.scanner_engines.own_wallet_map_time import (  # noqa: F401
    _hours_between,
    _is_fresh,
    _to_iso,
    _to_naive_dt,
)

logger = get_logger(__name__)
ROLE_EMOJI: dict[str, str] = {
    "dev": "\U0001f6e0\ufe0f",  # 🛠️
    "whale": "\U0001f433",  # 🐳
    "fish": "\U0001f41f",  # 🐟
    "shrimp": "\U0001f964",  # 🍤
    "fresh": "\U0001f331",  # 🌱
    "bundle": "\U0001f4e6",  # 📦
}


_ROLE_ORDER: tuple[str, ...] = ("dev", "whale", "fish", "shrimp", "fresh", "bundle")


_FRESH_WALLET_DAYS = 7


_WHALE_PCT = 5.0


_FISH_PCT = 1.0


_MAX_HOLDERS = 20


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



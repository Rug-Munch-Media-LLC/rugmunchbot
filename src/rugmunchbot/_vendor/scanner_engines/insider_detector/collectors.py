# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/insider_detector/collectors.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Phase 1 Data Collection — token metadata, traders, events."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines import gmgn_client as gmgn

from .config import DEXSCREENER_BOOSTS, DEXSCREENER_PROFILES, DEXSCREENER_SEARCH, DEXSCREENER_TOKEN
from .helpers import _parse_timestamp

logger = get_logger(__name__)


async def _collect_token_meta(
    token_address: str, chain: str, deployer: str | None = None
) -> tuple[str | None, str | None, float | None]:
    """Collect token metadata: symbol, deployer address, creation timestamp."""
    symbol: str | None = None
    deployer_addr: str | None = deployer
    created_at: float | None = None

    async def _from_dexscreener() -> None:
        nonlocal symbol, created_at
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(DEXSCREENER_TOKEN.format(token_address))
                if r.status_code == 200:
                    data = r.json()
                    pairs = data.get("pairs") or []
                    if pairs:
                        symbol = pairs[0].get("baseToken", {}).get("symbol", symbol)
                        creation = pairs[0].get("pairCreatedAt")
                        if creation:
                            created_at = creation / 1000
        except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError) as e:
            logger.debug("DexScreener meta fetch failed: %s", e)

    async def _from_gmgn() -> None:
        nonlocal symbol
        try:
            info = gmgn.get_token_info(token_address, chain)
            if info and isinstance(info, dict):
                symbol = info.get("symbol", info.get("base_token", {}).get("symbol", symbol))
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("GMGN meta fetch failed: %s", e)

    async def _from_deployer() -> None:
        nonlocal deployer_addr
        try:
            if not deployer_addr:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get(DEXSCREENER_SEARCH.format(token_address))
                    if r.status_code == 200:
                        data = r.json()
                        pairs = data.get("pairs") or []
                        if pairs:
                            deployer_addr = pairs[0].get("info", {}).get("deployer", pairs[0].get("creatorAddress"))
        except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError) as e:
            logger.debug("Deployer fetch failed: %s", e)

    await asyncio.gather(_from_dexscreener(), _from_gmgn(), _from_deployer(), return_exceptions=True)
    return symbol, deployer_addr, created_at


async def _get_token_traders(token_address: str, chain: str, max_traders: int = 100) -> list[dict[str, Any]]:
    """Get all traders and their trades for a token."""
    seen_hashes: set[Any] = set()
    traders_map: dict[str, dict[str, Any]] = {}

    def _add_trade(trade: dict[str, Any]) -> None:
        tx_hash = trade.get("tx_hash") or trade.get("transaction_hash", "")
        if tx_hash in seen_hashes:
            return
        seen_hashes.add(tx_hash)
        wallet = trade.get("wallet") or trade.get("maker", "")
        if not wallet:
            return
        if wallet not in traders_map:
            traders_map[wallet] = {
                "wallet": wallet,
                "buys": [],
                "sells": [],
                "total_bought_usd": 0.0,
                "total_sold_usd": 0.0,
                "first_buy_at": float("inf"),
                "last_trade_at": 0,
                "trade_count": 0,
            }
        entry = traders_map[wallet]
        amount_usd = float(trade.get("amount_usd", 0) or 0)
        side = (trade.get("side") or "buy").lower()
        timestamp = trade.get("timestamp", 0) or 0
        price_usd = float(trade.get("price_usd", 0) or 0)
        trade_entry = {
            "tx_hash": tx_hash,
            "amount_usd": amount_usd,
            "price_usd": price_usd,
            "timestamp": timestamp,
            "side": side,
        }
        if side == "buy":
            entry["buys"].append(trade_entry)
            entry["total_bought_usd"] += amount_usd
            if timestamp and timestamp < entry["first_buy_at"]:
                entry["first_buy_at"] = timestamp
        else:
            entry["sells"].append(trade_entry)
            entry["total_sold_usd"] += amount_usd
        if timestamp > entry["last_trade_at"]:
            entry["last_trade_at"] = timestamp
        entry["trade_count"] += 1

    try:
        top_traders = gmgn.get_top_traders(token_address, chain, min(max_traders, 50))
        for t in top_traders:
            _add_trade(
                {
                    "wallet": t.get("maker", t.get("wallet", "")),
                    "tx_hash": t.get("transaction_hash", t.get("tx_hash", "")),
                    "side": t.get("side", "buy"),
                    "amount_usd": float(t.get("amount_usd", 0) or 0),
                    "price_usd": float(t.get("price_usd", 0) or 0),
                    "timestamp": t.get("timestamp", 0),
                }
            )
    except (httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("GMGN top traders failed: %s", e)

    try:
        wallet_trades = gmgn.get_wallet_trades(token_address, chain, limit=50)
        if isinstance(wallet_trades, list):
            for t in wallet_trades:
                _add_trade(t)
    except httpx.HTTPError:
        logger.debug("unknown_error", exc_info=True)
        logger.warning("get_wallet_trades failed", exc_info=True)

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(DEXSCREENER_TOKEN.format(token_address))
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs") or []
                for pair in pairs:
                    dd = pair.get("txns", {})
                    for side_key in ("m5", "h1", "h6", "h24"):
                        for _side_name, side_data in dd.get(side_key, {}).items():
                            if isinstance(side_data, (int, float)):
                                continue
    except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError, ConnectionError, TimeoutError, OSError) as e:
        logger.debug("DexScreener pair data failed: %s", e)

    traders = list(traders_map.values())
    for t in traders:
        if t["first_buy_at"] == float("inf"):
            t["first_buy_at"] = 0
        t["buys"].sort(key=lambda x: x["timestamp"])
        t["sells"].sort(key=lambda x: x["timestamp"])

    traders.sort(key=lambda x: x["total_bought_usd"], reverse=True)
    return traders[:max_traders]


async def _gather_events(
    token_address: str,
    chain: str,
    deployer: str | None,
    symbol: str | None,
    token_created_at: float | None,
) -> dict[str, Any]:
    """Gather all event timestamps relevant to this token."""
    events: dict[str, Any] = {
        "dexscreener_first_seen": None,
        "dexscreener_first_seen_desc": None,
        "gmgn_first_trending": None,
        "gmgn_first_trending_desc": None,
        "first_kol_trade_at": None,
        "first_kol_trade_desc": None,
        "deployer_created_at": token_created_at,
        "pumpfun_graduated_at": None,
        "cex_listings": [],
        "volume_spikes": [],
    }

    async def _dex_profiles() -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(DEXSCREENER_PROFILES)
                if r.status_code == 200:
                    profiles = r.json()
                    if isinstance(profiles, list):
                        for profile in profiles:
                            addr = (profile.get("tokenAddress") or "").lower()
                            if addr == token_address.lower():
                                created = profile.get("createdAt")
                                if created:
                                    ts = _parse_timestamp(created)
                                    if ts:
                                        events["dexscreener_first_seen"] = ts
                                        events["dexscreener_first_seen_desc"] = "DexScreener token profile created"
                                        break
        except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError) as e:
            logger.debug("DexScreener profiles check failed: %s", e)

    async def _dex_boosts() -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(DEXSCREENER_BOOSTS)
                if r.status_code == 200:
                    boosts = r.json()
                    if isinstance(boosts, list):
                        for boost in boosts:
                            addr = (boost.get("tokenAddress") or "").lower()
                            if addr == token_address.lower():
                                created = boost.get("createdAt")
                                if created:
                                    ts = _parse_timestamp(created)
                                    if ts and (
                                        events.get("dexscreener_first_seen") is None
                                        or ts < events["dexscreener_first_seen"]
                                    ):
                                        events["dexscreener_first_seen"] = ts
                                        events["dexscreener_first_seen_desc"] = "DexScreener trending boost"
                                        break
        except (httpx.HTTPError, httpx.TimeoutException, json.JSONDecodeError) as e:
            logger.debug("DexScreener boosts check failed: %s", e)

    async def _gmgn_trending() -> None:
        try:
            for interval in ("1h", "6h", "24h"):
                trending = gmgn.get_trending(chain, interval, limit=50)
                if isinstance(trending, list):
                    for item in trending:
                        addr = item.get("base_address", "").lower()
                        if addr == token_address.lower():
                            ts = item.get("timestamp", 0)
                            if ts and (events.get("gmgn_first_trending") is None or ts < events["gmgn_first_trending"]):
                                events["gmgn_first_trending"] = ts
                                events["gmgn_first_trending_desc"] = f"GMGN trending ({interval} interval)"
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("GMGN trending check failed: %s", e)

    async def _kol_trades() -> None:
        try:
            for limit in (50, 100):
                kols = gmgn.get_kol_trades(chain, limit=limit)
                if isinstance(kols, list):
                    for k in kols:
                        addr = (k.get("token_address") or k.get("base_address", "")).lower()
                        if addr == token_address.lower():
                            ts = k.get("timestamp", 0)
                            if ts:
                                kol_name = k.get("kol_name", "unknown KOL")
                                if events.get("first_kol_trade_at") is None or ts < events["first_kol_trade_at"]:
                                    events["first_kol_trade_at"] = ts
                                    events["first_kol_trade_desc"] = f"KOL {kol_name} first traded"
                    if events.get("first_kol_trade_at"):
                        break
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("KOL trades check failed: %s", e)

    async def _gmgn_trenches() -> None:
        try:
            trenches = gmgn.get_trenches(chain, "completed", limit=50)
            if isinstance(trenches, list):
                for t in trenches:
                    addr = t.get("base_address", "").lower()
                    if addr == token_address.lower():
                        ts = t.get("graduated_at", t.get("timestamp", 0))
                        if ts:
                            events["pumpfun_graduated_at"] = ts
                            break
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("GMGN trenches check failed: %s", e)

    async def _cex_listings() -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(DEXSCREENER_TOKEN.format(token_address))
                if r.status_code == 200:
                    pairs_data = r.json()
                    if pairs_data and pairs_data.get("pairs"):
                        pair = pairs_data["pairs"][0]
                        symbol_found = pair.get("baseToken", {}).get("symbol", "")
                        if symbol_found:
                            events["cex_listings"] = [
                                {
                                    "symbol": symbol_found,
                                    "detected": "Listing detected on known CEX pairs",
                                    "timestamp": pair.get("pairCreatedAt", 0) / 1000
                                    if pair.get("pairCreatedAt")
                                    else None,
                                }
                            ]
        except (
            httpx.HTTPError,
            httpx.TimeoutException,
            json.JSONDecodeError,
            ConnectionError,
            TimeoutError,
            OSError,
        ) as e:
            logger.debug("CEX listing check failed: %s", e)

    await asyncio.gather(
        _dex_profiles(),
        _dex_boosts(),
        _gmgn_trending(),
        _kol_trades(),
        _gmgn_trenches(),
        _cex_listings(),
        return_exceptions=True,
    )
    return events

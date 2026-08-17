"""birdeye_market tool — direct Birdeye public API coverage.

Wired endpoints (paths verified against docs.birdeye.so 2026-08-14;
live success shapes could NOT be probed — see [REVIEW] notes below):

  * token price          GET /defi/price
  * smart-money discovery GET /smart-money/v1/token/list   (solana + 13 EVM chains)
  * token top traders    GET /defi/v2/tokens/top_traders
  * wallet tags tracker  GET /token/v1/wallet-tags-tracker (solana)
  * liquidity history    GET /defi/v3/liquidity/history/token (solana)

Key resolution: env ``BIRDEYE_API_KEY`` → secrets manager (never hardcoded).
If the key is missing/invalid OR Birdeye is rate-limited / quota-exhausted the
tool falls back to our own backend routes so the bot never answers "no data"
when the backend has the same signal through other providers.

[REVIEW] 2026-08-14 key probe: the secrets-manager Birdeye key returns 401
Unauthorized (revoked/invalid); the backend container's BIRDEYE_API_KEY returns
400 "Compute units usage limit exceeded" (valid but free-tier exhausted).
Backend databus provider chains (app/domains/databus/providers/chains/
freemium_apis.py) still call the OLD ``/public/*`` paths which return 404 —
those are stale and should be migrated to ``/defi/*`` / ``/smart-money/*``.
"""

from __future__ import annotations

import os
from typing import Any

from app.domains.telegram.rugmunchbot.config import _get_token
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.birdeye")

BIRDEYE_BASE = "https://public-api.birdeye.so"

# Smart-money discovery supports solana + these 13 EVM chains (docs 2026-08-14).
SMART_MONEY_CHAINS = (
    "solana",
    "ethereum",
    "arbitrum",
    "avalanche",
    "bsc",
    "optimism",
    "polygon",
    "base",
    "zksync",
    "monad",
    "hyperevm",
    "mantle",
    "megaeth",
    "robinhood",
)

# x-chain header normalization (Birdeye uses short lowercase slugs).
_CHAIN_ALIASES = {
    "sol": "solana",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bnb": "bsc",
    "matic": "polygon",
    "arb": "arbitrum",
    "op": "optimism",
    "zksync_era": "zksync",
}


def _chain_slug(chain: str) -> str:
    """Normalize a user chain name to Birdeye's x-chain slug."""
    c = (chain or "solana").strip().lower()
    return _CHAIN_ALIASES.get(c, c)


def _birdeye_key() -> str:
    """Resolve Birdeye API key: env var → secrets manager. Never hardcoded."""
    return _get_token("rmi/infra/birdeye_api_key", ["BIRDEYE_API_KEY"])


def _birdeye_keys() -> list[str]:
    """Ordered Birdeye keys for rotation (primary first).

    Verified 2026-08-16: the primary key is compute-quota-exhausted on
    Solana (HTTP 400) while ``BIRDEYE_API_KEY_ALT`` serves both Solana and
    EVM. ``_birdeye_get`` tries each key and rotates on 400/401/403 so the
    chat tools return live Birdeye data instead of silently falling back.
    """
    keys: list[str] = [_birdeye_key()]
    alt = os.environ.get("BIRDEYE_API_KEY_ALT", "")
    if alt and alt not in keys:
        keys.append(alt)
    return keys


async def _birdeye_get(path: str, params: dict[str, Any], chain: str = "solana") -> dict[str, Any] | None:
    """GET a Birdeye endpoint with the API-key + x-chain headers.

    ``http_get_json`` returns None on any non-200 (quota-exhausted 400 /
    invalid-key 401 / rate-limit 429), so this tries each key in
    ``_birdeye_keys()`` and returns the first non-None body. Bounded to the
    two configured keys.
    """
    for key in _birdeye_keys():
        if not key:
            continue
        headers = {"X-API-KEY": key, "accept": "application/json", "x-chain": _chain_slug(chain)}
        body = await http_get_json(f"{BIRDEYE_BASE}{path}", params=params, headers=headers, timeout=8.0)
        if body is not None:
            return body
    return None


def _unpack_data(body: dict[str, Any] | None) -> Any:
    """Birdeye wraps payloads in ``{success, data, message}`` — extract ``data``."""
    if not body:
        return None
    if isinstance(body.get("data"), (dict, list)):
        return body.get("data")
    # Some endpoints return lists directly under ``data`` with a success flag.
    return body if body.get("success") is True else None


# ── Low-level endpoint helpers (also importable by the price fallback chain) ──


async def birdeye_price(address: str, chain: str = "solana") -> dict[str, Any] | None:
    """Latest price for a token address (Birdeye ``/defi/price``)."""
    if not address:
        return None
    data = _unpack_data(await _birdeye_get("/defi/price", {"address": address}, chain))
    if not isinstance(data, dict):
        return None
    return {
        "price_usd": data.get("value"),
        "update_time": data.get("updateHumanTime") or data.get("updateUnixTime"),
        "source": "birdeye:/defi/price",
    }


async def birdeye_smart_money(chain: str = "solana", limit: int = 10) -> list[dict[str, Any]] | None:
    """Smart-money ranked token list (``/smart-money/v1/token/list``)."""
    params: dict[str, Any] = {"interval": "1d", "sort_by": "smart_traders_no", "limit": min(limit, 20)}
    data = _unpack_data(await _birdeye_get("/smart-money/v1/token/list", params, chain))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    out: list[dict[str, Any]] = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "address": it.get("address"),
                "symbol": it.get("symbol"),
                "name": it.get("name"),
                "price_usd": it.get("price"),
                "change_24h": it.get("priceChange24hPercent"),
                "smart_traders": it.get("smartTraderNo") or it.get("smart_traders_no"),
                "net_flow_usd": it.get("netFlow") or it.get("net_flow"),
                "market_cap": it.get("marketCap"),
            }
        )
    return out or None


async def birdeye_top_traders(address: str, chain: str = "solana", limit: int = 10) -> list[dict[str, Any]] | None:
    """Top traders for a token (``/defi/v2/tokens/top_traders``)."""
    if not address:
        return None
    params: dict[str, Any] = {
        "address": address,
        "time_frame": "24h",
        "sort_by": "volume",
        "limit": min(limit, 10),
    }
    data = _unpack_data(await _birdeye_get("/defi/v2/tokens/top_traders", params, chain))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    out: list[dict[str, Any]] = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "wallet": it.get("address") or it.get("wallet"),
                "tags": it.get("wallet_tags") or it.get("tags"),
                "buy_volume_usd": it.get("volumeBuyUSD") or it.get("buy_volume_usd"),
                "sell_volume_usd": it.get("volumeSellUSD") or it.get("sell_volume_usd"),
                "realized_pnl": it.get("realizedPnl") or it.get("realized_pnl"),
                "hold_volume_usd": it.get("holdVolumeUsd") or it.get("hold_volume_usd"),
                "first_trade": it.get("firstTradeUnixTime"),
                "last_trade": it.get("lastTradeUnixTime"),
            }
        )
    return out or None


async def birdeye_wallet_tags(address: str, time_frame: str = "1D") -> dict[str, Any] | None:
    """Tagged-wallet trading activity for a token (``/token/v1/wallet-tags-tracker``).

    Solana-only. ``time_frame`` bucket: 1m/5m/15m/30m/1h/2h/4h/1D/1W/1M.
    """
    if not address:
        return None
    data = _unpack_data(
        await _birdeye_get(
            "/token/v1/wallet-tags-tracker",
            {"token_address": address, "time_from": 0, "time_frame": time_frame},
            "solana",
        )
    )
    if not isinstance(data, dict):
        return None
    return {"address": address, "time_frame": time_frame, "groups": data}


async def birdeye_liquidity_history(
    address: str, resolution: str = "4h", count: int = 20
) -> list[dict[str, Any]] | None:
    """Liquidity time-series snapshots (``/defi/v3/liquidity/history/token``).

    Solana-only. ``resolution``: 1m/4h/1D; max 100 records.
    """
    if not address:
        return None
    data = _unpack_data(
        await _birdeye_get(
            "/defi/v3/liquidity/history/token",
            {"address": address, "resolution": resolution, "direction": "back", "count": min(count, 100)},
            "solana",
        )
    )
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        return None
    return items[:count]


# ── Backend fallbacks (used when the Birdeye key is unusable) ──────────────


async def _backend_price_fallback(address: str) -> ToolResult | None:
    """Our backend token detail (fronts DexScreener/Birdeye/GMGN aggregation)."""
    body = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=8.0)
    if body and body.get("pairs"):
        return ToolResult(
            tool="birdeye_market",
            ok=True,
            data={"kind": "price", "address": address, "pairs": body.get("pairs")},
            symbols=[],
            addresses=[address],
            source="backend:/api/v1/databus/token (birdeye fallback)",
        )
    return None


async def _backend_smart_money_fallback(chain: str = "solana", limit: int = 10) -> ToolResult | None:
    body = await backend_json_get("/api/v1/databus/premium/smart-money", params={"chain": chain}, timeout=8.0)
    wallets = body.get("smart_money_wallets") if body else None
    if not isinstance(wallets, list) or not wallets:
        return None
    addrs = [w.get("address") for w in wallets[:limit] if isinstance(w, dict) and w.get("address")]
    return ToolResult(
        tool="birdeye_market",
        ok=True,
        data={"kind": "smart_money", "chain": chain, "smart_money_wallets": wallets[:limit]},
        symbols=[],
        addresses=[a for a in addrs if a],
        source="backend:/api/v1/databus/premium/smart-money (birdeye fallback)",
    )


async def _backend_top_traders_fallback(address: str, limit: int = 10) -> ToolResult | None:
    body = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=8.0)
    if not body:
        return None
    raw = body.get("data")
    data = raw if isinstance(raw, dict) else body
    holders = data.get("top_holders") or data.get("holders") or []
    smart = data.get("smart_money") or []
    payload: dict[str, Any] = {"kind": "top_traders", "address": address}
    if isinstance(holders, list):
        payload["top_holders"] = holders[:limit]
    if isinstance(smart, list):
        payload["smart_money"] = smart[:limit]
    if not payload.get("top_holders") and not payload.get("smart_money"):
        return None
    return ToolResult(
        tool="birdeye_market",
        ok=True,
        data=payload,
        symbols=[],
        addresses=[address],
        source="backend:/api/v1/databus/token (birdeye fallback)",
    )


# ── The tool ───────────────────────────────────────────────────────────────


async def birdeye_market(
    *, kind: str = "price", address: str = "", chain: str = "solana", limit: int = 10
) -> ToolResult:
    """Birdeye market data: price / smart_money / top_traders / wallet_tags / liquidity_history.

    ``kind`` selects the dataset:
      * ``price`` — latest token price (``/defi/price``).
      * ``smart_money`` — smart-money ranked tokens, solana + 13 EVM chains.
      * ``top_traders`` — top trader wallets for a token (24h, by volume).
      * ``wallet_tags`` — tagged-wallet activity time series (solana only).
      * ``liquidity_history`` — liquidity snapshots time series (solana only).

    Falls back to our backend routes when the Birdeye key is missing/invalid
    or Birdeye is quota-limited, so the bot still returns real data.
    """
    kind = (kind or "price").strip().lower()
    chain = chain or "solana"

    if kind == "price":
        if address:
            price = await birdeye_price(address, chain)
            if price and price.get("price_usd") is not None:
                return ToolResult(
                    tool="birdeye_market",
                    ok=True,
                    data={"kind": "price", "address": address, "chain": chain, "price": price},
                    symbols=[],
                    addresses=[address],
                    source="birdeye:/defi/price",
                )
            fallback = await _backend_price_fallback(address)
            if fallback is not None:
                return fallback
        return ToolResult(
            tool="birdeye_market",
            ok=False,
            error="no live Birdeye price (key missing/invalid or quota exhausted; backend fallback empty)",
            addresses=[address] if address else [],
        )

    if kind == "smart_money":
        tokens = await birdeye_smart_money(chain, limit)
        if tokens:
            return ToolResult(
                tool="birdeye_market",
                ok=True,
                data={"kind": "smart_money", "chain": chain, "tokens": tokens},
                symbols=[t["symbol"] for t in tokens if t.get("symbol")],
                addresses=[t["address"] for t in tokens if t.get("address")],
                source="birdeye:/smart-money/v1/token/list",
            )
        fallback = await _backend_smart_money_fallback(chain, limit)
        if fallback is not None:
            return fallback
        return ToolResult(
            tool="birdeye_market",
            ok=False,
            error="no live Birdeye smart-money (key unusable; backend smart-money feed empty/denied)",
        )

    if kind == "top_traders":
        if address:
            traders = await birdeye_top_traders(address, chain, limit)
            if traders:
                return ToolResult(
                    tool="birdeye_market",
                    ok=True,
                    data={"kind": "top_traders", "address": address, "chain": chain, "traders": traders},
                    symbols=[],
                    addresses=[address, *[t["wallet"] for t in traders if t.get("wallet")]],
                    source="birdeye:/defi/v2/tokens/top_traders",
                )
            fallback = await _backend_top_traders_fallback(address, limit)
            if fallback is not None:
                return fallback
        return ToolResult(
            tool="birdeye_market",
            ok=False,
            error="no live Birdeye top-traders (key unusable or no trader data)",
            addresses=[address] if address else [],
        )

    if kind == "wallet_tags":
        if address:
            direct = await birdeye_wallet_tags(address)
            if direct:
                return ToolResult(
                    tool="birdeye_market",
                    ok=True,
                    data=direct,
                    symbols=[],
                    addresses=[address],
                    source="birdeye:/token/v1/wallet-tags-tracker",
                )
        return ToolResult(
            tool="birdeye_market",
            ok=False,
            error="no live Birdeye wallet-tags (solana token address required; key unusable)",
            addresses=[address] if address else [],
        )

    if kind == "liquidity_history":
        if address:
            snapshots = await birdeye_liquidity_history(address)
            if snapshots:
                return ToolResult(
                    tool="birdeye_market",
                    ok=True,
                    data={"kind": "liquidity_history", "address": address, "snapshots": snapshots},
                    symbols=[],
                    addresses=[address],
                    source="birdeye:/defi/v3/liquidity/history/token",
                )
        return ToolResult(
            tool="birdeye_market",
            ok=False,
            error="no live Birdeye liquidity-history (solana token address required; key unusable)",
            addresses=[address] if address else [],
        )

    return ToolResult(
        tool="birdeye_market",
        ok=False,
        error=f"unknown kind: {kind} (price/smart_money/top_traders/wallet_tags/liquidity_history)",
    )

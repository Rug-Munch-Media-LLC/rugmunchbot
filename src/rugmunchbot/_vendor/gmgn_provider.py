# VENDORED 2026-08-23 from rmi-backend/app/databus/gmgn_provider.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
#!/usr/bin/env python3
"""
GMGN Data Provider — GMGN Agent API (OpenAPI) integration.
==========================================================
Provides: token info, security, holders, trending, discover (trenches),
wallet detail, K-line, live trades, smart money, new pair discovery.

WORKING PATH (verified 2026-08-16)
----------------------------------
The public web API (``gmgn.ai/defi/quotation/v1/...``) is Cloudflare-blocked
(returns HTML even via proxy) and is NO LONGER used. This provider uses the
official **GMGN Agent API** instead:

  * Base URL:  ``https://openapi.gmgn.ai``  (NOT gmgn.ai — different host,
    no Cloudflare challenge on the data routes)
  * Auth:      ``X-APIKEY`` header with the GMGN_API_KEY, plus two query
    params required by the OpenAPI exist-auth scheme:
      - ``timestamp`` — unix seconds
      - ``client_id`` — random UUID (replay guard, fresh per request)
    (Verified from GMGNAI/gmgn-skills source: ``OpenApiClient.js`` /
    ``signer.js``, npm ``gmgn-cli`` 1.5.6 dist.)
  * The ``npx gmgn-cli`` CLI is a thin wrapper over this same API — the
    direct HTTP path is used here so no Node runtime dependency is needed.

Endpoints (verified live 2026-08-16, exist-auth = API key only):
  - token info:      GET /v1/token/info?chain&address
  - token security:  GET /v1/token/security?chain&address
  - token pool:      GET /v1/token/pool_info?chain&address
  - holders:         GET /v1/market/token_top_holders?chain&address&limit&order_by&direction[&tag]
  - traders:         GET /v1/market/token_top_traders?chain&address&limit
  - trending:        GET /v1/market/rank?chain&interval&order_by&direction&limit
  - discover:        POST /v1/trenches?chain   (body: {chain, types, limit})
  - K-line:          GET /v1/market/token_kline?chain&address&resolution&from&to
  - wallet stats:    GET /v1/user/wallet_stats?chain&wallet_address&period
  - wallet activity: GET /v1/user/wallet_activity?chain&wallet_address&limit
  - wallet holdings: GET /v1/user/wallet_holdings  (SIGNED auth — needs
    GMGN_PRIVATE_KEY, which this repo does not hold; NOT used here)

Rate limits: leaky bucket (rate=20, capacity=20), per-route weight; 429
returns ``X-RateLimit-Reset`` (unix secs) or body ``reset_at``. This client
honours the reset time and backs off instead of spamming (which extends
bans). Falls back to DexScreener/Birdeye when GMGN is unavailable.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.gmgn_fallbacks import (
    agent_auth_params,
    agent_headers,
    cached_or_fetch,
    cached_or_fetch_list,
    fallback_birdeye_holders,
    fallback_dexscreener_new_pairs,
    fallback_dexscreener_token,
    fallback_dexscreener_trades,
    normalize_trades,
)
from rugmunchbot._vendor.proxy_rotator import get_proxy_rotator

logger = get_logger(__name__)

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_BASE = "https://gmgn.ai"
# Official GMGN Agent API (used by the gmgn-cli SDK / openapi.gmgn.ai).
# Auth: X-APIKEY header + timestamp (unix seconds) + client_id (UUID) query
# params — see OpenApiClient.ts / signer.ts in GMGNAI/gmgn-skills.
GMGN_OPENAPI_BASE = "https://openapi.gmgn.ai"

# Browser-like headers kept for the non-Agent fallback egresses
# (DexScreener/Birdeye). The Agent API is not Cloudflare-gated on its data
# routes; a clean UA is still sent.
_BROWSER_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://gmgn.ai/?chain=sol",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
}

# Cache TTLs (seconds)
TRADE_CACHE_TTL = 30
TOKEN_CACHE_TTL = 120
HOLDER_CACHE_TTL = 300
TRENDING_CACHE_TTL = 60


def _normalize_chain(chain: str) -> str:
    """Map chain names to GMGN's short chain slugs (sol, eth, bsc, base...)."""
    aliases = {
        "solana": "sol",
        "sol": "sol",
        "ethereum": "eth",
        "eth": "eth",
        "mainnet": "eth",
        "bsc": "bsc",
        "bnb": "bsc",
        "binance": "bsc",
        "base": "base",
        "polygon": "polygon",
        "matic": "polygon",
        "arbitrum": "arbitrum",
        "arb": "arbitrum",
        "optimism": "optimism",
        "op": "optimism",
        "avalanche": "avax",
        "avax": "avax",
        "fantom": "fantom",
        "ftm": "fantom",
        "tron": "tron",
        "trx": "tron",
        "robinhood": "robinhood",
        "arc": "arc",
        "stable": "stable",
    }
    return aliases.get(chain.lower(), chain.lower())


class GMGNProvider:
    """GMGN Agent API client with fallback cascade."""

    def __init__(self) -> None:
        self.api_key = GMGN_API_KEY
        self.headers = dict(_BROWSER_HEADERS)
        if self.api_key:
            # GMGN Agent API auth convention (official SDK: X-APIKEY header).
            self.headers["X-APIKEY"] = self.api_key
        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        # Proxy-first egress when residential proxies are configured (used by
        # the fallback egresses — the Agent API itself is not CF-gated).
        self._rotator = get_proxy_rotator()
        self._proxy_url: str | None = None
        self._proxy_client: httpx.AsyncClient | None = None
        if self._rotator.enabled:
            self._proxy_url = self._rotator.get_rotating_proxy()
            self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)
        # Rate-limit state: next allowed unix ts + cooldown until (ban).
        self._next_allowed_at = 0.0
        self._banned_until = 0.0
        self._available = True

    # ═══════════════════════════════════════════════════════════════
    # CORE: Token Info
    # ═══════════════════════════════════════════════════════════════

    async def get_token_info(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Get comprehensive token data from GMGN."""
        result = await cached_or_fetch(
            f"token:{chain}:{address}",
            TOKEN_CACHE_TTL,
            self._fetch_token_info,
            address,
            chain,
        )
        if result:
            return result

        # Fallback: DexScreener
        return await fallback_dexscreener_token(self._request, address, chain)

    async def _fetch_token_info(self, address: str, chain: str) -> dict[str, Any] | None:
        """Fetch token info from the GMGN Agent API (GET /v1/token/info)."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            data = await self._agent_get("/v1/token/info", {"chain": chain_slug, "address": address})
            if not data:
                return None
            # Agent API shape: {"data": {"token": {...}}} — 2026-08-23 audit:
            # the parser stopped one level short (token = data["data"]), so
            # every field read hit the wrapper and returned defaults.
            token = ((data.get("data") or {}).get("token")) or {}
            if not token:
                return None
            price_obj = token.get("price") or {}
            price = _f(price_obj.get("price"))
            circ = _f(token.get("circulating_supply"))
            return {
                "address": address,
                "chain": chain,
                "name": token.get("name", ""),
                "symbol": token.get("symbol", ""),
                "price": price,
                "market_cap": price * circ if (price and circ) else 0,
                "fdv": price * _f(token.get("total_supply")) if price else 0,
                "liquidity": _f(token.get("liquidity")),
                "volume_24h": _f(price_obj.get("volume_24h")),
                "holders": int(token.get("holder_count", 0) or 0),
                "price_change_1h": _f(price_obj.get("price_1h")),
                "price_change_24h": _f(price_obj.get("price_24h")),
                "buy_24h": int(price_obj.get("buys_24h", 0) or 0),
                "sell_24h": int(price_obj.get("sells_24h", 0) or 0),
                "created_at": token.get("creation_timestamp"),
                "launchpad": token.get("launchpad"),
                "ath_price": _f(token.get("ath_price")),
                "launchpad_status": token.get("launchpad_status"),
                "launchpad_progress": _f(token.get("launchpad_progress")),
                "locked_ratio": _f(token.get("locked_ratio")),
                "source": "gmgn",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN token_info failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # WALLET INTELLIGENCE (derived — feeds the unified scanner)
    # ═══════════════════════════════════════════════════════════════

    async def get_wallet_intelligence(self, wallet: str, chain: str = "solana") -> dict[str, Any]:
        """Wallet insider/sniper/wash/PnL signals for the unified scanner.

        Derives the legacy ``GMGNClient.get_wallet_intelligence`` shape from
        the Agent API wallet detail (stats + activity) plus holder-derived
        behavior heuristics. Returns a dict with ``insider_score``,
        ``sniper_score``, ``wash_trading_score``, ``pump_dump_participant``,
        ``flipper``, ``win_rate`` and ``pnl`` so the scanner risk factors
        can be populated without the (never-implemented) legacy client.
        """
        try:
            detail = await self.get_wallet_detail(wallet, chain, period="7d")
            if not detail or detail.get("source") != "gmgn":
                return {}
            realized = float(detail.get("realized_profit") or 0)
            winrate = float(detail.get("winrate") or 0)
            buy_count = int(detail.get("buy") or 0)
            sell_count = int(detail.get("sell") or 0)
            pnl_gt5x = int(detail.get("pnl_gt_5x_num") or 0)
            activities = detail.get("activities") or []
            # Wash heuristic: near-equal buy/sell tx counts with meaningful volume.
            total_tx = buy_count + sell_count
            wash_score = 0.0
            if total_tx >= 6:
                ratio = buy_count / total_tx
                wash_score = round((1 - abs(ratio - 0.5) * 2) * 100, 1) if 0.25 <= ratio <= 0.75 else 0.0
            # Sniper heuristic: very high win-rate with short avg holding (early-exit
            # behavior) or heavy PnL concentration in few tokens.
            avg_hold = float(detail.get("avg_holding_period") or 0)
            sniper_score = 0.0
            if winrate >= 0.7 and avg_hold and avg_hold <= 86400 * 2:
                sniper_score = 70.0
            if pnl_gt5x >= 1:
                sniper_score = max(sniper_score, 55.0)
            # Insider heuristic: sustained high win-rate with few tokens traded.
            insider_score = 0.0
            if winrate >= 0.75 and int(detail.get("token_num") or 0) <= 5 and total_tx >= 5:
                insider_score = 65.0
            tags = detail.get("tags") or []
            if any(t in ("smart_degen", "kol") for t in tags):
                insider_score = max(insider_score, 40.0)
            activity_side = 0
            for row in activities:
                if not isinstance(row, dict):
                    continue
                side = str(row.get("type", row.get("side", ""))).lower()
                if side in ("buy", "sell"):
                    activity_side += 1 if side == "buy" else -1
            return {
                "insider_score": insider_score,
                "sniper_score": sniper_score,
                "wash_trading_score": wash_score,
                "pump_dump_participant": bool(pnl_gt5x >= 2 and realized < 0),
                "flipper": bool(buy_count >= 10 and sell_count >= 10 and avg_hold and avg_hold <= 86400),
                "win_rate": winrate,
                "pnl": {"realized": realized, "unrealized": 0},
                "net_activity": activity_side,
                "source": "gmgn",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN wallet_intelligence failed: %s", e)
        return {}

    # ═══════════════════════════════════════════════════════════════
    # LIVE TRADES
    # ═══════════════════════════════════════════════════════════════

    async def get_token_trades(
        self,
        address: str,
        chain: str = "solana",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent trades for a token."""
        result = await cached_or_fetch_list(
            f"trades:{chain}:{address}",
            TRADE_CACHE_TTL,
            self._fetch_trades,
            address,
            chain,
            limit,
        )
        if result:
            return result

        # Fallback: DexScreener trades
        return await fallback_dexscreener_trades(self._request, address, chain, limit)

    async def _fetch_trades(self, address: str, chain: str, limit: int) -> list[dict[str, Any]] | None:
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            # FIX (TOP-100 #9, 2026-08-23): this block referenced an undefined
            # `resp` (NameError on every call) — _agent_get already returns the
            # parsed JSON body (or None), so use `data` directly.
            data = await self._agent_get(
                "/v1/market/token_top_traders",
                {"chain": chain_slug, "address": address, "limit": min(limit, 100)},
            )
            if data is not None:
                trades = (data.get("data") or {}).get("history", data.get("data", data.get("trades", [])))
                if isinstance(trades, list):
                    return normalize_trades(trades, "gmgn")
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN trades failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # HOLDER ANALYSIS
    # ═══════════════════════════════════════════════════════════════

    async def get_holder_analysis(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Get holder breakdown including concentration, sniper wallets, top holders."""
        result = await cached_or_fetch(
            f"holders:{chain}:{address}",
            HOLDER_CACHE_TTL,
            self._fetch_holder_analysis,
            address,
            chain,
        )
        if result:
            return result

        # Fallback: Birdeye holder data
        return await fallback_birdeye_holders(self._request, address)

    async def _fetch_holder_analysis(self, address: str, chain: str) -> dict[str, Any] | None:
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            data = await self._agent_get(
                "/v1/market/token_top_holders",
                {
                    "chain": chain_slug,
                    "address": address,
                    "limit": 100,
                    "order_by": "amount_percentage",
                    "direction": "desc",
                },
            )
            if not data:
                return None
            rows = (data.get("data") or {}).get("list", [])
            if not isinstance(rows, list):
                return None
            top = []
            sniper_count = 0
            insider_count = 0
            smart_count = 0
            for h in rows[:20]:
                tags = h.get("tags") or []
                maker_tags = h.get("maker_token_tags") or []
                is_smart = bool(h.get("is_suspicious") is False and ("smart_degen" in tags or "kol" in tags))
                top.append(
                    {
                        "address": h.get("address", ""),
                        "amount_percentage": h.get("amount_percentage"),
                        "usd_value": h.get("usd_value"),
                        "realized_profit": h.get("realized_profit"),
                        "unrealized_profit": h.get("unrealized_profit"),
                        "profit": h.get("profit"),
                        "profit_change": h.get("profit_change"),
                        "is_insider": bool("rat_trader" in maker_tags or "bundler" in maker_tags),
                        "is_smart_money": is_smart,
                        "buy_tx_count": h.get("buy_tx_count_cur", 0),
                        "sell_tx_count": h.get("sell_tx_count_cur", 0),
                        "tags": tags[:6],
                        "maker_token_tags": maker_tags[:6],
                    }
                )
            # Full-list aggregates (snipers = low tx count + early entry)
            for h in rows:
                if "sniper" in (h.get("maker_token_tags") or []):
                    sniper_count += 1
                if "rat_trader" in (h.get("maker_token_tags") or []) or "bundler" in (h.get("maker_token_tags") or []):
                    insider_count += 1
                if "smart_degen" in (h.get("tags") or []) or "kol" in (h.get("tags") or []):
                    smart_count += 1
            if not sniper_count:
                # Fallback heuristic: fresh entry with ≤2 buy txs at launch-time profit
                sniper_count = sum(
                    1
                    for h in rows
                    if int(h.get("buy_tx_count_cur", 0) or 0) <= 2 and _f(h.get("profit")) and _f(h.get("profit")) > 0
                )
            return {
                "address": address,
                "chain": chain,
                "total_holders": len(rows),
                "top_10_pct": _sum_top_pct(rows),
                "top_holders": top,
                "sniper_count": sniper_count,
                "insider_count": insider_count,
                "smart_money_count": smart_count,
                "fresh_wallet_pct": 0,
                "source": "gmgn",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN holders failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # SMART MONEY
    # ═══════════════════════════════════════════════════════════════

    async def get_smart_money(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Get smart money/KOL activity for a token."""
        result = await cached_or_fetch(
            f"smartmoney:{chain}:{address}",
            TOKEN_CACHE_TTL,
            self._fetch_smart_money,
            address,
            chain,
        )
        if result:
            return result
        return {"address": address, "smart_money_activity": [], "source": "none"}

    async def _fetch_smart_money(self, address: str, chain: str) -> dict[str, Any] | None:
        holders = await self.get_holder_analysis(address, chain)
        security = await self.get_token_security(address, chain)
        if not holders or holders.get("source") != "gmgn":
            return None
        activity = []
        for h in holders.get("top_holders", []):
            if h.get("is_smart_money"):
                buys = int(h.get("buy_tx_count") or 0)
                sells = int(h.get("sell_tx_count") or 0)
                activity.append(
                    {
                        "wallet": h.get("address", ""),
                        "buy_7d": buys,
                        "sell_7d": sells,
                        "unrealized_profit": h.get("unrealized_profit"),
                        "realized_profit": h.get("realized_profit"),
                    }
                )
        return {
            "address": address,
            "chain": chain,
            "smart_money_activity": activity,
            "smart_money_count": int(holders.get("smart_money_count") or 0),
            "kol_count": sum(1 for h in holders.get("top_holders", []) if "kol" in (h.get("tags") or [])),
            "buy_pressure": None,
            "total_profit_pnl": None,
            "insider_rate": security.get("suspected_insider_hold_rate") if security else None,
            "sniper_count": security.get("sniper_count") if security else None,
            "source": "gmgn",
            "timestamp": datetime.now(UTC).isoformat(),
        }

    # ═══════════════════════════════════════════════════════════════
    # NEW PAIRS / TOKEN LAUNCHES
    # ═══════════════════════════════════════════════════════════════

    async def get_new_pairs(self, chain: str = "solana", limit: int = 50) -> list[dict[str, Any]]:
        """Get newest token launches from GMGN."""
        result = await cached_or_fetch_list(
            f"newpairs:{chain}",
            30,  # 30 second cache for launch detection
            self._fetch_new_pairs,
            chain,
            limit,
        )
        if result:
            return result

        # Fallback: DexScreener new pairs
        return await fallback_dexscreener_new_pairs(self._request, chain, limit)

    async def _fetch_new_pairs(self, chain: str, limit: int) -> list[dict[str, Any]] | None:
        if not self._available:
            return None
        try:
            buckets = await self.get_discover(chain, types=["new_creation"], limit=limit)
            rows = buckets.get("new_creation", [])
            if rows:
                return rows
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN new_pairs failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # AGENT API (openapi.gmgn.ai) — security / trending / discover /
    # wallet detail / kline. Auth: X-APIKEY + timestamp + client_id.
    # ═══════════════════════════════════════════════════════════════
    async def get_token_security(self, address: str, chain: str = "solana") -> dict[str, Any]:
        """Get GMGN token security/audit info (honeypot, renounce, taxes, holder rate).

        Live endpoint: ``GET /v1/token/security`` on the Agent API (verified
        200 with JSON against openapi.gmgn.ai). Returns a normalized dict with
        the keys the caching-shield / scanner callers rely on (``is_honeypot``,
        ``renounced_mint``, ``burn_status``, ``top_10_holder_rate``,
        ``rug_ratio``, ``sniper_count``, ``suspected_insider_hold_rate``).
        """
        if not self.api_key:
            return {"address": address, "source": "none"}
        result = await cached_or_fetch(
            f"security:{chain}:{address}",
            TOKEN_CACHE_TTL,
            self._fetch_token_security,
            address,
            chain,
        )
        if result:
            return result
        return {"address": address, "source": "none"}

    async def _fetch_token_security(self, address: str, chain: str) -> dict[str, Any] | None:
        """Fetch token security from the GMGN Agent API."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_OPENAPI_BASE}/v1/token/security",
                headers=agent_headers(self.api_key),
                params=agent_auth_params(self.api_key, chain=chain_slug, address=address),
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data or data.get("code") != 0:
                    logger.debug("GMGN token_security bad code: %s", (data or {}).get("code"))
                    return None
                sec = data.get("data") or {}
                return {
                    "address": address,
                    "chain": chain,
                    "is_honeypot": bool(sec.get("is_honeypot", sec.get("honeypot", False))),
                    "renounced_mint": sec.get("renounced_mint", sec.get("renounced")),
                    "burn_status": sec.get("burn_status"),
                    "burn_ratio": sec.get("burn_ratio"),
                    "top_10_holder_rate": sec.get("top_10_holder_rate", 0),
                    "rug_ratio": sec.get("rug_ratio", 0),
                    "sniper_count": sec.get("sniper_count", 0),
                    "suspected_insider_hold_rate": sec.get("suspected_insider_hold_rate", 0),
                    "buy_tax": sec.get("buy_tax"),
                    "sell_tax": sec.get("sell_tax"),
                    "average_tax": sec.get("average_tax"),
                    "is_open_source": sec.get("is_open_source"),
                    "is_blacklist": sec.get("is_blacklist", sec.get("blacklist")),
                    "can_not_sell": sec.get("can_not_sell"),
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            logger.debug("GMGN token_security HTTP %s for %s", resp.status_code, address)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN token_security failed: %s", e)
        return None

    async def get_trending(
        self,
        chain: str = "solana",
        limit: int = 50,
        interval: str = "1h",
    ) -> list[dict[str, Any]]:
        """Get trending/rank tokens from the GMGN Agent API.

        Live endpoint: ``GET /v1/market/rank`` (verified 200). The response is
        double-wrapped (``data.data.rank``) — unwrapped here into a flat list.
        ``interval`` is accepted for compatibility with the databus price
        provider callers (1m / 5m / 1h / 6h / 24h).
        """
        result = await cached_or_fetch_list(
            f"trending:{chain}:{interval}",
            TRADE_CACHE_TTL,
            self._fetch_trending,
            chain,
            interval,
            limit,
        )
        if result:
            return result
        return []

    async def _fetch_trending(self, chain: str, interval: str, limit: int) -> list[dict[str, Any]] | None:
        """Fetch trending rank from the GMGN Agent API (double-wrapped)."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_OPENAPI_BASE}/v1/market/rank",
                headers=agent_headers(self.api_key),
                params=agent_auth_params(
                    self.api_key,
                    chain=chain_slug,
                    interval=interval,
                    limit=max(1, min(int(limit), 100)),
                ),
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data or data.get("code") != 0:
                    logger.debug("GMGN trending bad code: %s", (data or {}).get("code"))
                    return None
                # Double-wrapped: {code:0, data: {code:0, data: {rank: [...]}}}
                inner = (data.get("data") or {}).get("data") or {}
                rank = inner.get("rank", (data.get("data") or {}).get("rank", []))
                if isinstance(rank, list):
                    return [
                        {
                            "address": p.get("address", ""),
                            "chain": chain,
                            "symbol": p.get("symbol", ""),
                            "name": p.get("name", ""),
                            "price": float(p.get("price", 0) or 0),
                            "price_change_1h": float(p.get("price_change_percent1h", 0) or 0),
                            "price_change_24h": float(
                                p.get("price_change_percent24h", p.get("price_change_percent", 0)) or 0
                            ),
                            "volume_24h": float(p.get("volume", 0) or 0),
                            "liquidity": float(p.get("liquidity", 0) or 0),
                            "market_cap": float(p.get("market_cap", 0) or 0),
                            "holder_count": int(p.get("holder_count", 0) or 0),
                            "swaps_24h": int(p.get("swaps", 0) or 0),
                            "buy_24h": int(p.get("buys", 0) or 0),
                            "sell_24h": int(p.get("sells", 0) or 0),
                            "created_at": p.get("creation_timestamp"),
                            "launchpad": p.get("launchpad"),
                            "source": "gmgn",
                        }
                        for p in rank
                    ]
            logger.debug("GMGN trending HTTP %s", resp.status_code)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN trending failed: %s", e)
        return None

    async def get_discover(self, chain: str = "solana", limit: int = 50) -> list[dict[str, Any]]:
        """Get discover/trenches tokens (incl. new_creation) from the Agent API.

        Live endpoint: ``POST /v1/trenches`` (verified 200 with
        ``new_creation`` / ``near_completion`` / ``completed`` sections).
        Returns the flattened ``new_creation`` list — the same launch-detection
        shape as :meth:`get_new_pairs` but from the Agent API (no Cloudflare).
        """
        result = await cached_or_fetch_list(
            f"discover:{chain}",
            TRADE_CACHE_TTL,
            self._fetch_discover,
            chain,
            limit,
        )
        if result:
            return result
        return []

    async def _fetch_discover(self, chain: str, limit: int) -> list[dict[str, Any]] | None:
        """Fetch trenches/new creations from the GMGN Agent API."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            body = {
                "version": "v2",
                "new_creation": {
                    "filters": ["offchain", "onchain"],
                    "launchpad_platform_v2": True,
                    "limit": max(1, min(int(limit), 80)),
                },
            }
            resp = await self._request(
                "POST",
                f"{GMGN_OPENAPI_BASE}/v1/trenches",
                headers=agent_headers(self.api_key),
                params=agent_auth_params(self.api_key, chain=chain_slug),
                json=body,
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data or data.get("code") != 0:
                    logger.debug("GMGN discover bad code: %s", (data or {}).get("code"))
                    return None
                # new_creation arrives as a plain list of token rows.
                section = (data.get("data") or {}).get("new_creation") or []
                rows = section if isinstance(section, list) else section.get("rank", section.get("list", []))
                if isinstance(rows, list):
                    return [
                        {
                            "address": p.get("address", ""),
                            "chain": chain,
                            "symbol": p.get("symbol", ""),
                            "name": p.get("name", ""),
                            "price": float(p.get("price", 0) or 0),
                            "liquidity": float(p.get("liquidity", 0) or 0),
                            "market_cap": float(p.get("market_cap", 0) or 0),
                            "volume_24h": float(p.get("volume_24h", p.get("volume", 0)) or 0),
                            "created_at": p.get("created_timestamp", p.get("creation_timestamp")),
                            "launchpad": p.get("launchpad"),
                            "holder_count": int(p.get("holder_count", 0) or 0),
                            "source": "gmgn",
                        }
                        for p in rows
                    ]
            logger.debug("GMGN discover HTTP %s", resp.status_code)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN discover failed: %s", e)
        return None

    async def get_wallet_detail(self, wallet: str, chain: str = "solana", period: str = "7d") -> dict[str, Any]:
        """Get wallet stats + activity from the GMGN Agent API.

        Live endpoints: ``GET /v1/user/wallet_stats`` and
        ``GET /v1/user/wallet_activity`` (both verified 200). Merges the stats
        (realized PnL, buy/sell counts, winrate, token count, avg holding) with
        recent activity rows so the derived wallet-intelligence layer
        (:meth:`get_wallet_intelligence`) can compute behavior heuristics.
        """
        if not self.api_key:
            return {"wallet": wallet, "source": "none"}
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_OPENAPI_BASE}/v1/user/wallet_stats",
                headers=agent_headers(self.api_key),
                params=agent_auth_params(
                    self.api_key,
                    chain=chain_slug,
                    wallet_address=wallet,
                    period=period,
                ),
            )
            if resp.status_code != 200:
                logger.debug("GMGN wallet_stats HTTP %s", resp.status_code)
                return {"wallet": wallet, "source": "none"}
            data = resp.json()
            if not data or data.get("code") != 0:
                logger.debug("GMGN wallet_stats bad code: %s", (data or {}).get("code"))
                return {"wallet": wallet, "source": "none"}
            stats = data.get("data") or {}
            pnl_stat = stats.get("pnl_stat") or {}
            common = stats.get("common") or {}
            # Activity feed (best-effort — wallet_stats alone is the core shape).
            activities: list[Any] = []
            try:
                act_resp = await self._request(
                    "GET",
                    f"{GMGN_OPENAPI_BASE}/v1/user/wallet_activity",
                    headers=agent_headers(self.api_key),
                    params=agent_auth_params(
                        self.api_key,
                        chain=chain_slug,
                        wallet_address=wallet,
                        limit=50,
                    ),
                )
                if act_resp.status_code == 200:
                    act_data = act_resp.json()
                    if act_data and act_data.get("code") == 0:
                        activities = (act_data.get("data") or {}).get("activities", []) or []
            except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
                logger.debug("GMGN wallet_activity failed (stats kept): %s", e)
            return {
                "wallet": wallet,
                "chain": chain,
                "realized_profit": stats.get("realized_profit", 0),
                "realized_profit_pnl": stats.get("realized_profit_pnl", 0),
                "buy": int(stats.get("buy", 0) or 0),
                "sell": int(stats.get("sell", 0) or 0),
                "winrate": pnl_stat.get("winrate", 0),
                "token_num": int(pnl_stat.get("token_num", 0) or 0),
                "avg_holding_period": pnl_stat.get("avg_holding_period", 0),
                "pnl_gt_5x_num": int(pnl_stat.get("pnl_gt_5x_num", 0) or 0),
                "tags": common.get("tags", []) if isinstance(common.get("tags"), list) else [],
                "activities": activities,
                "source": "gmgn",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN wallet_detail failed: %s", e)
        return {"wallet": wallet, "source": "none"}

    async def get_kline(
        self,
        address: str,
        chain: str = "solana",
        period: str = "1h",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get candlestick (OHLCV) data for a token from the GMGN Agent API.

        Live endpoint: ``GET /v1/market/token_kline`` with ``resolution``
        (verified 200 with a ``data.list`` of candles). ``period`` maps to the
        API's ``resolution`` param (1m / 5m / 15m / 30m / 1h / 4h / 1d).
        """
        result = await cached_or_fetch_list(
            f"kline:{chain}:{address}:{period}",
            TRADE_CACHE_TTL,
            self._fetch_kline,
            address,
            chain,
            period,
            limit,
        )
        if result:
            return result
        return []

    async def _fetch_kline(self, address: str, chain: str, period: str, limit: int) -> list[dict[str, Any]] | None:
        """Fetch OHLCV candles from the GMGN Agent API."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_OPENAPI_BASE}/v1/market/token_kline",
                headers=agent_headers(self.api_key),
                params=agent_auth_params(
                    self.api_key,
                    chain=chain_slug,
                    address=address,
                    resolution=period,
                    limit=max(1, min(int(limit), 1000)),
                ),
            )
            if resp.status_code == 200:
                data = resp.json()
                if not data or data.get("code") != 0:
                    logger.debug("GMGN kline bad code: %s", (data or {}).get("code"))
                    return None
                candles = (data.get("data") or {}).get("list", [])
                if isinstance(candles, list):
                    return [
                        {
                            "time": c.get("time"),
                            "open": float(c.get("open", 0) or 0),
                            "high": float(c.get("high", 0) or 0),
                            "low": float(c.get("low", 0) or 0),
                            "close": float(c.get("close", 0) or 0),
                            "volume": float(c.get("volume", 0) or 0),
                            "amount": float(c.get("amount", 0) or 0),
                            "source": "gmgn",
                        }
                        for c in candles
                    ]
            logger.debug("GMGN kline HTTP %s", resp.status_code)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN kline failed: %s", e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════
    # AGENT API REQUEST HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _agent_params(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Merge caller params with the Agent API exist-auth query params."""
        merged = dict(params or {})
        merged["timestamp"] = int(time.time())
        merged["client_id"] = str(uuid.uuid4())
        return merged

    async def _agent_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """GET against the Agent API with exist-auth + 429-aware backoff."""
        if not self.api_key:
            logger.debug("GMGN Agent API: no API key configured")
            return None
        now = time.time()
        if now < self._banned_until:
            logger.debug("GMGN Agent API: banned until %s", self._banned_until)
            return None
        if now < self._next_allowed_at:
            return None  # caller falls through to next source; no spam

        try:
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}{path}",
                headers=self.headers,
                params=self._agent_params(params),
            )
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN Agent API GET %s failed: %s", path, e)
            return None
        return self._handle_agent_response(resp, path)

    async def _agent_post(
        self, path: str, params: dict[str, Any] | None, body: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if not self.api_key:
            logger.debug("GMGN Agent API: no API key configured")
            return None
        now = time.time()
        if now < self._banned_until or now < self._next_allowed_at:
            return None
        try:
            resp = await self._request(
                "POST",
                f"{GMGN_BASE}{path}",
                headers=self.headers,
                params=self._agent_params(params),
                body=body,
            )
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN Agent API POST %s failed: %s", path, e)
            return None
        return self._handle_agent_response(resp, path)

    def _handle_agent_response(self, resp: httpx.Response, path: str) -> dict[str, Any] | None:
        """Validate Agent API response; apply rate-limit bookkeeping."""
        if resp.status_code == 429:
            reset = self._parse_reset(resp)
            if reset:
                self._banned_until = max(self._banned_until, float(reset))
            self._next_allowed_at = time.time() + 30
            logger.debug("GMGN Agent API 429 on %s (reset=%s)", path, reset)
            return None
        if resp.status_code == 401 or resp.status_code == 403:
            logger.debug("GMGN Agent API auth error %s on %s", resp.status_code, path)
            self._available = False
            return None
        if resp.status_code != 200:
            logger.debug("GMGN Agent API HTTP %s on %s", resp.status_code, path)
            return None
        try:
            payload = resp.json()
        except ValueError:
            logger.debug("GMGN Agent API non-JSON on %s (Cloudflare HTML?)", path)
            return None
        if not isinstance(payload, dict):
            return None
        code = payload.get("code")
        if code not in (0, None, 200):
            logger.debug(
                "GMGN Agent API error code=%s on %s: %s", code, path, payload.get("error") or payload.get("message")
            )
            return None
        return payload

    @staticmethod
    def _parse_reset(resp: httpx.Response) -> float | None:
        """Read rate-limit reset from X-RateLimit-Reset header or body reset_at."""
        raw = resp.headers.get("X-RateLimit-Reset")
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        try:
            body = resp.json()
            raw = body.get("reset_at") if isinstance(body, dict) else None
            if raw:
                return float(raw)
        except ValueError:
            pass
        return None

    # ═══════════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════════

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        """Proxy-first HTTP request with a single direct retry.

        When a proxied client is configured it is tried first; on a proxy
        failure (403/5xx/timeout — Cloudflare IP-block family) the proxy is
        marked failed (cooldown) and the request is retried once against the
        direct client. Transport errors propagate to callers, which already
        guard each fetch with try/except.
        """
        if self._proxy_client is not None:
            try:
                proxy_resp = await self._proxy_client.request(method, url, headers=headers, params=params, json=json)
            except (httpx.HTTPError, ConnectionError, TimeoutError):
                self._rotate_proxy()
                return await self.client.request(method, url, headers=headers, params=params, json=json)
            if proxy_resp.status_code in (403, 429) or proxy_resp.status_code >= 500:
                self._rotate_proxy()
                return await self.client.request(method, url, headers=headers, params=params, json=json)
            return proxy_resp
        return await self.client.request(method, url, headers=headers, params=params, json=json)

    def _rotate_proxy(self) -> None:
        """Mark the current proxy failed and pick the next healthy egress."""
        self._rotator.mark_failed(self._proxy_url)
        self._proxy_url = self._rotator.next_after_failure()
        self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)

    async def close(self) -> None:
        """Clean up resources."""
        await self.client.aclose()
        if self._proxy_client is not None:
            await self._proxy_client.aclose()


def _f(value: Any) -> float:
    """Best-effort float coercion (strings from the API included)."""
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sum_top_pct(rows: list[dict[str, Any]], cap: int = 10) -> float | None:
    """Sum supply % of the first ``cap`` holders (Agent API rows)."""
    if not isinstance(rows, list):
        return None
    total = 0.0
    seen = 0
    for h in rows:
        pct = h.get("amount_percentage")
        try:
            if pct is None:
                continue
            total += float(pct)
            seen += 1
            if seen >= cap:
                break
        except (TypeError, ValueError):
            continue
    return total if seen else None


# ── Singleton ──────────────────────────────────────────────────────

_gmgn_provider: GMGNProvider | None = None


def get_gmgn_provider() -> GMGNProvider:
    """Get or create the GMGN provider singleton."""
    global _gmgn_provider
    if _gmgn_provider is None:
        _gmgn_provider = GMGNProvider()
    return _gmgn_provider

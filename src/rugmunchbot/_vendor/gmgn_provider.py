# VENDORED 2026-08-17 from rmi-backend/app/databus/gmgn_provider.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
#!/usr/bin/env python3
"""
GMGN Data Provider — Real GMGN public API integration.
======================================================
Provides: token info, live trades, holder analysis, smart money,
bundle/sniper detection, KOL tracking, new pair discovery.

Uses GMGN's public (undocumented) JSON API — no API key required.
The GMGN Agent API key (GMGN_API_KEY) is accepted but optional; when
present it is sent as the ``X-APIKEY`` header (the auth convention used by
the official GMGN SDK, see ``src/client/OpenApiClient.ts`` in
GMGNAI/gmgn-skills), otherwise browser-like headers are used. Falls back
to DexScreener/Birdeye when GMGN is unavailable.

Cloudflare escape: when residential proxies are configured
(``WEBSHARE_PROXY_LIST`` / ``WEBSHARE_PROXY_USER`` / ``WEBSHARE_PROXY_PASS``)
the provider routes requests through
:class:`app.databus.proxy_rotator.ProxyRotator` — proxy-first, and a
403/5xx/timeout on the proxy egress marks the proxy failed (cooldown) and
retries once direct. With no proxies configured the provider uses a direct
client exactly as before.

Endpoint provenance (verified against public open-source usage 2026-08-09):
  - token info:      /defi/quotation/v1/tokens/{chain}/{address}
  - trades:          /defi/quotation/v1/trades/{chain}/{address}
  - holders:         /vas/api/v1/token_holders/{chain}/{address}
  - smart money:     /defi/quotation/v1/smartmoney/{chain}/walletNew/{wallet}
  - new pairs:       /defi/quotation/v1/rank/{chain}/pump?new_creation=true
"""

from __future__ import annotations

import os
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

# Browser-like headers — GMGN's public API rejects plain curl/httpx UAs.
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
    }
    return aliases.get(chain.lower(), chain.lower())


class GMGNProvider:
    """Real GMGN public API client with fallback cascade."""

    def __init__(self) -> None:
        self.api_key = GMGN_API_KEY
        self.headers = dict(_BROWSER_HEADERS)
        if self.api_key:
            # GMGN Agent API auth convention (official SDK: X-APIKEY header).
            self.headers["X-APIKEY"] = self.api_key
        # Direct client is the always-available fallback egress.
        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        # Proxy-first egress when residential proxies are configured.
        self._rotator = get_proxy_rotator()
        self._proxy_url: str | None = None
        self._proxy_client: httpx.AsyncClient | None = None
        if self._rotator.enabled:
            self._proxy_url = self._rotator.get_rotating_proxy()
            self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)
        # Public API works without a key; _available is only used to decide
        # whether to try GMGN at all (True = always try, fallbacks cover failures).
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
        """Fetch token info from GMGN public API directly."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            # GMGN public token quotation endpoint
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/tokens/{chain_slug}/{address}",
                headers=self.headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = (data.get("data") or {}).get("token", data.get("data", data))
                if not token:
                    return None
                return {
                    "address": address,
                    "chain": chain,
                    "name": token.get("name", ""),
                    "symbol": token.get("symbol", ""),
                    "price": float(token.get("price", 0) or 0),
                    "market_cap": float(token.get("market_cap", 0) or 0),
                    "fdv": float(token.get("fdv", 0) or 0),
                    "liquidity": float(token.get("liquidity", 0) or 0),
                    "volume_24h": float(token.get("volume_24h", 0) or 0),
                    "holders": int(token.get("holder_count", token.get("holders", 0)) or 0),
                    "price_change_1h": float(token.get("price_1h", 0) or 0),
                    "price_change_24h": float(token.get("price_24h", 0) or 0),
                    "buy_24h": int(token.get("buys_24h", 0) or 0),
                    "sell_24h": int(token.get("sells_24h", 0) or 0),
                    "created_at": token.get("creation_timestamp"),
                    "launchpad": token.get("launchpad"),
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            logger.debug("GMGN token_info HTTP %s for %s", resp.status_code, address)
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
        """Fetch live trades from GMGN."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/trades/{chain_slug}/{address}",
                headers=self.headers,
                params={"limit": limit},
            )
            if resp.status_code == 200:
                data = resp.json()
                trades = (data.get("data") or {}).get("history", data.get("data", data.get("trades", [])))
                if isinstance(trades, list):
                    return normalize_trades(trades, "gmgn")
            logger.debug("GMGN trades HTTP %s", resp.status_code)
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
        """Fetch holder analysis from GMGN."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/vas/api/v1/token_holders/{chain_slug}/{address}",
                headers=self.headers,
                params={"orderby": "amount_percentage", "direction": "desc", "limit": 100},
            )
            if resp.status_code == 200:
                data = resp.json()
                rows = (data.get("data") or {}).get("list", [])
                top = []
                for h in rows[:20]:
                    top.append(
                        {
                            "address": h.get("address", ""),
                            "amount_percentage": h.get("amount_percentage"),
                            "realized_profit": h.get("realized_profit"),
                            "unrealized_profit": h.get("unrealized_profit"),
                            "profit_change": h.get("profit_change"),
                            "is_insider": bool(h.get("insider", False)),
                            "is_smart_money": bool(h.get("is_smart_dealer", h.get("smart_money", False))),
                        }
                    )
                return {
                    "address": address,
                    "chain": chain,
                    "total_holders": len(rows),
                    "top_10_pct": None,
                    "top_holders": top,
                    "sniper_count": sum(
                        1 for h in rows if h.get("profit_change", 0) and h.get("buy_tx_count_cur", 0) <= 2
                    ),
                    "insider_count": sum(1 for h in rows if h.get("insider", False)),
                    "fresh_wallet_pct": 0,
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            logger.debug("GMGN holders HTTP %s", resp.status_code)
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
        """Fetch smart money data from GMGN."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/smartmoney/{chain_slug}/walletNew/{address}",
                headers=self.headers,
                params={"period": "7d"},
            )
            if resp.status_code == 200:
                data = resp.json()
                sm = data.get("data", data)
                return {
                    "address": address,
                    "chain": chain,
                    "smart_money_activity": sm.get("activity", []),
                    "kol_trades": sm.get("kol_trades", []),
                    "smart_money_count": sm.get("smart_money_count", 0),
                    "kol_count": sm.get("kol_count", 0),
                    "buy_pressure": sm.get("buy_pressure", 0),
                    "total_profit_pnl": sm.get("total_profit_pnl"),
                    "winrate": sm.get("winrate"),
                    "sol_balance": sm.get("sol_balance"),
                    "buy_7d": sm.get("buy_7d"),
                    "tags": sm.get("tags", []),
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN smart_money failed: %s", e)
        return None

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
        """Fetch new token listings from GMGN (pump.fun new_creation rank)."""
        if not self._available:
            return None
        try:
            chain_slug = _normalize_chain(chain)
            resp = await self._request(
                "GET",
                f"{GMGN_BASE}/defi/quotation/v1/rank/{chain_slug}/pump",
                headers=self.headers,
                params={
                    "limit": limit,
                    "orderby": "created_timestamp",
                    "direction": "desc",
                    "new_creation": "true",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = (data.get("data") or {}).get("rank", data.get("data", data.get("pairs", [])))
                if isinstance(pairs, list):
                    return [
                        {
                            "address": p.get("address", ""),
                            "chain": chain,
                            "symbol": p.get("symbol", ""),
                            "name": p.get("name", ""),
                            "price": float(p.get("price", 0) or 0),
                            "liquidity": float(p.get("liquidity", 0) or 0),
                            "market_cap": float(p.get("market_cap", 0) or 0),
                            "created_at": p.get("creation_timestamp"),
                            "age_seconds": 0,
                            "launchpad": p.get("launchpad", "pump"),
                            "source": "gmgn",
                        }
                        for p in pairs
                    ]
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


# ── Singleton ──────────────────────────────────────────────

_gmgn_provider: GMGNProvider | None = None


def get_gmgn_provider() -> GMGNProvider:
    """Get or create the GMGN provider singleton."""
    global _gmgn_provider
    if _gmgn_provider is None:
        _gmgn_provider = GMGNProvider()
    return _gmgn_provider

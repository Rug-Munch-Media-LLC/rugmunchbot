# Split from the vendored gmgn_provider.py during the de-godding program
# 2026-08-22 (market-data endpoint seam). Method bodies moved VERBATIM — see
# the facade ``gmgn_provider.py`` for provenance and the singleton.
"""GMGN market-data endpoints: token info, trades, new pairs, trending,
discover/trenches, kline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot._vendor.gmgn_fallbacks import (
    agent_auth_params,
    agent_headers,
    cached_or_fetch,
    cached_or_fetch_list,
    fallback_dexscreener_new_pairs,
    fallback_dexscreener_token,
    fallback_dexscreener_trades,
    normalize_trades,
)
from rugmunchbot._vendor.gmgn_transport import (
    GMGN_BASE,
    GMGN_OPENAPI_BASE,
    TOKEN_CACHE_TTL,
    TRADE_CACHE_TTL,
    _normalize_chain,
    logger as _logger,
)

logger = _logger


class _GMGNMarketEndpointsMixin:
    """Market-data endpoint methods for :class:`GMGNProvider` (bodies verbatim)."""

    if TYPE_CHECKING:  # transport attrs provided by _GMGNTransportMixin
        import httpx

        api_key: str
        headers: dict[str, str]
        _available: bool

        async def _request(
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None,
            json: Any = None,
        ) -> httpx.Response: ...

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

    async def _fetch_trades(
        self, address: str, chain: str, limit: int
    ) -> list[dict[str, Any]] | None:
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
                trades = (data.get("data") or {}).get(
                    "history", data.get("data", data.get("trades", []))
                )
                if isinstance(trades, list):
                    return normalize_trades(trades, "gmgn")
            logger.debug("GMGN trades HTTP %s", resp.status_code)
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GMGN trades failed: %s", e)
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
                pairs = (data.get("data") or {}).get(
                    "rank", data.get("data", data.get("pairs", []))
                )
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
    # AGENT API (openapi.gmgn.ai) — trending / discover / kline.
    # Auth: X-APIKEY + timestamp + client_id.
    # ═══════════════════════════════════════════════════════════════

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

    async def _fetch_trending(
        self, chain: str, interval: str, limit: int
    ) -> list[dict[str, Any]] | None:
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
                                p.get("price_change_percent24h", p.get("price_change_percent", 0))
                                or 0
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
                rows = (
                    section
                    if isinstance(section, list)
                    else section.get("rank", section.get("list", []))
                )
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

    async def _fetch_kline(
        self, address: str, chain: str, period: str, limit: int
    ) -> list[dict[str, Any]] | None:
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

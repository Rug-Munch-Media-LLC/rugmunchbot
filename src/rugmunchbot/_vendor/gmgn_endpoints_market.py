# VENDORED 2026-08-24 from rmi-backend/app/databus/gmgn_endpoints_market.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""GMGN market endpoints — split from gmgn_provider.py (de-godding 2026-08-23).

Methods operate on ``self`` state initialized by ``GMGNProvider.__init__``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from datetime import UTC, datetime

from rugmunchbot._vendor.gmgn_transport import (
    _f,
    _normalize_chain,
    _sum_top_pct,
)  # noqa: E402

logger = logging.getLogger(__name__)


class MarketEndpointsMixin:
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

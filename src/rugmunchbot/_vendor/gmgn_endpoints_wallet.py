# VENDORED 2026-08-24 from rmi-backend/app/databus/gmgn_endpoints_wallet.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""GMGN wallet endpoints — split from gmgn_provider.py (de-godding 2026-08-23).

Methods operate on ``self`` state initialized by ``GMGNProvider.__init__``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class WalletEndpointsMixin:
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

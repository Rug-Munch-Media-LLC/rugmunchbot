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

# Split from the vendored gmgn_provider.py during the de-godding program
# 2026-08-22 (wallet/security endpoint seam). Method bodies moved VERBATIM —
# see the facade ``gmgn_provider.py`` for provenance and the singleton.
# 2026-08-23 FIX: _fetch_smart_money no longer calls the WALLET-scoped
# endpoint with a token address (fabricated +0 nets); token-scoped paths
# first, holder-derived fallback second. This file is bot-owned (not in
# vendor-from-backend.sh's one-way pull list) — safe to maintain here.
"""GMGN wallet + security endpoints: wallet intelligence/detail, holder
analysis, smart money, token security."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot._vendor.gmgn_fallbacks import (
    agent_auth_params,
    agent_headers,
    cached_or_fetch,
    fallback_birdeye_holders,
)
from rugmunchbot._vendor.gmgn_transport import (
    GMGN_BASE,
    GMGN_OPENAPI_BASE,
    HOLDER_CACHE_TTL,
    TOKEN_CACHE_TTL,
    _normalize_chain,
    logger as _logger,
)

logger = _logger


class _GMGNWalletEndpointsMixin:
    """Wallet/security endpoint methods for :class:`GMGNProvider` (bodies verbatim)."""

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
                wash_score = (
                    round((1 - abs(ratio - 0.5) * 2) * 100, 1) if 0.25 <= ratio <= 0.75 else 0.0
                )
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
                "flipper": bool(
                    buy_count >= 10 and sell_count >= 10 and avg_hold and avg_hold <= 86400
                ),
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
                            "is_smart_money": bool(
                                h.get("is_smart_dealer", h.get("smart_money", False))
                            ),
                        }
                    )
                return {
                    "address": address,
                    "chain": chain,
                    "total_holders": len(rows),
                    "top_10_pct": None,
                    "top_holders": top,
                    "sniper_count": sum(
                        1
                        for h in rows
                        if h.get("profit_change", 0) and h.get("buy_tx_count_cur", 0) <= 2
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
        """Fetch smart money data for a TOKEN.

        2026-08-23 fix: this used to call the WALLET-scoped endpoint
        (``/smartmoney/{chain}/walletNew/{wallet}``) with a token address,
        which can only return empty/garbage rows — every report then showed
        a fabricated "Smart-Money Net: +0". Now queries GMGN's token-scoped
        smart-money endpoints (``tokenNew`` / ``token`` spellings) first and
        falls back to holder-derived smart money (backend methodology) when
        both miss.
        """
        if not self._available:
            return None
        chain_slug = _normalize_chain(chain)
        for scope in ("tokenNew", "token"):
            try:
                resp = await self._request(
                    "GET",
                    f"{GMGN_BASE}/defi/quotation/v1/smartmoney/{chain_slug}/{scope}/{address}",
                    headers=self.headers,
                    params={"period": "7d"},
                )
                if resp.status_code != 200:
                    continue
                sm = resp.json().get("data", resp.json())
                if not isinstance(sm, dict):
                    continue
                activity = sm.get("activity") or sm.get("smart_degens") or []
                if not isinstance(activity, list):
                    activity = []
                if not activity and not (
                    sm.get("smart_degen_count") or sm.get("smart_money_count")
                ):
                    continue
                return {
                    "address": address,
                    "chain": chain,
                    "smart_money_activity": activity,
                    "kol_trades": sm.get("kol_trades", []),
                    "smart_money_count": int(
                        sm.get("smart_degen_count", sm.get("smart_money_count", 0)) or 0
                    ),
                    "kol_count": int(sm.get("kol_count", 0) or 0),
                    "buy_pressure": sm.get("buy_pressure"),
                    "total_profit_pnl": sm.get("total_profit_pnl"),
                    "winrate": sm.get("winrate"),
                    "tags": sm.get("tags", []),
                    "source": "gmgn",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
                logger.debug("GMGN token smart_money %s failed: %s", scope, e)
        # Fallback: derive smart-money rows from flagged top holders
        # (mirrors rmi-backend's gmgn_provider methodology).
        holders = await self.get_holder_analysis(address, chain)
        if not holders or holders.get("source") != "gmgn":
            return None
        activity = []
        for h in holders.get("top_holders", []):
            if isinstance(h, dict) and h.get("is_smart_money"):
                activity.append(
                    {
                        "wallet": h.get("address", ""),
                        "buy_7d": int(h.get("buy_tx_count") or 0),
                        "sell_7d": int(h.get("sell_tx_count") or 0),
                        "realized_profit": h.get("realized_profit"),
                        "unrealized_profit": h.get("unrealized_profit"),
                    }
                )
        if not activity:
            return None
        return {
            "address": address,
            "chain": chain,
            "smart_money_activity": activity,
            "smart_money_count": int(holders.get("smart_money_count") or 0),
            "kol_count": sum(
                1
                for h in holders.get("top_holders", [])
                if isinstance(h, dict) and "kol" in (h.get("tags") or [])
            ),
            "buy_pressure": None,
            "total_profit_pnl": None,
            "source": "gmgn",
            "timestamp": datetime.now(UTC).isoformat(),
        }

    # ═══════════════════════════════════════════════════════════════
    # AGENT API (openapi.gmgn.ai) — security / wallet detail.
    # Auth: X-APIKEY + timestamp + client_id.
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

    async def get_wallet_detail(
        self, wallet: str, chain: str = "solana", period: str = "7d"
    ) -> dict[str, Any]:
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

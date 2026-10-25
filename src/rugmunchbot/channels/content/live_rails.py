"""Live-rail fallbacks — the endpoints that ACTUALLY carry data (2026-10-17).

Diagnosis (report 124): every channel job was posting 0 items. The rails the
pipeline reads were dead or empty while live data sat one endpoint over:

* ``/api/v1/launches/alerts``  → ``{"alerts": [], "count": 0}`` (empty)
* ``/api/v1/launches/feed``    → ``{"launches": [], "count": 0}`` (empty)
* ``/api/v1/data/scan-history``→ HTTP 404 (route removed)
* ``/api/v1/alerts/recent``    → HTTP 404 (rugbrain route, not backend)
* ``wallet_memory.token_transfers`` → 0 rows (whale rail queried an empty table)

Meanwhile these endpoints carry live data on the same backend:

* ``/api/v1/market/launches``   → recent launches (address/symbol/chain/risk)
* ``/api/v1/market/whales``     → recent whale moves (wallet/token/amount/chain)
* ``/api/v1/market/alerts-feed``→ scam/pump-dump alerts (severity/token/chain)
* ``/api/v1/shill/campaigns``   → coordinated shill campaigns (accounts/score)

This module is the fallback layer: each ``fetch_*_live`` returns the SAME
normalized shape the primary rail returns, so the pipeline can merge them
without a second code path. Fail-open by contract — a dead endpoint returns
``[]`` and the primary rail's (empty) result stands.

The whale rail additionally falls back to ``wallet_memory.evm_transfers``
(394M rows, live) when ``token_transfers`` (0 rows) is empty — the table the
fetcher was pointed at was never populated.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.content.helpers import _as_list, _image_url
from rugmunchbot.data_resolver import backend_get_json

__all__ = [
    "fetch_alerts_feed_live",
    "fetch_launches_live",
    "fetch_shill_campaigns_live",
    "fetch_whales_live",
]


def _risk_level(score: Any) -> str:
    """Map a numeric risk score to the channel vocabulary (fail-open)."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 80:
        return "critical"
    if value >= 60:
        return "high"
    if value >= 40:
        return "medium"
    return "low"


async def fetch_launches_live(chain: str = "solana", limit: int = 5) -> list[dict[str, Any]]:
    """Recent launches from ``/api/v1/market/launches`` (the LIVE rail).

    Fallback for ``/api/v1/launches/feed`` (empty). Returns the same
    normalized launch dicts ``_fetch_launches`` produces so the alpha lane
    can merge them untouched. Never raises.
    """
    body = await backend_get_json(
        "/api/v1/market/launches",
        params={"limit": max(1, min(limit * 4, 50))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    preferred: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for item in _as_list(body.get("items")):
        address = str(item.get("address") or "").strip()
        if not address:
            continue
        item_chain = str(item.get("chain") or chain).strip()
        launch = {
            "address": address,
            "name": str(item.get("name") or "Unknown").strip(),
            "symbol": str(item.get("symbol") or "???").strip(),
            "chain": item_chain,
            "risk_level": _risk_level(item.get("risk_score")),
            "risk_score": item.get("risk_score"),
            "risk_flags": [],
            "age_hours": (item.get("age_minutes") or 0) / 60.0,
            "liquidity_usd": item.get("liquidity"),
            "holders": item.get("holders"),
            "market_cap": item.get("market_cap"),
            "change_24h": item.get("change_24h"),
            "image_url": _image_url(item.get("image_url") or item.get("image")),
        }
        if chain and item_chain == chain:
            preferred.append(launch)
        else:
            other.append(launch)
    return (preferred + other)[:limit]


async def fetch_whales_live(chain: str = "solana", limit: int = 5) -> list[dict[str, Any]]:
    """Recent whale moves from ``/api/v1/market/whales`` (the LIVE rail).

    Fallback for the ClickHouse ``token_transfers`` rail (0 rows). Returns
    the same normalized whale dicts ``fetch_whale_moves`` produces. Never
    raises.

    Chain handling: the live rail is multi-chain and may carry only chains
    other than the requested one (observed 2026-10-17: only ``base`` while
    alpha asks for ``solana``). A fallback exists to keep the lane FLOWING,
    so requested-chain items are preferred but cross-chain items are kept
    rather than dropped — an empty channel is the defect being fixed.
    """
    body = await backend_get_json(
        "/api/v1/market/whales",
        params={"limit": max(1, min(limit * 4, 50))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    preferred: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for item in _as_list(body.get("items")):
        token = str(item.get("token_address") or "").strip()
        wallet = str(item.get("from_wallet") or item.get("to_wallet") or "").strip()
        if not token and not wallet:
            continue
        item_chain = str(item.get("chain") or chain).strip()
        move = {
            "kind": "whale_move",
            "chain": item_chain,
            "token_address": token,
            "token_symbol": str(item.get("token_symbol") or "???").strip(),
            "wallet": wallet,
            "wallet_label": str(item.get("wallet_label") or "").strip() or None,
            "amount_usd": item.get("value_usd") or item.get("amount"),
            "direction": str(item.get("type") or "").strip(),
            "tx_hash": str(item.get("tx_hash") or "").strip(),
            "timestamp": str(item.get("timestamp") or "").strip(),
            "is_smart_money": bool(item.get("is_smart_money")),
            "image_url": "",
        }
        if chain and item_chain == chain:
            preferred.append(move)
        else:
            other.append(move)
    return (preferred + other)[:limit]


async def fetch_alerts_feed_live(limit: int = 5) -> list[dict[str, Any]]:
    """Scam/pump-dump alerts from ``/api/v1/market/alerts-feed`` (LIVE rail).

    Fallback for the dead ``/api/v1/alerts/recent`` + empty launch-alert
    rail. Returns alert-shaped dicts the alerts lane can merge. Never raises.
    """
    body = await backend_get_json(
        "/api/v1/market/alerts-feed",
        params={"limit": max(1, min(limit, 50))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    alerts: list[dict[str, Any]] = []
    for item in _as_list(body.get("items"))[:limit]:
        token = str(item.get("token") or "").strip()
        if not token:
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        alerts.append(
            {
                "address": token,
                "token_address": token,
                "name": token,
                "symbol": token,
                "chain": str(item.get("chain") or "").strip(),
                "risk_level": str(item.get("severity") or "medium").strip().lower(),
                "risk_score": None,
                "risk_flags": [str(item.get("type") or "alert")],
                "title": str(item.get("title") or "").strip(),
                "summary": str(item.get("description") or "").strip(),
                "verdict": str(item.get("description") or "").strip(),
                "condition": str(item.get("type") or "alert").replace("_", " "),
                "alert_type": str(item.get("type") or "alert").replace("_", " "),
                "url": str(item.get("id") or "").strip(),
                "created_at": str(item.get("created_at") or "").strip(),
                "liquidity": meta.get("liquidity"),
                "market_cap": meta.get("market_cap"),
                "image_url": "",
            }
        )
    return alerts


async def fetch_shill_campaigns_live(limit: int = 5) -> list[dict[str, Any]]:
    """Coordinated shill campaigns from ``/api/v1/shill/campaigns`` (LIVE).

    Fallback for the rugbrain ``/rag/shill-campaigns`` rail. Returns
    campaign-shaped dicts the shills lane can merge. Never raises.
    """
    body = await backend_get_json(
        "/api/v1/shill/campaigns",
        params={"limit": max(1, min(limit, 50))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    campaigns: list[dict[str, Any]] = []
    for item in _as_list(body.get("campaigns"))[:limit]:
        token = str(item.get("token") or item.get("key") or "").strip()
        if not token:
            continue
        accounts = [str(a) for a in _as_list(item.get("accounts"))]
        campaigns.append(
            {
                "token": token,
                "chain": str(item.get("chain") or "").strip(),
                "accounts": accounts,
                "accounts_count": int(item.get("accounts_count") or len(accounts)),
                "mentions_count": int(item.get("mentions_count") or 0),
                "score": item.get("score"),
                "evidence": _as_list(item.get("evidence")),
            }
        )
    return campaigns

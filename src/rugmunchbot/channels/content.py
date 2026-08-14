"""Channel content fetchers — real backend/on-chain sources, fail-open.

Every fetch returns a normalized list of dicts (or ``[]``) and never raises.
Sources are the bot's own backend endpoints (``data_resolver.backend_get_json``
with the shared-secret header) plus the alerts-domain whale collector.

Sources per channel
-------------------
* news   → ``GET /api/v1/news/trending?window_hours=168&limit=N``
* alpha  → whale moves: ``rugmunchbot.integrations.whale_watch.collect_whale_alerts``
           (helius WhaleWatcher; real on-chain large transfers once its alert
           history is populated by a monitor loop — fail-open ``[]`` today);
           smart money: ``GET /api/v1/databus/premium/smart-money`` (the same
           feed the ``smart_money`` tool uses); launches:
           ``GET /api/v1/launches/feed``.
* alerts → ``GET /api/v1/launches/alerts`` (high-risk launch alerts with
           honeypot/rug/liquidity risk flags).

Nothing here is synthesized: every value comes from the listed live source.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.data_resolver import backend_get_json
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.content")

_NEWS_WINDOW_HOURS = 168


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a JSON value into a list of dicts (fail-open)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


# ── News ──────────────────────────────────────────────────────────────────


async def fetch_trending_news(limit: int = 5) -> list[dict[str, Any]]:
    """Curated trending headlines from our backend news feed."""
    body = await backend_get_json(
        "/api/v1/news/trending",
        params={"window_hours": _NEWS_WINDOW_HOURS, "limit": max(1, min(limit, 20))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    items = _as_list(body.get("items") or body.get("articles"))
    return [
        {
            "title": str(item.get("title") or "").strip(),
            "summary": str(item.get("summary") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "source": str(item.get("source") or "unknown").strip(),
            "sentiment_label": str(item.get("sentiment_label") or "").strip(),
            "factual_rating": str(item.get("factual_rating") or "").strip(),
        }
        for item in items[:limit]
        if str(item.get("title") or "").strip()
    ]


# ── Alpha: whale moves + smart money + launches ───────────────────────────


async def _collect_whale_watch_alerts(limit: int) -> list[dict[str, Any]]:
    """Pull whale moves from the alerts-domain collector (real on-chain).

    Uses ``rugmunchbot.integrations.whale_watch.collect_whale_alerts`` — the helius
    WhaleWatcher path. Returns ``[]`` when no monitor loop has populated the
    watcher's alert history (fail-open by design; see whale_watch.py).
    """
    try:
        from rugmunchbot.integrations.whale_watch import collect_whale_alerts

        alerts = collect_whale_alerts()
    except Exception as exc:  # fail-open by contract
        logger.debug("channels:whale_watch_collect_failed err=%s", str(exc)[:120])
        return []
    moves: list[dict[str, Any]] = []
    for alert in alerts[:limit]:
        try:
            payload = alert.model_dump(mode="json")
        except Exception:
            to_dict = getattr(alert, "to_dict", None)
            payload = to_dict() if callable(to_dict) else {}
        moves.append(
            {
                "token_address": str(payload.get("token_address") or "").strip(),
                "token_symbol": str(payload.get("token_symbol") or "").strip(),
                "title": str(payload.get("title") or "Whale move").strip(),
                "body": str(payload.get("body") or "").strip(),
                "chain": str(payload.get("chain") or "solana").strip(),
                "source": str(payload.get("source") or "whale_watcher").strip(),
            }
        )
    return moves


async def _fetch_smart_money(chain: str, limit: int) -> list[dict[str, Any]]:
    """Smart-money wallets from the backend premium feed (real)."""
    body = await backend_get_json(
        "/api/v1/databus/premium/smart-money",
        params={"chain": chain},
        timeout_secs=8.0,
    )
    if not body:
        return []
    wallets = body.get("smart_money_wallets")
    if not isinstance(wallets, list):
        inner = body.get("data")
        wallets = inner.get("smart_money_wallets") if isinstance(inner, dict) else None
    if not isinstance(wallets, list):
        return []
    return [
        {
            "address": str(w.get("address") or "").strip(),
            "label": str(w.get("label") or w.get("name") or "").strip(),
            "tags": w.get("tags") if isinstance(w.get("tags"), list) else [],
            "pnl_usd": w.get("pnl_usd"),
            "realized_pnl": w.get("realized_pnl"),
            "chain": chain,
        }
        for w in wallets[:limit]
        if isinstance(w, dict) and str(w.get("address") or "").strip()
    ]


async def _fetch_launches(chain: str, limit: int) -> list[dict[str, Any]]:
    """Recent token launches from the backend launch feed (real)."""
    body = await backend_get_json(
        "/api/v1/launches/feed",
        params={"chain": chain, "limit": max(1, min(limit, 50))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    launches = _as_list(body.get("launches"))
    return [
        {
            "address": str(item.get("address") or "").strip(),
            "name": str(item.get("name") or "Unknown").strip(),
            "symbol": str(item.get("symbol") or "???").strip(),
            "chain": str(item.get("chain") or chain).strip(),
            "risk_level": str(item.get("risk_level") or "unknown").strip(),
            "age_hours": item.get("age_hours"),
            "liquidity_usd": item.get("liquidity_usd") or item.get("liquidity"),
            "holders": item.get("holders"),
        }
        for item in launches[:limit]
        if str(item.get("address") or "").strip()
    ]


async def fetch_alpha_content(
    *,
    chain: str = "solana",
    whale_limit: int = 3,
    smart_limit: int = 5,
    launch_limit: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Assemble the alpha-channel content: whale moves + smart money + launches.

    Returns ``{"whale_moves": [...], "smart_money": [...], "launches": [...]}``
    — each a list of normalized dicts, empty when the source has no data.
    Never raises.
    """
    whale_moves = await _collect_whale_watch_alerts(whale_limit)
    smart_money = await _fetch_smart_money(chain, smart_limit)
    launches = await _fetch_launches(chain, launch_limit)
    return {"whale_moves": whale_moves, "smart_money": smart_money, "launches": launches}


# ── Alerts: scanner risk digest ───────────────────────────────────────────


async def fetch_risk_alerts(limit: int = 5) -> list[dict[str, Any]]:
    """Recent high-risk scanner alerts (honeypot/rug/liquidity flags).

    Real source: the backend launch-alerts rail (tokens auto-scanned above
    70/100 risk with their flags). The event-driven ``alert_hooks`` rail
    already posts instantly on high-risk scans; this digest keeps the alerts
    channel active on a regular cadence.
    """
    body = await backend_get_json(
        "/api/v1/launches/alerts",
        params={"limit": max(1, min(limit, 20))},
        timeout_secs=8.0,
    )
    if not body:
        return []
    alerts = _as_list(body.get("alerts"))
    normalized: list[dict[str, Any]] = []
    for item in alerts[:limit]:
        security = item.get("security")
        raw_flags = item.get("risk_flags")
        flags = [str(f) for f in raw_flags] if isinstance(raw_flags, list) else []
        if isinstance(security, dict):
            for k, v in security.items():
                if v is True and k not in {str(f).lower() for f in flags}:
                    flags.append(k)
        normalized.append(
            {
                "address": str(item.get("address") or "").strip(),
                "name": str(item.get("name") or "Unknown").strip(),
                "symbol": str(item.get("symbol") or "???").strip(),
                "chain": str(item.get("chain") or "").strip(),
                "risk_level": str(item.get("risk_level") or "unknown").strip(),
                "risk_score": item.get("risk_score"),
                "risk_flags": [str(f) for f in flags][:6],
                "title": str(item.get("title") or "").strip(),
            }
        )
    return [a for a in normalized if a["address"]]


__all__ = [
    "fetch_alpha_content",
    "fetch_risk_alerts",
    "fetch_trending_news",
]

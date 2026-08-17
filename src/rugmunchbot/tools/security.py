"""security_scan + wallet_analysis tools — REAL scanner / wallet data.

Sources (own-data first, all backend):
  * ``POST /api/v1/bot/scan`` — scanner risk scores (Redis-cached, fast).
  * ``GET /api/v1/databus/wallet/{address}/tokens`` — wallet holdings.
  * ``GET /api/v1/databus/wallet/{address}/pnl`` — wallet profit/loss.
  * ``POST /api/v2/scanner/wallet/scan`` — v2 wallet forensics (clusters,
    scam exposure, smart-money overlap).

The wallet tools use the REAL convenience routes — never the bare
``/api/v1/databus/wallet/{address}`` route (that 404s).
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get, backend_json_post

logger = get_logger("rmi_bot.tools.security")


async def security_scan(*, address: str = "", chain: str = "") -> ToolResult:
    """Real scanner risk payload for a token address."""
    if not address:
        return ToolResult(tool="security_scan", ok=False, error="address required")
    body = await backend_json_post(
        "/api/v1/bot/scan",
        json={"address": address, "chain": chain or ""},
        timeout=8.0,
    )
    if not body or body.get("error"):
        return ToolResult(tool="security_scan", ok=False, error="no live data (scanner unavailable)")
    # Flatten the compact scanner payload so the LLM formats real numbers.
    keep = {
        "address": address,
        "risk_score": body.get("risk_score"),
        "risk_level": body.get("risk_level") or body.get("verdict"),
        "liquidity_usd": body.get("liquidity_usd"),
        "price_usd": body.get("price_usd"),
        "volume_24h": body.get("volume_24h"),
        "holders": body.get("holders"),
        "is_honeypot": body.get("is_honeypot"),
        "is_mintable": body.get("is_mintable"),
        "deployer_funded_by": body.get("deployer_funded_by"),
        "flags": body.get("flags") or body.get("risk_flags"),
        "cached": bool(body.get("cached")),
        "scan_time": body.get("scan_time") or body.get("timestamp"),
    }
    return ToolResult(
        tool="security_scan",
        ok=True,
        data=keep,
        symbols=[(body.get("symbol") or "")] if body.get("symbol") else [],
        addresses=[address],
        source="backend:/api/v1/bot/scan",
    )


async def wallet_analysis(*, address: str = "", chain: str = "") -> ToolResult:
    """Real wallet holdings + P&L via the correct convenience routes."""
    if not address:
        return ToolResult(tool="wallet_analysis", ok=False, error="address required")

    tokens_body = await backend_json_get(f"/api/v1/databus/wallet/{address}/tokens", timeout=8.0)
    pnl_body = await backend_json_get(f"/api/v1/databus/wallet/{address}/pnl", timeout=8.0)
    v2_body = await backend_json_post(
        "/api/v2/scanner/wallet/scan",
        json={"address": address, "chain": chain or ""},
        timeout=20.0,
    )

    payload: dict[str, Any] = {"address": address, "chain": chain or ""}
    if tokens_body is not None:
        payload["tokens"] = (
            tokens_body.get("tokens") if isinstance(tokens_body.get("tokens"), list) else tokens_body.get("data")
        )
    if pnl_body is not None:
        payload["pnl"] = pnl_body.get("pnl") if pnl_body.get("pnl") is not None else pnl_body.get("data")
    if v2_body and v2_body.get("status") == "ok":
        payload["v2"] = v2_body

    # Collect token symbols/addresses from holdings for the guardrail.
    symbols: list[str] = []
    token_addrs: list[str] = []
    holdings = payload.get("tokens")
    if isinstance(holdings, list):
        for t in holdings:
            if not isinstance(t, dict):
                continue
            if t.get("symbol"):
                symbols.append(str(t["symbol"]))
            if t.get("address") or t.get("mint") or t.get("token_address"):
                token_addrs.append(str(t.get("address") or t.get("mint") or t.get("token_address")))

    if not any(k in payload for k in ("tokens", "pnl", "v2")):
        return ToolResult(
            tool="wallet_analysis",
            ok=False,
            error="no live wallet data (backend wallet routes unavailable)",
            addresses=[address],
        )
    return ToolResult(
        tool="wallet_analysis",
        ok=True,
        data=payload,
        symbols=list(dict.fromkeys(symbols)),
        addresses=list(dict.fromkeys([address, *token_addrs])),
        source="backend:/api/v1/databus/wallet/{address}/tokens+/pnl",
    )

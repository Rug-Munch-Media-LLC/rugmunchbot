# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/gmgn_client.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
GMGN CLI Wrapper — Rug Munch Media
===================================
Python wrapper around `npx gmgn-cli` for agent-friendly access.
Provides typed access to GMGN smart money tracking, KOL monitoring,
token security, market signals, and trending data.

Supported chains: sol (Solana), bsc (BSC), base (Base), eth (coming)
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from typing import Any, cast

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
GMGN_ENV = {"GMGN_API_KEY": GMGN_API_KEY, "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin")}


def _gmgn(args: list[str], timeout: int = 15) -> dict[str, Any]:
    """Call npx gmgn-cli and return parsed JSON."""
    cmd = ["npx", "gmgn-cli", *args]
    try:
        # Fixed command list, no shell=True; args are internal CLI flags (no user input)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=GMGN_ENV, shell=False)
        if result.returncode != 0:
            logger.error(f"gmgn-cli failed: {result.stderr[:200]}")
            return {"error": result.stderr[:200]}
        return cast("dict[str, Any]", json.loads(result.stdout))
    except subprocess.TimeoutExpired:
        return {"error": "gmgn-cli timed out"}
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON: {result.stdout[:200]}"}
    except (KeyError, TypeError) as e:
        logger.debug("_gmgn_error", exc_info=True)
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════
# SMART MONEY TRACKING
# ══════════════════════════════════════════════════════════════════════


def get_smart_money_trades(chain: str = "sol", limit: int = 20) -> list[dict[str, Any]]:
    """Get recent smart money trades. Returns list of trades with wallet tags."""
    result = _gmgn(["track", "smartmoney", "--chain", chain, "--limit", str(limit)])
    trades = result.get("list", []) if isinstance(result, dict) else []
    return [
        {
            "tx_hash": t.get("transaction_hash", ""),
            "wallet": t.get("maker", ""),
            "token": t.get("base_token", {}).get("symbol", "?"),
            "token_address": t.get("base_address", ""),
            "amount_usd": t.get("amount_usd", 0),
            "price_usd": t.get("price_usd", 0),
            "side": t.get("side", "?"),
            "timestamp": t.get("timestamp", 0),
            "tags": t.get("maker_info", {}).get("tags", []),
            "maker_name": t.get("maker_info", {}).get("name", ""),
            "chain": chain,
        }
        for t in trades
    ]


def get_kol_trades(chain: str = "sol", limit: int = 20) -> list[dict[str, Any]]:
    """Get recent KOL/influencer trades."""
    result = _gmgn(["track", "kol", "--chain", chain, "--limit", str(limit)])
    trades = result.get("list", []) if isinstance(result, dict) else []
    return [
        {
            "tx_hash": t.get("transaction_hash", ""),
            "wallet": t.get("maker", ""),
            "token": t.get("base_token", {}).get("symbol", "?"),
            "token_address": t.get("base_address", ""),
            "amount_usd": t.get("amount_usd", 0),
            "side": t.get("side", "?"),
            "timestamp": t.get("timestamp", 0),
            "kol_name": t.get("maker_info", {}).get("name", ""),
            "twitter": t.get("maker_info", {}).get("twitter_username", ""),
            "chain": chain,
        }
        for t in trades
    ]


# ══════════════════════════════════════════════════════════════════════
# MARKET SIGNALS
# ══════════════════════════════════════════════════════════════════════


def get_market_signals(chain: str = "sol", groups: str = "smart-money", limit: int = 20) -> list[dict[str, Any]]:
    """Get market signals: smart money buys, price spikes, large buys."""
    result = _gmgn(["market", "signal", "--chain", chain, "--groups", groups, "--limit", str(limit)])
    signals = result if isinstance(result, list) else result.get("list", [])
    return [
        {
            "token": s.get("base_token", {}).get("symbol", "?") if s.get("base_token") else "?",
            "token_address": s.get("base_address", ""),
            "signal_type": s.get("signal_type", "?"),
            "description": s.get("description", ""),
            "timestamp": s.get("timestamp", 0),
            "chain": chain,
        }
        for s in signals
    ]


# ══════════════════════════════════════════════════════════════════════
# TOKEN SECURITY
# ══════════════════════════════════════════════════════════════════════


def get_token_security(address: str, chain: str = "sol") -> dict[str, Any]:
    """Get GMGN token security audit."""
    return _gmgn(["token", "security", "--address", address, "--chain", chain])


def get_token_info(address: str, chain: str = "sol") -> dict[str, Any]:
    """Get token basic info and price."""
    return _gmgn(["token", "info", "--address", address, "--chain", chain])


def get_token_holders(address: str, chain: str = "sol", limit: int = 20) -> list[dict[str, Any]]:
    """Get top token holders."""
    result = _gmgn(["token", "holders", "--address", address, "--chain", chain, "--limit", str(limit)])
    return cast("list[dict[str, Any]]", result.get("list", [])) if isinstance(result, dict) else []


def get_top_traders(address: str, chain: str = "sol", limit: int = 20) -> list[dict[str, Any]]:
    """Get top traders for a token."""
    result = _gmgn(["token", "traders", "--address", address, "--chain", chain, "--limit", str(limit)])
    return cast("list[dict[str, Any]]", result.get("list", [])) if isinstance(result, dict) else []


# ══════════════════════════════════════════════════════════════════════
# TRENDING & MARKET
# ══════════════════════════════════════════════════════════════════════


def get_trending(chain: str = "sol", interval: str = "1h", limit: int = 20) -> list[dict[str, Any]]:
    """Get trending tokens."""
    result = _gmgn(["market", "trending", "--chain", chain, "--interval", interval, "--limit", str(limit)])
    return cast("list[dict[str, Any]]", result.get("list", [])) if isinstance(result, dict) else []


def get_kline(address: str, chain: str = "sol", resolution: str = "1h", limit: int = 100) -> list[dict[str, Any]]:
    """Get candlestick data."""
    result = _gmgn(
        [
            "market",
            "kline",
            "--address",
            address,
            "--chain",
            chain,
            "--resolution",
            resolution,
            "--limit",
            str(limit),
        ]
    )
    return cast("list[dict[str, Any]]", result.get("list", [])) if isinstance(result, dict) else []


def get_trenches(chain: str = "sol", status: str = "new", limit: int = 20) -> list[dict[str, Any]]:
    """Get new token launches (Trenches). status: new, near, completed."""
    result = _gmgn(["market", "trenches", "--chain", chain, "--status", status, "--limit", str(limit)])
    return cast("list[dict[str, Any]]", result.get("list", [])) if isinstance(result, dict) else []


def get_hot_searches(chain: str = "sol") -> list[dict[str, Any]]:
    """Get most-searched tokens."""
    result = _gmgn(["market", "hot-searches", "--chain", chain])
    return cast("list[dict[str, Any]]", result.get("list", [])) if isinstance(result, dict) else []


# ══════════════════════════════════════════════════════════════════════
# PORTFOLIO
# ══════════════════════════════════════════════════════════════════════


def get_portfolio(address: str, chain: str = "sol") -> dict[str, Any]:
    """Get wallet portfolio holdings."""
    return _gmgn(["portfolio", "holdings", "--address", address, "--chain", chain])


def get_wallet_trades(address: str, chain: str = "sol", limit: int = 50) -> list[dict[str, Any]]:
    """Get wallet trade history."""
    result = _gmgn(["portfolio", "trades", "--address", address, "--chain", chain, "--limit", str(limit)])
    return cast("list[dict[str, Any]]", result.get("list", [])) if isinstance(result, dict) else []


# ══════════════════════════════════════════════════════════════════════
# AGGREGATED INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════


def get_wallet_tags(wallet: str, chain: str = "sol") -> list[str]:
    """Get GMGN tags for a wallet (smart_degen, photon, axiom, etc)."""
    trades = get_smart_money_trades(chain, limit=50)
    for t in trades:
        if t["wallet"].lower() == wallet.lower():
            return cast("list[str]", t.get("tags", []))
    return []


def get_all_signals(chain: str = "sol") -> dict[str, Any]:
    """Get all market signals aggregated."""
    return {
        "smart_money": get_smart_money_trades(chain, limit=10),
        "kol": get_kol_trades(chain, limit=10),
        "signals": get_market_signals(chain, limit=10),
        "trending": get_trending(chain, limit=10),
        "new_launches": get_trenches(chain, limit=10),
        "hot_searches": get_hot_searches(chain),
        "fetched_at": datetime.now(UTC).isoformat(),
    }


logger.info("GMGN CLI wrapper loaded")

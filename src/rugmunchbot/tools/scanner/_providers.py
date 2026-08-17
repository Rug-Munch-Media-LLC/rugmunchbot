"""Scanner engine — external security/market providers + coercion helpers.

The EVM security chain (GoPlus -> honeypot.is fallback), the RMI databus
bundle/wallet provider, null-safe numeric coercion (``_safe_float``) and
the chain auto-detect used across the scanner engine. Every provider is
wrapped as ONE coroutine so the fallback ordering is preserved exactly
while the chain runs concurrently with the other independent providers.
Each request is capped and fails open into ``result['errors']`` — a
slow/blocked provider degrades, never hangs.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, GOPLUS, HONEYPOT, backend_headers
from app.domains.telegram.rugmunchbot.formatters import is_evm
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.scanner")


async def _scan_evm_security_provider(client: httpx.AsyncClient, address: str, result: dict[str, Any]) -> None:
    """EVM security chain: GoPlus Security, then honeypot.is fallback.

    Wrapped as ONE coroutine so the GoPlus → honeypot.is fallback ordering is
    preserved exactly (honeypot.is only runs when GoPlus had no honeypot
    answer) while the chain runs concurrently with the other independent
    providers. Each request is capped at 8s and fails open into
    ``result['errors']`` — a slow/blocked provider degrades, never hangs.
    """
    if not is_evm(address):
        return
    # GoPlus Security
    try:
        chain_id_map = {
            "ethereum": "1",
            "bsc": "56",
            "polygon": "137",
            "arbitrum": "42161",
            "avalanche": "43114",
            "base": "8453",
            "optimism": "10",
            "fantom": "250",
        }
        cid = chain_id_map.get(result["chain"].lower(), "1")
        r = await client.get(f"{GOPLUS}/token_security/{cid}?contract_addresses={address}", timeout=8.0)
        if r.status_code == 200:
            sec = r.json().get("result", {}).get(address.lower(), {})
            if sec:
                result["contract_verified"] = sec.get("is_open_source") == "1"
                result["honeypot"] = sec.get("is_honeypot") == "1"
                result["buy_tax"] = _safe_float(sec.get("buy_tax"))
                result["sell_tax"] = _safe_float(sec.get("sell_tax"))
                result["mintable"] = sec.get("is_mintable") == "1"
                result["can_blacklist"] = sec.get("is_blacklisted") == "1" or sec.get("can_take_back_ownership") == "1"
                result["owner_renounced"] = sec.get("is_owner_renounced") != "0"
                lp_locks = sec.get("lp_holders", [])
                locked_pct = sum(_safe_float(h.get("percent")) for h in lp_locks if h.get("is_locked") == 1)
                result["lp_locked"] = locked_pct
                holders_data = sec.get("holders", [])
                result["holders"] = len(holders_data) if holders_data else 0
                for h in holders_data[:10]:
                    result["top_holders"].append(
                        {
                            "address": h.get("address", ""),
                            "pct": _safe_float(h.get("percent")) * 100,
                            "is_contract": h.get("is_contract") == 1,
                            "tag": h.get("tag", ""),
                        }
                    )
                if result["honeypot"]:
                    result["threats"].append("HONEYPOT DETECTED")
                    result["risk_score"] += 40
                if not result["contract_verified"]:
                    result["threats"].append("Contract not verified")
                    result["risk_score"] += 15
                if result["mintable"]:
                    result["threats"].append("Mintable supply")
                    result["risk_score"] += 20
                if result["can_blacklist"]:
                    result["threats"].append("Owner can blacklist")
                    result["risk_score"] += 15
                if not result["owner_renounced"]:
                    result["threats"].append("Ownership not renounced")
                    result["risk_score"] += 10
                if result["buy_tax"] and result["buy_tax"] > 0.1:
                    result["threats"].append(f"High buy tax: {result['buy_tax'] * 100:.1f}%")
                    result["risk_score"] += 10
                if result["sell_tax"] and result["sell_tax"] > 0.1:
                    result["threats"].append(f"High sell tax: {result['sell_tax'] * 100:.1f}%")
                    result["risk_score"] += 15
                if locked_pct < 0.5 and result["mcap"] > 10000:
                    result["threats"].append(f"Low LP lock: {locked_pct * 100:.0f}%")
                    result["risk_score"] += 15
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        result["errors"].append(f"GoPlus: {e}")

    # Honeypot.is (EVM fallback)
    if result["honeypot"] is None:
        try:
            r = await client.get(f"{HONEYPOT}/IsHoneypot?address={address}", timeout=8.0)
            if r.status_code == 200:
                hp = r.json()
                result["honeypot"] = hp.get("isHoneypot", False)
                if result["honeypot"]:
                    result["threats"].append("HONEYPOT (honeypot.is)")
                    result["risk_score"] += 40
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            result["errors"].append(f"Honeypot.is: {e}")


async def _scan_rmi_provider(client: httpx.AsyncClient, address: str, result: dict[str, Any]) -> None:
    """RMI databus provider: bundle detection + fresh-wallet ratio + dev wallet.

    Fail-open into ``result['errors']``; 8s cap. Runs concurrently with the
    EVM security chain inside ``scan_token``.
    """
    try:
        r = await client.get(f"{BACKEND_URL}/api/v1/databus/token/{address}", headers=backend_headers(), timeout=8.0)
        if r.status_code == 200:
            rmi = r.json()
            if rmi.get("bundle_detected"):
                result["bundle_detected"] = True
                result["threats"].append("Bundle activity detected")
                result["risk_score"] += 20
            if rmi.get("fresh_wallet_pct"):
                result["fresh_wallet_pct"] = rmi["fresh_wallet_pct"]
                if rmi["fresh_wallet_pct"] > 50:
                    result["threats"].append(f"High fresh wallet ratio: {rmi['fresh_wallet_pct']:.0f}%")
                    result["risk_score"] += 10
            if rmi.get("dev_wallet"):
                result["dev_wallet"] = rmi["dev_wallet"]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        result["errors"].append(f"RMI Backend: {e}")


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce a value to float; null/unparseable values never crash a scan.

    DexScreener / GoPlus / Solana.fm payloads carry nulls (``priceUsd: null``
    on delisted pairs) and occasionally garbage strings; ``float(None)``
    raises ``TypeError`` and would kill the whole scan reply. Guarded exactly
    as the audit asks: ``try/except (TypeError, ValueError): 0.0``.
    """
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _detect_chain(address: str) -> str:
    """0x prefix → ethereum, otherwise solana (mirrors backend auto-detect)."""
    addr = (address or "").strip()
    if addr.startswith(("0x", "0X")):
        return "ethereum"
    return "solana"

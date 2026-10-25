"""_local_v2.py split part: _to_goplus_checks, _local_token_security, _local_holder_data, _local_volume_authenticity, _local_cross_chain_entity, _databus_fetch."""
from __future__ import annotations
from typing import Any
import asyncio
from .localv2_p1 import _ENGINE_TIMEOUT_S, _MISS, _local_token_metadata, _local_token_price, logger  # noqa: F401

def _to_goplus_checks(sec: dict[str, Any]) -> dict[str, Any]:
    """Map the flat own-security payload into GoPlus-style ``checks``.

    The vendored scoring reads ``security.checks`` (GoPlus shape); the local
    ``own_security_analysis`` engine returns flat booleans. Convert the
    engine's own flags — never fabricating: ``None`` stays absent.
    """
    checks: dict[str, Any] = {}
    if sec.get("is_honeypot") is True:
        checks["HP01"] = {"status": "fail", "details": "sell-side honeypot signal (own security)"}
    elif sec.get("is_honeypot") is False:
        checks["HP01"] = {"status": "pass", "details": "no honeypot signal"}
    if sec.get("is_mintable") is True:
        checks["MINT"] = {"status": "fail", "details": "mint authority active"}
    elif sec.get("is_mintable") is False:
        checks["MINT"] = {"status": "pass", "details": "mint authority revoked"}
    if sec.get("ownership_renounced") is False:
        checks["OWNER"] = {"status": "fail", "details": "ownership not renounced"}
    elif sec.get("ownership_renounced") is True:
        checks["OWNER"] = {"status": "pass", "details": "ownership renounced"}
    if sec.get("is_proxy") is True:
        checks["PROXY"] = {"status": "fail", "details": "upgradeable proxy detected"}
    elif sec.get("is_proxy") is False:
        checks["PROXY"] = {"status": "pass", "details": "not a proxy"}
    for key in ("buy_tax_pct", "sell_tax_pct"):
        tax = sec.get(key)
        if isinstance(tax, (int, float)) and tax > 10.0:
            checks[key.upper().replace("_PCT", "")] = {
                "status": "fail",
                "details": f"{key} {tax:.1f}%",
            }
    return checks

async def _local_token_security(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``token_security`` — own security analysis (GoPlus-compatible)."""
    if not token:
        return _MISS
    try:
        from rugmunch_scanner.unified.engines.own_security_analysis import get_own_security

        sec = await asyncio.wait_for(get_own_security(token, chain), timeout=_ENGINE_TIMEOUT_S)
    except Exception as exc:
        logger.debug("local_security_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
        return _MISS
    if not isinstance(sec, dict) or sec.get("result") == "unavailable":
        return _MISS
    payload = dict(sec)
    payload["checks"] = _to_goplus_checks(sec)
    payload["source"] = "own"
    return {"data": payload, "source": "local:own_security", "tier": "free", "is_local": True}

async def _local_holder_data(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``holder_data`` — own holder analysis (ClickHouse evm_transfers)."""
    if not token:
        return _MISS
    try:
        from rugmunch_scanner.unified.engines.own_holder_analysis import (
            get_own_holder_analysis,
        )

        data = await asyncio.wait_for(
            get_own_holder_analysis(token, chain), timeout=_ENGINE_TIMEOUT_S
        )
    except Exception as exc:
        logger.debug("local_holders_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
        return _MISS
    if not isinstance(data, dict) or data.get("result") == "unavailable":
        return _MISS
    return {"data": data, "source": "local:own_holder_analysis", "tier": "free", "is_local": True}

async def _local_volume_authenticity(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``volume_authenticity`` — own wash-trading score engine."""
    if not token:
        return _MISS
    try:
        from rugmunch_scanner.unified.engines.own_volume_authenticity import (
            get_own_volume_authenticity,
        )

        data = await asyncio.wait_for(
            get_own_volume_authenticity(token, chain), timeout=_ENGINE_TIMEOUT_S
        )
    except Exception as exc:
        logger.debug("local_volume_auth_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
        return _MISS
    if not isinstance(data, dict) or data.get("result") == "unavailable":
        return _MISS
    return {
        "data": data,
        "source": "local:own_volume_authenticity",
        "tier": "free",
        "is_local": True,
    }

async def _local_cross_chain_entity(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``cross_chain_entity`` — own cross-chain entity resolution."""
    if not token:
        return _MISS
    try:
        from rugmunch_scanner.unified.engines.cross_chain_entity import (
            resolve_cross_chain_entity,
        )

        data = await asyncio.wait_for(
            resolve_cross_chain_entity(token, chain), timeout=_ENGINE_TIMEOUT_S
        )
    except Exception as exc:
        logger.debug("local_cross_chain_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
        return _MISS
    if not isinstance(data, dict) or data.get("result") == "unavailable":
        return _MISS
    return {"data": data, "source": "local:cross_chain_entity", "tier": "free", "is_local": True}

async def _databus_fetch(
    chain_name: str,
    admin_key: str | None = None,
    **params: Any,
) -> dict[str, Any]:
    """Local ``app.databus.fetch`` — dispatch DataBus chains to vendored engines.

    Mirrors the backend DataBus envelope (``{"data": ..., "source": ...,
    "tier": ...}``) so the vendored ``_fetch_batch`` unwraps it unchanged.
    Unknown chains return the same error-miss the backend DataBus returns
    for unregistered types. Never raises.
    """
    token = str(
        params.get("address") or params.get("token_address") or params.get("mint") or ""
    ).strip()
    chain = str(params.get("chain") or "solana").strip()
    try:
        if chain_name == "token_price":
            return await _local_token_price(token, chain)
        if chain_name == "token_metadata":
            return await _local_token_metadata(token, chain)
        if chain_name == "token_security":
            return await _local_token_security(token, chain)
        if chain_name == "holder_data":
            return await _local_holder_data(token, chain)
        if chain_name == "volume_authenticity":
            return await _local_volume_authenticity(token, chain)
        if chain_name == "cross_chain_entity":
            return await _local_cross_chain_entity(token, chain)
        # lite_chain_indexer_patterns / token_launches / premium chains have
        # no vendored local engine — honest miss (the caller treats it as
        # "no data", never a false penalty).
    except Exception as exc:
        logger.debug("local_databus_fetch_failed chain=%s err=%s", chain_name, str(exc)[:100])
    return _MISS

"""Scanner engine — the chat scanner tool (``scanner_intel``) + registration.

``scanner_intel`` is the REAL-data chat tool: unified v2 scan -> fast bot
scan -> DexScreener fallback, always embedding the canonical ``token``
identity block. ``register_extra_tools`` self-registers scanner_intel +
token_meta on the shared registry (import-time side effect). Patchable
names (``backend_json_post``, ``token_meta``) resolve through the package.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.config import DEXSCREENER
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools import scanner as _scanner
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult
from app.domains.telegram.rugmunchbot.tools.scanner._deployer import (
    _extract_pair_intel,
    _supplement_deployer_socials,
    _token_meta_dict,
)
from app.domains.telegram.rugmunchbot.tools.scanner._flatten import _flatten_dexscreener, _flatten_v1, _flatten_v2
from app.domains.telegram.rugmunchbot.tools.scanner._providers import _detect_chain
from app.domains.telegram.rugmunchbot.tools.scanner._scans import scan_token_v2

logger = get_logger("rmi_bot.tools.scanner")


async def scanner_intel(*, address: str = "", chain: str = "") -> ToolResult:
    """Real scanner intel for a token — OUR scanner is the primary source.

    Call order: unified v2 scan → fast bot scan → DexScreener fallback (see
    module docstring). Every payload embeds the ``token`` identity block
    (logo + name + symbol + chain + price) resolved by ``token_meta``.
    """
    if not address:
        return ToolResult(tool="scanner_intel", ok=False, error="address required")

    chain = (chain or "").strip() or _detect_chain(address)

    # 1. Own backend unified v2 scan (primary source — best-in-class scanner).
    #    Delegates to scan_token_v2 so /scan and the chat tool share ONE v2
    #    engine — one client, one payload shape (unified 2026-08-15).
    body = await scan_token_v2(address, chain)
    # Gate: the unified scanner returns ``status: ok`` even for addresses with
    # no on-chain token (empty name/symbol, zero price, bogus flags). Require
    # real token identity before treating it as a genuine scan result, else we
    # report fabricated "medium risk" for a token that does not exist.
    if isinstance(body, dict) and (body.get("name") or body.get("symbol")):
        intel = _flatten_v2(body, address, chain)
        # Deployer + socials: free-tier v2 scans skip the backend deployer
        # module, so backfill from DexScreener pair data (fail-open, only
        # when the flatten left either empty).
        intel = await _supplement_deployer_socials(intel, address, chain)
        meta = await _token_meta_dict(address, chain, symbol=str(body.get("symbol") or ""))
        intel["token"] = meta
        return ToolResult(
            tool="scanner_intel",
            ok=True,
            data=intel,
            symbols=[str(body.get("symbol") or meta.get("symbol") or "")]
            if (body.get("symbol") or meta.get("symbol"))
            else [],
            addresses=[address],
            source="backend:/api/v2/scanner/token/scan",
        )

    # 2. Own backend fast bot scan (Redis-cached quick scan).
    body = await _scanner.backend_json_post(
        "/api/v1/bot/scan",
        json={"address": address, "chain": chain},
        timeout=10.0,
    )
    if isinstance(body, dict) and body.get("risk_score") is not None and not body.get("error"):
        meta = await _token_meta_dict(address, chain)
        # v1 serves whatever sits in the scanner cache — including a garbage
        # entry persisted by a failed v2 attempt on a non-existent address.
        # Require real token identity (resolved name/symbol) before presenting.
        if meta.get("name") or meta.get("symbol"):
            intel = _flatten_v1(body, address, chain)
            intel["token"] = meta
            return ToolResult(
                tool="scanner_intel",
                ok=True,
                data=intel,
                symbols=[str(meta.get("symbol"))] if meta.get("symbol") else [],
                addresses=[address],
                source="backend:/api/v1/bot/scan",
            )

    # 3. DexScreener direct — external fallback when our backend is down.
    body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
    pairs = (body or {}).get("pairs") if isinstance(body, dict) else None
    if isinstance(pairs, list) and pairs:
        intel = _flatten_dexscreener(address, chain)
        # The pair payload is already in hand — extract creator + socials
        # (real data only; absent stays absent).
        creator, pair_socials = _extract_pair_intel(pairs[0])
        if creator:
            intel["deployer_address"] = creator
            intel["dev_wallet"] = creator
        if pair_socials:
            intel["socials"] = pair_socials
        meta = await _token_meta_dict(address, chain)
        intel["token"] = meta
        return ToolResult(
            tool="scanner_intel",
            ok=True,
            data=intel,
            symbols=[str(meta.get("symbol"))] if meta.get("symbol") else [],
            addresses=[address],
            source="dexscreener:/latest/dex/tokens",
        )

    return ToolResult(
        tool="scanner_intel",
        ok=False,
        error="no live scanner data (backend + DexScreener unavailable)",
        addresses=[address],
    )


def register_extra_tools() -> None:
    """Register scanner_intel + token_meta on the shared tool registry.

    Called on import (self-registration) so the tools package wires them in
    with a single additive import line in ``tools/__init__.py``.
    """
    from app.domains.telegram.rugmunchbot.tools import registry

    registry.register("scanner_intel", scanner_intel, timeout=35.0, ttl=120.0)
    registry.register("token_meta", _scanner.token_meta, timeout=12.0, ttl=300.0)

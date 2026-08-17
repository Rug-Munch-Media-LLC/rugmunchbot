"""Scanner engine — deployer address + socials resolution (fail-open).

The free-tier v2 scan does NOT run the backend deployer module, so the
report used to render "Deployer: Address: unknown" and "NO SOCIALS" even
when DexScreener / Birdeye / the SENTINEL metadata fingerprint carried the
data. These helpers resolve both from the real sources, fail-open, and
never fabricate: no address -> still "unknown", no links -> still "NO
SOCIALS". ``_token_meta_dict`` routes ``token_meta`` through the package
so tests can patch ``tools.scanner.token_meta`` (monkeypatch contract).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.domains.telegram.rugmunchbot.config import DEXSCREENER
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools import scanner as _scanner
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.scanner._market import _ds_market_snapshot, _merge_market_snapshot
from app.domains.telegram.rugmunchbot.tools.scanner._providers import _detect_chain

logger = get_logger("rmi_bot.tools.scanner")

# ── Deployer + socials resolution (scan report Deployer / Socials blocks) ──
# The free-tier v2 scan does NOT run the backend deployer module, so the
# report used to render "Deployer: Address: unknown" and "NO SOCIALS" even
# when DexScreener / Birdeye / the SENTINEL metadata fingerprint carried the
# data. These helpers resolve both from the real sources, fail-open, and
# never fabricate: no address → still "unknown", no links → still "NO SOCIALS".

#: Deployer address sentinel values that mean "we genuinely don't know".
_EMPTY_ADDR = {"", "unknown", "null", "none", "0x", "0x0"}


def _extract_pair_intel(pair: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Extract ``(creator_address, socials)`` from one DexScreener pair.

    Creator: ``creatorAddress`` (canonical DexScreener field, same one the
    backend SENTINEL resolver reads) then ``creator``. Socials: the pair
    ``info.socials`` list (twitter/telegram/…) plus ``info.websites`` mapped
    to the ``website`` key. Also accepts the ``baseToken.info`` nesting some
    payloads use. Real data only — every value is stripped and validated.
    """
    creator = ""
    raw_creator = pair.get("creatorAddress") or pair.get("creator")
    if isinstance(raw_creator, str) and raw_creator.strip().lower() not in _EMPTY_ADDR:
        creator = raw_creator.strip()

    socials: dict[str, str] = {}

    def _merge_info(info: Any) -> None:
        if not isinstance(info, dict):
            return
        for s in info.get("socials") or []:
            if not isinstance(s, dict):
                continue
            stype = str(s.get("type") or "").strip().lower()
            surl = str(s.get("url") or "").strip()
            if stype and surl:
                socials[stype] = surl
        for w in info.get("websites") or []:
            if not isinstance(w, dict):
                continue
            wurl = str(w.get("url") or "").strip()
            if wurl:
                socials.setdefault("website", wurl)

    _merge_info(pair.get("info"))
    base = pair.get("baseToken")
    if isinstance(base, dict):
        _merge_info(base.get("info"))
    return creator, socials


async def _birdeye_creator(address: str, chain: str) -> str:
    """Best-effort Birdeye ``token_overview`` ``creator`` (Solana, key-gated).

    Returns "" when no ``BIRDEYE_API_KEY`` is configured, the payload is
    missing, or the lookup fails — never raises. Only a value that looks like
    a real address (32+ chars, not a sentinel) is returned.
    """
    key = os.getenv("BIRDEYE_API_KEY", "").strip()
    if not key:
        return ""
    try:
        body = await http_get_json(
            "https://public-api.birdeye.so/defi/token_overview",
            params={"address": address},
            headers={"X-API-KEY": key, "x-chain": chain or "solana"},
            timeout=6.0,
        )
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            creator = str(data.get("creator") or "").strip()
            if len(creator) >= 32 and creator.lower() not in _EMPTY_ADDR:
                return creator
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        logger.debug("birdeye_creator_failed", exc_info=True)
    return ""


async def _supplement_deployer_socials(
    result: dict[str, Any],
    address: str,
    chain: str = "",
) -> dict[str, Any]:
    """Fill the report's market data, deployer address + socials from real sources.

    Best-effort + fail-open (never raises). Resolution order:

      1. DexScreener pair payload — the best-pair market snapshot (liquidity,
         multi-timeframe volume/price change — see ``_ds_market_snapshot``),
         ``creatorAddress``/``creator`` and the pair ``info`` socials/websites.
      2. Birdeye ``token_overview.creator`` (Solana, only when an API key is
         set and DexScreener had no creator).

    The backend v2 evidence (``deployer_address`` / ``dev_wallet``) and the
    SENTINEL metadata-fingerprint ``creator`` are already merged by
    ``v2_token_to_legacy``/``_flatten_v2`` — this only fills what those left
    empty. Existing values are never clobbered. Mutates and returns ``result``.
    """
    has_deployer = bool(result.get("deployer_address") or result.get("dev_wallet"))
    _socials = result.get("socials")
    socials = _socials if isinstance(_socials, dict) else {}

    chain_norm = chain or str(result.get("chain") or "") or _detect_chain(address)
    creator = ""
    pair_socials: dict[str, str] = {}

    try:
        body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=8.0)
        pairs = (body or {}).get("pairs") if isinstance(body, dict) else None
        if isinstance(pairs, list) and pairs:
            # Market backfill ALWAYS runs (the report's Market Data block must
            # show real liquidity / timeframe volume / price change even when
            # the v2 backend already resolved the deployer). Uses the
            # best-pair snapshot — ``pairs[0]`` is often a pump.fun bonding
            # curve pair with zero liquidity, which is why the report showed
            # ``Liquidity: $0`` for normal tokens.
            _merge_market_snapshot(result, _ds_market_snapshot(pairs))
            p = pairs[0]
            pair_creator, pair_socials = _extract_pair_intel(p)
            if not has_deployer and pair_creator:
                creator = pair_creator
            if not result.get("chain"):
                result["chain"] = p.get("chainId") or chain_norm
            if not result.get("name") or result.get("name") in ("Unknown", "???"):
                bt = p.get("baseToken") or {}
                if not result.get("name"):
                    result["name"] = bt.get("name") or "Unknown"
                if not result.get("symbol"):
                    result["symbol"] = bt.get("symbol") or "???"
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        logger.debug("deployer_socials_dexscreener_failed", exc_info=True)

    if not creator and not has_deployer and (chain_norm or "").lower() == "solana":
        creator = await _birdeye_creator(address, chain_norm)

    creator = (creator or "").strip()
    if creator and not has_deployer:
        # Real address only — never fabricate when genuinely unavailable.
        result["deployer_address"] = creator
        if not result.get("dev_wallet"):
            result["dev_wallet"] = creator

    if pair_socials:
        merged = dict(socials)
        merged.update(pair_socials)
        result["socials"] = merged

    return result


async def _token_meta_dict(address: str, chain: str, symbol: str = "") -> dict[str, Any]:
    """Resolve the token identity block through the cached ``token_meta`` tool.

    ``scanner_intel`` previously called ``resolve_token_meta`` directly,
    bypassing the token_meta cache — every scan re-resolved identity over the
    network. Routing through the public tool reuses the Redis identity/price
    cache (600s / 60s) so repeated scans of the same token are cheap.
    """
    tr = await _scanner.token_meta(address=address, chain=chain, symbol=symbol)
    return tr.data if isinstance(tr.data, dict) else {}

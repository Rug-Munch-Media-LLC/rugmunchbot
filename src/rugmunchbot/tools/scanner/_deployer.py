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

import asyncio
import os
from typing import Any

import httpx

from rugmunchbot.config import DEXSCREENER
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools import scanner as _scanner
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.scanner._market import _ds_market_snapshot, _merge_market_snapshot
from rugmunchbot.tools.scanner._providers import _detect_chain

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


async def _birdeye_holders(address: str, chain: str) -> int | None:
    """Best-effort Birdeye ``token_overview`` holder count (key-gated).

    Fills the report's Holders line when the v2 backend / GMGN enrichment
    left it empty — real data only, ``None`` when unavailable. Never raises.
    """
    key = os.getenv("BIRDEYE_API_KEY", "").strip()
    if not key:
        return None
    try:
        body = await http_get_json(
            "https://public-api.birdeye.so/defi/token_overview",
            params={"address": address},
            headers={"X-API-KEY": key, "x-chain": chain or "solana"},
            timeout=6.0,
        )
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            raw = data.get("holder_count") or data.get("holders")
            if raw is not None:
                count = int(raw)
                if count >= 0:
                    return count
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
        TypeError,
        ValueError,
    ):
        logger.debug("birdeye_holders_failed", exc_info=True)
    return None


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

    # Leg 0 — OUR OWN INDEXER first (2026-08-25 quality push): the backend
    # DataBus aggregate (359M+ on-chain rows, 17 chains) beats external
    # sources for holders/taxes/security fields when it has the token.
    # Fail-open; never clobbers values the v2 scan already filled.
    try:
        from rugmunchbot.config import BACKEND_URL, backend_headers
        from rugmunchbot.tools._client import get_shared_client

        client = get_shared_client(8)
        r = await client.get(
            f"{BACKEND_URL}/api/v1/databus/token/{address}",
            headers=backend_headers(),
        )
        if r.status_code == 200:
            agg = r.json() if isinstance(r.json(), dict) else {}
            data = agg.get("data") if isinstance(agg.get("data"), dict) else agg
            for src_key, dst_key in (
                ("holder_count", "holders"),
                ("total_holders", "holders"),
                ("holders", "holders"),
                ("buy_tax", "buy_tax"),
                ("sell_tax", "sell_tax"),
                ("deployer", "deployer_address"),
                ("creator", "deployer_address"),
            ):
                val = data.get(src_key)
                if val is not None and not result.get(dst_key):
                    result[dst_key] = val
            if isinstance(data.get("socials"), dict) and data["socials"]:
                merged = dict(socials)
                merged.update({k: str(v) for k, v in data["socials"].items() if v})
                result["socials"] = merged
    except Exception:
        from rugmunchbot.tools.scanner._section_log import log_section_fallback

        log_section_fallback("enrich", "indexer", "dexscreener", reason="databus_miss")
        logger.debug("databus_supplement_miss %s", address[:12], exc_info=True)

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
        from rugmunchbot.tools.scanner._section_log import log_section_fallback

        log_section_fallback("market", "dexscreener", "none", reason="ds_unreachable")
        logger.debug("deployer_socials_dexscreener_failed", exc_info=True)

    if not creator and not has_deployer and (chain_norm or "").lower() == "solana":
        from rugmunchbot.tools.scanner._section_log import log_section_fallback

        log_section_fallback("deployer", "dexscreener", "birdeye", reason="no_creator")
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

    # Holders backfill (2026-08-25 quality push): the report's Holders line
    # must show a real count when any source has one — Birdeye is the
    # fail-open last leg when v2/GMGN left it empty.
    _holders = result.get("holders")
    holders = _holders if isinstance(_holders, (int, float)) else None
    if not holders:
        from rugmunchbot.tools.scanner._section_log import log_section_fallback

        log_section_fallback("holders", "indexer", "birdeye", reason="no_count")
        count = await _birdeye_holders(address, chain_norm)
        if count is not None:
            result["holders"] = count

    # ── TIER-0 indexer sections (SCAN-QUALITY-PLAN item 2, 2026-08-26) ──
    # Our own ClickHouse engines fill what the scan payload left missing:
    # the repeat-deployer track record and launch-bundle clustering.
    # Indexer-first: RPC/external chains only ever ran above; these legs
    # read OUR data. Each is time-boxed and fail-open — a slow or dead
    # ClickHouse never blocks the card, and every miss logs a section
    # fallback so completeness stays auditable.
    deployer_final = str(result.get("deployer_address") or result.get("dev_wallet") or "").strip()
    if deployer_final and not result.get("deployer_profile"):
        try:
            from rugmunchbot._vendor.scanner_engines.deployer_profiles import (
                get_deployer_profile,
            )

            profile = await asyncio.wait_for(
                get_deployer_profile(deployer_final, chain_norm), timeout=6.0
            )
            if profile:
                result["deployer_profile"] = profile
                # Formatter-facing metrics mirror (Deployer block renders
                # tokens-deployed / rugpull-rate / honeypot-rate from here).
                result.setdefault(
                    "deployer_metrics",
                    {
                        "total_tokens_deployed": profile.get("total_tokens_launched"),
                        "rugpull_rate": profile.get("rugged_pct"),
                        "honeypot_rate": profile.get("honeypot_pct"),
                    },
                )
            else:
                log_section_fallback(
                    "deployer_profile", "clickhouse", "none", reason="not_repeat_deployer"
                )
        except TimeoutError:
            log_section_fallback("deployer_profile", "clickhouse", "none", reason="timeout")
        except Exception:
            log_section_fallback("deployer_profile", "clickhouse", "none", reason="engine_error")

    if not result.get("bundle_analysis"):
        try:
            from rugmunchbot._vendor.scanner_engines.own_bundle_detect import (
                get_own_bundle_analysis,
            )

            bundle = await asyncio.wait_for(
                get_own_bundle_analysis(address.lower(), chain_norm), timeout=6.0
            )
            if bundle and bundle.get("source") == "own":
                blocks = bundle.get("bundle_blocks") or []
                result["bundle_analysis"] = {
                    "is_bundled": bool(bundle.get("is_bundled")),
                    "risk_label": bundle.get("risk_label"),
                    "confidence": bundle.get("confidence"),
                    "common_funder_score": bundle.get("common_funder_score"),
                    "launch_buyers": sum(int(b.get("buyers") or 0) for b in blocks),
                    "cluster_blocks": [b.get("block") for b in blocks[:5]],
                    "common_funders": bundle.get("common_funders"),
                }
            else:
                log_section_fallback("bundle", "clickhouse", "none", reason="no_own_data")
        except TimeoutError:
            log_section_fallback("bundle", "clickhouse", "none", reason="timeout")
        except Exception:
            log_section_fallback("bundle", "clickhouse", "none", reason="engine_error")

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

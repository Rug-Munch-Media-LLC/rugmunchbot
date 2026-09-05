"""Local unified-scanner v2 execution — run the vendored scanner in-process.

Primary scan engine for ``/scan``, ``/deepscan`` and the scans-of-interest
churn rail (2026-08-29). Eliminates the flaky HTTP hop to rmi-backend
(``localhost:8000``): the full engine is vendored under
``rugmunch_scanner.unified.unified`` (holder_data, bundle_detect,
deployer, liquidity, wallet_analysis, deep_profile) with its own-data
engines under ``rugmunch_scanner.unified.engines``.

The vendored copy was frozen from rmi-backend and still references
backend-only modules at import time — SQLAlchemy Postgres persistence
(``persistence.py``) and the DataBus registry (``scanner.py._fetch_batch``).
This module bridges that gap WITHOUT editing the frozen copy:

* backend-only modules are stubbed in ``sys.modules`` so the package
  imports and persistence degrades to a no-op (the bot has no Postgres);
* ``app.databus.fetch`` is backed by the local engines, so the scanner's
  DataBus hops stay in-process.

Public entry: :func:`scan_token_v2_local` — returns the v2 payload dict
(``status="ok"`` shape the bot's handlers / RiskReport read) or ``None``
so callers can fall back to the backend HTTP scan (fallback-only).
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.scanner.local_v2")

#: Hard budget for the in-process scan's fast path. The vendored engine
#: bounds itself to ``SCAN_HARD_TIMEOUT_S`` (25s default) anyway; this is a
#: belt-and-suspenders cap so a wedged engine can never hang the handler.
_LOCAL_SCAN_TIMEOUT_S = 40.0

#: Per-engine caps for the DataBus chain fetches (the vendored ``_fetch_batch``
#: applies its own per-batch timeout; these guard the slow free-API tiers).
_ENGINE_TIMEOUT_S = 12.0

#: DataBus error-miss payload — ``_fetch_batch`` treats any dict with an
#: ``error`` key as a miss (identical to the backend's unknown-data-type).
_MISS: dict[str, Any] = {
    "error": "unknown_data_type",
    "message": "not available in the local scanner",
}


# ═══════════════════════════════════════════════════════════════════════════
# Import bridge — stub backend-only modules before the vendored scanner loads
# ═══════════════════════════════════════════════════════════════════════════

_INSTALLED: bool = False


def _stub_module(name: str) -> Any:
    """Create (or fetch) a stub module registered in ``sys.modules``.

    Returns ``Any`` because stub modules are built dynamically (attributes
    are assigned after creation); mypy cannot know them statically.
    """
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


def install_import_bridge() -> None:
    """Register backend-only module stubs required to import the vendored scanner.

    The vendored ``unified_scanner`` was frozen from rmi-backend and imports
    modules the bot does not ship:

    * ``sqlalchemy.exc`` — ``persistence.py`` (module-level import; the bot
      has no Postgres, so persistence degrades to a no-op).
    * ``app.databus`` — ``scanner.py._fetch_batch`` (module-level import;
      backed by the local engines below).
    * ``app.models.scan`` / ``app.models.token`` — ORM models used only
      inside ``persist_result`` after a DB session is obtained (never in the
      bot — the engine stub makes ``_get_async_session`` return None).
    * ``app.core.database`` — the backend's async engine; stubbed so
      ``_get_async_session`` fails with the SQLAlchemyError the vendored
      code already catches.
    * ``app.domains.databus.providers.chains.dexscreener`` — the raw
      DexScreener provider used by the price oracle tier + deployer
      resolution; backed by a local HTTP fetch.
    * ``app.domains.scanner.shared.deployer_labeler`` — fire-and-forget
      auto-label; no-op in the bot.

    Idempotent. Safe in tests: registered only when the real modules are
    absent, and the bot never ships an ``app``/``sqlalchemy`` package.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # ── sqlalchemy (persistence.py imports sqlalchemy.exc at module scope) ──
    if "sqlalchemy" not in sys.modules:
        sqlalchemy: Any = types.ModuleType("sqlalchemy")
        exc: Any = types.ModuleType("sqlalchemy.exc")

        def _sqlalchemy_error(*_args: Any, **_kwargs: Any) -> Any:
            raise exc.SQLAlchemyError("SQLAlchemy is not available in the bot process")

        exc.SQLAlchemyError = Exception
        sqlalchemy.exc = exc
        sqlalchemy.select = _sqlalchemy_error
        sqlalchemy.text = _sqlalchemy_error
        sys.modules["sqlalchemy"] = sqlalchemy
        sys.modules["sqlalchemy.exc"] = exc

    # ── app package tree ──
    if "app" not in sys.modules:
        app = types.ModuleType("app")
        sys.modules["app"] = app

    # app.databus — the one REAL backend module: fetch() dispatches to the
    # local engines (see _databus_fetch below). The backend exposes a module
    # singleton named ``databus`` (``from app.databus import databus`` in
    # scanner._fetch_batch) — export ourselves under that name.
    databus = _stub_module("app.databus")
    databus.fetch = _databus_fetch
    databus.databus = databus

    # app.core.database — no backend engine; _get_async_session then fails
    # with SQLAlchemyError (caught by the vendored persistence) and returns
    # None, so persist_result degrades to a no-op without touching Postgres.
    _stub_module("app.core")
    db_mod = _stub_module("app.core.database")

    class _NoEngine:
        def connect(self) -> Any:
            raise db_mod.SQLAlchemyError("no backend Postgres in bot process")

    db_mod.async_session_factory = None
    db_mod.engine = _NoEngine()

    # app.models — dummy ORM classes (persist_result imports them BEFORE the
    # session check; the session is always None in the bot so they are never
    # constructed, but the import must succeed to avoid an uncaught ImportError).
    _stub_module("app.models")
    scan_mod = _stub_module("app.models.scan")
    token_mod = _stub_module("app.models.token")
    scan_mod.ScanResult = type("ScanResult", (), {})
    token_mod.Token = type("Token", (), {})

    # app.domains.databus.providers.chains.dexscreener — the raw DexScreener
    # provider (price oracle tier 2 + deployer/creator discovery).
    _stub_module("app.domains")
    _stub_module("app.domains.databus")
    _stub_module("app.domains.databus.providers")
    _stub_module("app.domains.databus.providers.chains")
    dexscreener_mod = _stub_module("app.domains.databus.providers.chains.dexscreener")
    dexscreener_mod._dexscreener_price = _local_dexscreener_price

    # app.domains.scanner.shared.deployer_labeler — fire-and-forget auto-label
    # (no-op in the bot; the vendored auto_label_deployer task would otherwise
    # die with an unhandled ImportError for high-risk tokens).
    _stub_module("app.domains.scanner")
    _stub_module("app.domains.scanner.shared")
    labeler = _stub_module("app.domains.scanner.shared.deployer_labeler")

    async def _noop_auto_label(**_: Any) -> None:
        return None

    labeler.auto_label_deployer = _noop_auto_label


# ═══════════════════════════════════════════════════════════════════════════
# Local DataBus — dispatch the scanner's DataBus chains to vendored engines
# ═══════════════════════════════════════════════════════════════════════════


async def _local_dexscreener_price(mint: str, token: str | None = None) -> dict[str, Any] | None:
    """Local raw-DexScreener provider (the backend's is not vendored).

    Returns ``{"pairs": [...]}`` or None — the shape the price oracle tier 2
    and ``_data_sources.get_dexscreener_pairs`` expect. Pairs are normalized
    so the queried token is ALWAYS the ``baseToken`` (the backend provider
    does the same): when the token appears on the quote side of a pair
    (stablecoins like USDC are the quote on most pairs), the sides are
    swapped and the price fields inverted — otherwise the oracle's identity
    read (``baseToken.name/symbol``) returns the OTHER side's name. Fail-open.
    """
    target = (token or mint or "").strip()
    if not target:
        return None
    try:
        from rugmunchbot.tools._client import http_get_json

        body = await asyncio.wait_for(
            http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{target}", timeout=8.0),
            timeout=10.0,
        )
        if not isinstance(body, dict):
            return None
        pairs = body.get("pairs")
        if isinstance(pairs, list):
            body["pairs"] = [_normalize_dex_pair(p, target) for p in pairs if isinstance(p, dict)]
        return body
    except Exception as exc:
        logger.debug("local_dexscreener_failed addr=%.40s err=%s", target[:40], str(exc)[:80])
        return None


def _normalize_dex_pair(pair: dict[str, Any], token: str) -> dict[str, Any]:
    """Ensure ``token`` is the pair's ``baseToken`` (swap when it is the quote)."""
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    if not isinstance(base, dict) or not isinstance(quote, dict):
        return pair
    token_addr = str(token).lower()
    base_addr = str(base.get("address") or "").lower()
    quote_addr = str(quote.get("address") or "").lower()
    if base_addr == token_addr or quote_addr != token_addr:
        return pair
    swapped = dict(pair)
    swapped["baseToken"] = quote
    swapped["quoteToken"] = base
    try:
        price_usd = float(pair.get("priceUsd") or 0)
        price_native = float(pair.get("priceNative") or 0)
        if price_native > 0:
            swapped["priceUsd"] = price_usd / price_native
            swapped["priceNative"] = 1.0 / price_native
    except (TypeError, ValueError, ZeroDivisionError) as e:
        logger.debug("pair_swap:price_recompute_failed err=%s", e)
    return swapped


async def _local_dex_pairs(token: str, chain: str) -> list[dict[str, Any]]:
    """Best-effort DexScreener-compatible pair list (own ClickHouse first)."""
    try:
        from rugmunch_scanner.unified.engines.own_dex_data import get_own_dex_data

        dex = await asyncio.wait_for(get_own_dex_data(token, chain), timeout=_ENGINE_TIMEOUT_S)
        pairs = (dex or {}).get("pairs") or []
        if pairs:
            return [p for p in pairs if isinstance(p, dict)]
    except Exception as exc:
        logger.debug("local_dex_data_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
    # Fallback: raw DexScreener API (provider stub above / _data_sources).
    try:
        from rugmunch_scanner.unified.unified._data_sources import get_dexscreener_pairs

        raw = await asyncio.wait_for(get_dexscreener_pairs(token), timeout=10.0)
        pairs = (raw or {}).get("pairs") or []
        return [p for p in pairs if isinstance(p, dict)]
    except Exception:
        return []


def _best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest-liquidity pair (mirrors the bot's ``_ds_market_snapshot`` pick)."""
    if not pairs:
        return None
    try:
        return max(
            pairs,
            key=lambda p: (
                float(((p.get("liquidity") or {}).get("usd")) or 0)
                + float(((p.get("volume") or {}).get("h24")) or 0) * 0.01
            ),
        )
    except (TypeError, ValueError):
        return pairs[0]


async def _local_token_price(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``token_price`` — tiered price oracle + own DEX pairs."""
    if not token:
        return _MISS
    payload: dict[str, Any] = {}
    try:
        from rugmunch_scanner.unified.engines.price_oracle import get_token_price

        price = await asyncio.wait_for(get_token_price(token, chain), timeout=12.0)
        if isinstance(price, dict) and price.get("price_usd") is not None:
            payload["price_usd"] = price.get("price_usd")
            payload["source"] = f"local:{price.get('source', 'price_oracle')}"
            payload["timestamp"] = price.get("timestamp")
            payload["confidence"] = price.get("confidence")
    except Exception as exc:
        logger.debug("local_price_oracle_failed addr=%.40s err=%s", token[:40], str(exc)[:80])
    pairs = await _local_dex_pairs(token, chain)
    best = _best_pair(pairs)
    if best:
        base = best.get("baseToken") or {}
        payload.setdefault("name", base.get("name") or "")
        payload.setdefault("symbol", base.get("symbol") or "")
        if best.get("marketCap") is not None:
            payload.setdefault("market_cap_usd", best.get("marketCap"))
        liq = (best.get("liquidity") or {}).get("usd")
        if liq is not None:
            payload.setdefault("liquidity_usd", liq)
        vol = (best.get("volume") or {}).get("h24")
        if vol is not None:
            payload.setdefault("volume_24h_usd", vol)
        if best.get("fdv") is not None and "market_cap_usd" not in payload:
            payload.setdefault("market_cap_usd", best.get("fdv"))
    if not payload:
        return _MISS
    return {
        "data": payload,
        "source": payload.get("source", "local:price_oracle"),
        "tier": "free",
        "is_local": True,
    }


async def _local_token_metadata(token: str, chain: str) -> dict[str, Any]:
    """DataBus ``token_metadata`` — DexScreener-compatible pairs payload."""
    if not token:
        return _MISS
    pairs = await _local_dex_pairs(token, chain)
    if not pairs:
        return _MISS
    best = _best_pair(pairs)
    base = best.get("baseToken") or {} if best else {}
    return {
        "data": {
            "pairs": pairs,
            "name": (base.get("name") or "") if isinstance(base, dict) else "",
            "symbol": (base.get("symbol") or "") if isinstance(base, dict) else "",
        },
        "source": "local:own_dex_data",
        "tier": "free",
        "is_local": True,
    }


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


# ═══════════════════════════════════════════════════════════════════════════
# Public entry — run the vendored scanner in-process
# ═══════════════════════════════════════════════════════════════════════════


def _to_v2_payload(result: Any, address: str, chain: str) -> dict[str, Any] | None:
    """Convert the vendored ``ScanResult`` into the bot's v2 payload dict.

    The backend v2 endpoint serializes with the same legacy serializer
    (``_to_legacy_dict``), so this matches the HTTP payload the bot's
    handlers were built against: ``status="ok"``, top-level
    ``safety_score``/``risk_flags``/``evidence``/``wallet_metrics``/
    ``bundle_analysis``/``deep_profile``, with the rich per-dimension data
    carried inside ``evidence`` (holders / liquidity / security / deployer /
    market / funding_analysis). A few alias keys are added so the bot's
    formatters and the RiskReport builder find the data they read top-level
    on the backend payload. Returns ``None`` when the engine produced no
    usable core data (callers then fall back to the backend HTTP scan).
    """
    from rugmunch_scanner.unified.unified._legacy import _to_legacy_dict

    payload = _to_legacy_dict(result)
    payload["status"] = "ok"
    payload["address"] = address
    payload["token_address"] = address
    payload["chain"] = result.chain or chain
    payload["background_status"] = getattr(result, "background_status", "complete")
    flags = payload.get("flags") or []
    payload.setdefault("risk_flags", flags)
    payload.setdefault("risk_score", round(100.0 - float(payload.get("safety_score") or 0), 2))
    # The engine's own-data intel at the top level (the backend payload
    # carries these; the legacy serializer keeps them nested inside
    # evidence — surface both so the RiskReport renders real data).
    if result.holders and isinstance(result.holders, dict):
        payload.setdefault("holder_analysis", result.holders)
        top = result.holders.get("top_holders") or []
        if isinstance(top, list) and top:
            payload.setdefault("top_holders", top)
        payload.setdefault("holders", result.holders.get("holder_count"))
    if result.security and isinstance(result.security, dict):
        payload.setdefault("security", result.security)
    if result.dev_reputation and isinstance(result.dev_reputation, dict):
        payload.setdefault(
            "deployer_address",
            result.dev_reputation.get("deployer_address")
            or result.dev_reputation.get("address")
            or "",
        )
    # Market map with price-change / volume timeframes (the backend's price
    # oracle tier attaches this; the bot's v2_token_to_legacy reads it).
    if result.price_usd and "market" not in payload:
        market: dict[str, Any] = {"price_usd": result.price_usd}
        pairs: list[dict[str, Any]] = []
        if (
            result.liquidity
            and isinstance(result.liquidity, dict)
            and result.liquidity.get("pairs")
        ):
            pairs = result.liquidity.get("pairs") or []
        best = _best_pair(pairs) if pairs else None
        if best:
            pc = best.get("priceChange") or {}
            vol = best.get("volume") or {}
            market["price_change"] = {
                "h1": pc.get("h1"),
                "h6": pc.get("h6"),
                "h24": pc.get("h24"),
            }
            market["volume"] = {
                "h1": vol.get("h1"),
                "h6": vol.get("h6"),
                "h24": vol.get("h24"),
            }
        payload["market"] = market

    # Honesty gate: a scan with no core identity, no modules and no evidence
    # is a miss — the caller falls back to the backend HTTP scan.
    has_scan_data = bool(
        (result.name or result.symbol)
        or payload.get("safety_score") is not None
        or payload.get("modules_run")
        or payload.get("evidence")
    )
    if not has_scan_data:
        return None
    return payload


async def scan_token_v2_local(
    address: str,
    chain: str,
    *,
    deep: bool = False,
) -> dict[str, Any] | None:
    """Run the vendored ``UnifiedTokenScanner`` in-process → v2 payload dict.

    ``deep=True`` maps to the engine's SENTINEL deep tier (``scan_tier``
    ``"deep"``, elite phase tier) — the full free+premium module set the
    vendored fleet supports. Returns the v2-compatible dict or ``None`` when
    the engine produced no usable data (callers fall back to the backend).
    """
    if not address:
        return None
    install_import_bridge()
    try:
        from rugmunch_scanner.unified.unified import get_token_scanner

        scanner = get_token_scanner()
        result = await asyncio.wait_for(
            scanner.scan_token(address=address, chain=chain, tier="deep" if deep else "quick"),
            timeout=_LOCAL_SCAN_TIMEOUT_S,
        )
    except Exception as exc:
        logger.warning(
            "local_v2_scan_failed addr=%.40s chain=%s deep=%s err=%s",
            address[:40],
            chain,
            deep,
            str(exc)[:160],
        )
        return None
    return _to_v2_payload(result, address, chain)


__all__ = ["install_import_bridge", "scan_token_v2_local"]

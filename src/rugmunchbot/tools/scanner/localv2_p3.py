"""_local_v2.py split part: install_import_bridge, _to_v2_payload."""

from __future__ import annotations

import sys
import types
from typing import Any

from . import localv2_p1
from .localv2_p1 import _best_pair, _local_dexscreener_price, _stub_module
from .localv2_p2 import _databus_fetch


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
    if localv2_p1._INSTALLED:
        return
    localv2_p1._INSTALLED = True

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

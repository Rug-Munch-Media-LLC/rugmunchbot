"""Unit tests for the split ``_local_v2`` parts — pure mapping + engine dispatch.

The split moved behaviour into ``localv2_p1..p4``; these tests pin the part
that has no smoke coverage (the GoPlus mapping and the DataBus dispatch) and
the legacy payload adapter. No network, no vendored engine is executed.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from rugmunchbot.tools.scanner import _local_v2 as v2


def _install_engine(monkeypatch: pytest.MonkeyPatch, module: str, attr: str, result: Any) -> None:
    """Install a stand-in vendored engine module exposing one coroutine."""
    stub = type(sys)(module)

    async def _call(*_args: Any, **_kwargs: Any) -> Any:
        if isinstance(result, Exception):
            raise result
        return result

    setattr(stub, attr, _call)
    monkeypatch.setitem(sys.modules, module, stub)


# ── _to_goplus_checks ────────────────────────────────────────────────────


def test_goplus_maps_every_engine_flag() -> None:
    checks = v2._to_goplus_checks(
        {
            "is_honeypot": True,
            "is_mintable": True,
            "ownership_renounced": False,
            "is_proxy": True,
        }
    )
    assert checks["HP01"]["status"] == "fail"
    assert checks["MINT"]["status"] == "fail"
    assert checks["OWNER"]["status"] == "fail"
    assert checks["PROXY"]["status"] == "fail"


def test_goplus_clean_flags_pass() -> None:
    checks = v2._to_goplus_checks(
        {
            "is_honeypot": False,
            "is_mintable": False,
            "ownership_renounced": True,
            "is_proxy": False,
        }
    )
    assert {c["status"] for c in checks.values()} == {"pass"}
    assert len(checks) == 4


def test_goplus_absent_flags_stay_absent() -> None:
    assert v2._to_goplus_checks({}) == {}


def test_goplus_tax_threshold_is_ten_percent() -> None:
    checks = v2._to_goplus_checks({"buy_tax_pct": 12.5, "sell_tax_pct": 5.0})
    assert checks["BUY_TAX"]["status"] == "fail"
    assert "SELL_TAX" not in checks


# ── engine dispatchers ───────────────────────────────────────────────────

SECURITY_MOD = "rugmunch_scanner.unified.engines.own_security_analysis"
HOLDER_MOD = "rugmunch_scanner.unified.engines.own_holder_analysis"
VOLUME_MOD = "rugmunch_scanner.unified.engines.own_volume_authenticity"
ENTITY_MOD = "rugmunch_scanner.unified.engines.cross_chain_entity"

DISPATCH = [
    ("_local_token_security", SECURITY_MOD, "get_own_security", "local:own_security"),
    (
        "_local_holder_data",
        HOLDER_MOD,
        "get_own_holder_analysis",
        "local:own_holder_analysis",
    ),
    (
        "_local_volume_authenticity",
        VOLUME_MOD,
        "get_own_volume_authenticity",
        "local:own_volume_authenticity",
    ),
    (
        "_local_cross_chain_entity",
        ENTITY_MOD,
        "resolve_cross_chain_entity",
        "local:cross_chain_entity",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("fn_name", "module", "attr", "source"), DISPATCH)
async def test_engine_dispatch_wraps_envelope(
    monkeypatch: pytest.MonkeyPatch, fn_name: str, module: str, attr: str, source: str
) -> None:
    _install_engine(monkeypatch, module, attr, {"holders": 3})
    out = await getattr(v2, fn_name)("tok", "solana")
    assert out["source"] == source
    assert out["data"]["holders"] == 3
    assert out["tier"] == "free"
    assert out["is_local"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("fn_name", "module", "attr", "source"), DISPATCH)
async def test_engine_dispatch_misses_are_honest(
    monkeypatch: pytest.MonkeyPatch, fn_name: str, module: str, attr: str, source: str
) -> None:
    fn = getattr(v2, fn_name)
    assert await fn("", "solana") is v2._MISS
    _install_engine(monkeypatch, module, attr, {"result": "unavailable"})
    assert await fn("tok", "solana") is v2._MISS
    _install_engine(monkeypatch, module, attr, RuntimeError("engine down"))
    assert await fn("tok", "solana") is v2._MISS


@pytest.mark.asyncio
async def test_databus_dispatch_and_unknown_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_engine(monkeypatch, SECURITY_MOD, "get_own_security", {"is_honeypot": False})
    out = await v2._databus_fetch("token_security", address="tok", chain="solana")
    assert out["source"] == "local:own_security"
    assert out["data"]["checks"]["HP01"]["status"] == "pass"
    assert await v2._databus_fetch("token_launches", address="tok") is v2._MISS


@pytest.mark.asyncio
async def test_databus_dispatch_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_engine(monkeypatch, HOLDER_MOD, "get_own_holder_analysis", RuntimeError("boom"))
    assert await v2._databus_fetch("holder_data", token_address="tok") is v2._MISS


# ── _to_v2_payload ───────────────────────────────────────────────────────


def _result(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "chain": "ethereum",
        "name": "Tok",
        "symbol": "TOK",
        "holders": {"holder_count": 42, "top_holders": [{"address": "a"}]},
        "security": {"is_honeypot": False},
        "dev_reputation": {"deployer_address": "0xdev"},
        "price_usd": 1.5,
        "liquidity": {
            "pairs": [{"liquidity": {"usd": 9}, "priceChange": {"h1": 1}, "volume": {"h1": 2}}]
        },
        "background_status": "complete",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _legacy(monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]) -> None:
    module = importlib.import_module("rugmunch_scanner.unified.unified._legacy")
    monkeypatch.setattr(module, "_to_legacy_dict", lambda _result: dict(payload))


def test_payload_aliases_and_market(monkeypatch: pytest.MonkeyPatch) -> None:
    _legacy(monkeypatch, {"safety_score": 70.0, "flags": ["f"], "modules_run": ["security"]})
    out = v2._to_v2_payload(_result(), "0xtok", "solana")
    assert out is not None
    assert out["status"] == "ok"
    assert out["address"] == "0xtok"
    assert out["chain"] == "ethereum"
    assert out["risk_score"] == 30.0
    assert out["risk_flags"] == ["f"]
    assert out["holders"] == 42
    assert out["deployer_address"] == "0xdev"
    assert out["market"]["price_usd"] == 1.5
    assert out["market"]["price_change"]["h1"] == 1


def test_payload_without_scan_data_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _legacy(monkeypatch, {})
    out = v2._to_v2_payload(_result(name="", symbol=""), "0xtok", "solana")
    assert out is None

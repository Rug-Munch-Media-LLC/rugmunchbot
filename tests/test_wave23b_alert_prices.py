"""Test wave 23b — alert price-batch cascade over stubbed transports.

Contracts read from source (2026-08-22). The own-data-first resolver runs
for real; only the transport boundary is stubbed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.handlers.alerts import _prices as ap

if TYPE_CHECKING:
    import pytest

_EVM_A = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_EVM_B = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_empty_batch_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise AssertionError("resolver must not be called for an empty batch")

    monkeypatch.setattr(ap, "resolve_with_fallback", boom)
    import asyncio

    assert asyncio.run(ap._fetch_prices_batch([])) == {}


def _install_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real cascade semantics: primary if valid, else fallback."""

    async def fake_resolve(name: str, primary: Any, fallback: Any, **kw: Any) -> Any:
        res = await primary()
        if kw["is_valid"](res):
            return res
        return await fallback()

    monkeypatch.setattr(ap, "resolve_with_fallback", fake_resolve)


def test_own_data_primary_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_resolver(monkeypatch)

    async def fake_backend(path: str, **kw: Any) -> dict[str, Any]:
        assert "ethereum" in path
        assert _EVM_A.lower() in path
        return {"cached": True, "data": {"price_usd": 1.23}}

    monkeypatch.setattr(ap, "backend_get_json", fake_backend)
    monkeypatch.setattr(ap, "detect_chain", lambda addr: "ethereum")

    import asyncio

    out = asyncio.run(ap._fetch_prices_batch([_EVM_A]))
    assert out == {_EVM_A.lower(): 1.23}


def test_partial_own_data_fills_rest_from_dexscreener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_resolver(monkeypatch)

    async def fake_backend(path: str, **kw: Any) -> dict[str, Any]:
        if _EVM_A.lower() in path:
            return {"cached": True, "data": {"price_usd": 1.0}}
        return {}  # cache cold for B

    seen_urls: list[str] = []

    async def fake_http(url: str, **kw: Any) -> dict[str, Any]:
        seen_urls.append(url)
        # Real contract: _external batches ONLY the missing addresses, so
        # the response cannot contain a pair for the own-data hit.
        return {"pairs": [{"baseToken": {"address": _EVM_B}, "priceUsd": "4.56"}]}

    monkeypatch.setattr(ap, "backend_get_json", fake_backend)
    monkeypatch.setattr(ap, "detect_chain", lambda addr: "ethereum")
    monkeypatch.setattr(ap, "http_get_json", fake_http)

    import asyncio

    out = asyncio.run(ap._fetch_prices_batch([_EVM_A, _EVM_B]))
    # Own hit kept at its own price; only the missing address filled externally.
    assert out == {_EVM_A.lower(): 1.0, _EVM_B.lower(): 4.56}
    assert len(seen_urls) == 1
    assert _EVM_B.lower() in seen_urls[0]


def test_unknown_chain_skips_own_leg_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_resolver(monkeypatch)

    async def fail_backend(path: str, **kw: Any) -> None:
        raise AssertionError("own leg must not query for unknown chains")

    async def fake_http(url: str, **kw: Any) -> dict[str, Any]:
        return {"pairs": [{"baseToken": {"address": _EVM_A}, "priceUsd": "7"}]}

    monkeypatch.setattr(ap, "backend_get_json", fail_backend)
    monkeypatch.setattr(ap, "detect_chain", lambda addr: "unknown")
    monkeypatch.setattr(ap, "http_get_json", fake_http)

    import asyncio

    out = asyncio.run(ap._fetch_prices_batch([_EVM_A]))
    assert out == {_EVM_A.lower(): 7.0}

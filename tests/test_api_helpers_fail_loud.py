"""API helpers must fail LOUD on backend errors — fail-open regression guard.

Audit 2026-08-21 (section F): the bot's backend helpers mapped every
non-200 (wallet /pnl + /tokens returned bare 502s) to a silent ``None``,
which commands rendered as "no data" — users could not tell a dead backend
from an empty wallet. The backend now returns an honest contract
(200+{"data": null, "reason": "no_data"}) for genuine no-data, so any
non-200 that still reaches the bot is a REAL error and must surface loudly:

* ``backend_json_get`` / ``backend_json_post`` raise ``BackendApiError``
  (carrying the HTTP status) instead of returning ``None``.
* External-provider helpers (``http_get_json``) KEEP the None=miss contract
  — the multi-provider fallback chains depend on it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from rugmunchbot.tools import _client, budget, registry


class FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeClient:
    """Stands in for httpx.AsyncClient (resolved at call time by get_shared_client).

    Response/error configuration is CLASS-level: get_shared_client pools one
    client per timeout bucket, and tests must not care which bucket a helper
    picks.
    """

    response: Any = FakeResponse(200, {})
    raise_on_call: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[tuple[str, str]] = []

    async def get(self, url: str, **kw: Any) -> FakeResponse:
        self.calls.append(("GET", url))
        if type(self).raise_on_call:
            raise type(self).raise_on_call
        return type(self).response

    async def post(self, url: str, **kw: Any) -> FakeResponse:
        self.calls.append(("POST", url))
        if type(self).raise_on_call:
            raise type(self).raise_on_call
        return type(self).response

    @property
    def is_closed(self) -> bool:
        return False


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch):
    """Install the fake client class + unlimited budget; yield the class to configure."""
    monkeypatch.setattr(_client.httpx, "AsyncClient", FakeClient)
    _client._shared_clients.clear()
    FakeClient.response = FakeResponse(200, {})
    FakeClient.raise_on_call = None
    monkeypatch.setattr(budget.budget, "acquire", lambda provider: True)
    yield FakeClient
    _client._shared_clients.clear()
    FakeClient.response = FakeResponse(200, {})
    FakeClient.raise_on_call = None


# ── The loud contract for backend helpers ────────────────────────────────


@pytest.mark.asyncio
async def test_backend_json_get_returns_dict_on_200(fake_http) -> None:
    fake_http["client"] = None
    body = await registry.backend_json_get("/api/v1/x")
    assert body == {}  # fake default 200 {}


@pytest.mark.asyncio
async def test_backend_json_get_non_200_raises_with_status(fake_http) -> None:
    fake_http["client"].response = FakeResponse(502, {"detail": "bad gateway"})
    with pytest.raises(_client.BackendApiError) as excinfo:
        await registry.backend_json_get("/api/v1/databus/wallet/0xabc/pnl")
    assert excinfo.value.status_code == 502
    assert "502" in str(excinfo.value)


@pytest.mark.asyncio
async def test_backend_json_post_non_200_raises_with_status(fake_http) -> None:
    fake_http["client"].response = FakeResponse(503, {})
    with pytest.raises(_client.BackendApiError) as excinfo:
        await registry.backend_json_post("/api/v1/bot/scan", json={"address": "0xabc"})
    assert excinfo.value.status_code == 503


@pytest.mark.asyncio
async def test_backend_json_get_network_error_raises_not_none(fake_http) -> None:
    fake_http["client"].raise_on_call = httpx.ConnectError("connection refused")
    with pytest.raises(_client.BackendApiError):
        await registry.backend_json_get("/api/v1/x")


@pytest.mark.asyncio
async def test_backend_json_get_budget_exhausted_is_loud(
    monkeypatch: pytest.MonkeyPatch, fake_http
) -> None:
    monkeypatch.setattr(budget.budget, "acquire", lambda provider: False)
    with pytest.raises(_client.BackendApiError) as excinfo:
        await registry.backend_json_get("/api/v1/x")
    assert excinfo.value.status_code == 429  # rate-limit sentinel — never a silent None


@pytest.mark.asyncio
async def test_backend_json_get_invalid_json_body_raises(fake_http) -> None:
    fake_http["client"].response = FakeResponse(200, ValueError("bad json"))
    with pytest.raises(_client.BackendApiError):
        await registry.backend_json_get("/api/v1/x")


@pytest.mark.asyncio
async def test_backend_json_get_non_object_body_raises(fake_http) -> None:
    fake_http["client"].response = FakeResponse(200, [1, 2, 3])
    with pytest.raises(_client.BackendApiError):
        await registry.backend_json_get("/api/v1/x")


# ── External-provider helpers KEEP the None=miss fallback contract ──────


@pytest.mark.asyncio
async def test_http_get_json_external_still_returns_none_on_non_200(fake_http) -> None:
    fake_http["client"].response = FakeResponse(429, {})
    assert await _client.http_get_json("https://api.dexscreener.com/x") is None


@pytest.mark.asyncio
async def test_http_get_json_external_still_returns_none_on_network_error(fake_http) -> None:
    fake_http["client"].raise_on_call = httpx.ConnectError("boom")
    assert await _client.http_get_json("https://api.dexscreener.com/x") is None


# ── Loud errors surface in tool results instead of silent no-data ───────


@pytest.mark.asyncio
async def test_wallet_analysis_error_names_http_status_when_backend_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """registry.run must carry the HTTP status to the user-facing error."""
    from rugmunchbot.tools import security

    async def _boom(path: str, **kw: Any) -> dict[str, Any]:
        raise _client.BackendApiError(f"HTTP 502 for {path}", status_code=502, url=path)

    monkeypatch.setattr(security, "backend_json_get", _boom)
    monkeypatch.setattr(security, "backend_json_post", _boom)

    reg = registry.ToolRegistry()
    reg.register("wallet_analysis", security.wallet_analysis, timeout=5.0, ttl=0)
    result = await reg.run("wallet_analysis", address="0xabc", chain="ethereum")
    assert result.ok is False
    assert result.error is not None
    assert "502" in result.error  # loud — not "no live data" from a silent None

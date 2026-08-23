"""DataBus client + scanner security-fallback rung tests.

Offline: HTTP stubbed at the httpx boundary. Contracts:

* databus_price / databus_wallet / databus_token_scan parse happy paths
* every call is fail-open (None on non-200, bad payload, transport error)
* short-TTL memoization prevents repeat backend hits within TTL
* security chain: DataBus rung fires only when GoPlus+honeypot.is came up
  empty, lifts honeypot/verified flags and caps its risk contribution
"""

from __future__ import annotations

import pytest

from rugmunchbot.tools import databus as db
from rugmunchbot.tools.scanner import _providers as prov


@pytest.fixture(autouse=True)
def _clean_cache():
    db.clear_cache()
    yield
    db.clear_cache()


# ── Client happy paths ───────────────────────────────────────────────


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_databus_price_parses_pair(monkeypatch):
    class FakeClient:
        async def get(self, url, params=None, timeout=None):
            assert "/fetch/token_price" in url
            return _Resp(
                payload={
                    "data": {
                        "pairs": [
                            {
                                "priceUsd": "1.23",
                                "priceChange": {"h24": 5.5},
                                "volume": {"h24": "999.0"},
                            }
                        ]
                    }
                }
            )

    async def fake_shared_client(timeout):
        return FakeClient()

    monkeypatch.setattr(db, "get_shared_client", fake_shared_client)
    out = await db.databus_price("So11111111111111111111111111111111111111112")
    assert out == {"price_usd": 1.23, "change_24h": 5.5, "volume_24h": 999.0, "source": "databus"}


@pytest.mark.asyncio
async def test_databus_price_fail_open(monkeypatch):
    class FakeClient:
        async def get(self, url, params=None, timeout=None):
            return _Resp(status_code=503)

    async def fake_shared_client(timeout):
        return FakeClient()

    monkeypatch.setattr(db, "get_shared_client", fake_shared_client)
    assert await db.databus_price("mint") is None
    assert await db.databus_price("") is None  # empty guard


@pytest.mark.asyncio
async def test_databus_memoizes_within_ttl(monkeypatch):
    calls = {"n": 0}

    class FakeClient:
        async def get(self, url, params=None, timeout=None):
            calls["n"] += 1
            return _Resp(payload={"data": {"pairs": [{"priceUsd": "2"}]}})

    async def fake_shared_client(timeout):
        return FakeClient()

    monkeypatch.setattr(db, "get_shared_client", fake_shared_client)
    await db.databus_price("mint")
    await db.databus_price("mint")
    assert calls["n"] == 1  # second hit served from memo


@pytest.mark.asyncio
async def test_databus_wallet_and_scan_fail_open(monkeypatch):
    class FakeClient:
        async def get(self, url, timeout=None):
            raise ConnectionError("backend down")

        async def post(self, url, json=None, headers=None, timeout=None):
            raise ConnectionError("backend down")

    async def fake_shared_client(timeout):
        return FakeClient()

    monkeypatch.setattr(db, "get_shared_client", fake_shared_client)
    assert await db.databus_wallet("addr") is None
    assert await db.databus_token_scan("addr", chain="ethereum") is None


# ── Scanner security-chain integration ───────────────────────────────


def _base_result():
    return {
        "chain": "ethereum",
        "honeypot": None,
        "contract_verified": False,
        "threats": [],
        "risk_score": 0,
        "errors": [],
        "top_holders": [],
        "mcap": 50000,
    }


@pytest.mark.asyncio
async def test_databus_rung_fires_when_free_providers_empty(monkeypatch):
    """GoPlus/honeypot.is both fail (client raises) → DataBus scan fills."""
    fired = {"databus": False}

    async def failing_get(self, url, timeout=None):
        raise ConnectionError("free tier down")

    async def fake_scan(address, chain="ethereum"):
        fired["databus"] = True
        return {
            "contract_verified": True,
            "threats": ["HONEYPOT DETECTED"],
            "risk_score": 55,
        }

    monkeypatch.setattr(db, "databus_token_scan", fake_scan)

    class FakeHTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get(self, url, timeout=None):
            raise ConnectionError("free tier down")

    result = _base_result()
    async with FakeHTTP() as client:
        await prov._scan_evm_security_provider(client, "0xABC", result)

    assert fired["databus"] is True
    assert result["honeypot"] is True
    assert result["contract_verified"] is True
    assert result["risk_score"] > 0


@pytest.mark.asyncio
async def test_databus_rung_skipped_when_goplus_answered(monkeypatch):
    """GoPlus answered (honeypot set) → DataBus rung must NOT fire."""
    fired = {"databus": False}

    async def fake_scan(address, chain="ethereum"):
        fired["databus"] = True
        return {}

    monkeypatch.setattr(db, "databus_token_scan", fake_scan)

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"result": {}}

    class FakeHTTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get(self, url, timeout=None):
            return FakeResponse()

    result = _base_result()
    async with FakeHTTP() as client:
        await prov._scan_evm_security_provider(client, "0xABC", result)

    assert fired["databus"] is False

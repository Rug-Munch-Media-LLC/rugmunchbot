"""Wave 34 — Vybe labeled-intelligence client + scan rung (offline)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rugmunchbot.tools import vybe as vb


@pytest.fixture(autouse=True)
def _clean():
    vb.clear_cache()
    yield
    vb.clear_cache()


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


@pytest.mark.asyncio
async def test_top_holders_parse():
    with patch.object(
        vb,
        "get_shared_client",
        return_value=_fake_client(
            _resp(
                {
                    "data": [
                        {
                            "ownerAddress": "A",
                            "ownerName": "Binance Exchange 2",
                            "percentageOfSupplyHeld": 8.0,
                            "valueUsd": "646000000",
                        },
                        {"ownerAddress": "B", "ownerName": "", "percentageOfSupplyHeld": 1.5},
                    ]
                }
            )
        ),
    ):
        out = await vb.vybe_top_holders("MINT", limit=2)
    assert out[0] == {
        "address": "A",
        "owner_name": "Binance Exchange 2",
        "pct": 8.0,
        "usd_value": 646000000.0,
        "label": "",
    }
    assert out[1]["pct"] == 1.5
    assert out[1]["usd_value"] == 0.0


def _fake_client(resp):
    c = MagicMock()
    c.get = AsyncMock(return_value=resp)
    return c


@pytest.mark.asyncio
async def test_fail_open_non200(monkeypatch):
    monkeypatch.setenv("VYBE_API_KEY", "k")
    with patch.object(vb, "get_shared_client", return_value=_fake_client(_resp({}, status=403))):
        assert await vb.vybe_token_details("MINT") is None
        assert await vb.vybe_top_holders("MINT") == []


@pytest.mark.asyncio
async def test_no_key_short_circuits(monkeypatch):
    monkeypatch.delenv("VYBE_API_KEY", raising=False)
    assert await vb.vybe_token_details("M") is None


@pytest.mark.asyncio
async def test_memoization(monkeypatch):
    monkeypatch.setenv("VYBE_API_KEY", "k")
    calls = {"n": 0}
    client = _fake_client(_resp({"data": []}))

    async def counting_get(*a, **k):
        calls["n"] += 1
        return client.get(*a, **k)

    with patch.object(vb, "get_shared_client", return_value=client):
        await vb.vybe_top_holders("M")
        await vb.vybe_top_holders("M")
    assert calls["n"] == 0  # served from memo (client.get is the mock itself)


@pytest.mark.asyncio
async def test_cex_concentration_threat():
    """Scan rung: >=30% CEX-labeled supply adds a threat + score."""

    holders = [
        {"address": f"A{i}", "owner_name": n, "pct": p, "usd_value": 0, "label": ""}
        for i, (n, p) in enumerate([("Binance Hot 1", 20.0), ("Coinbase 2", 15.0), ("Whale", 5.0)])
    ]
    with patch("rugmunchbot.tools.vybe.vybe_top_holders", AsyncMock(return_value=holders)):
        cex_pct = 0.0
        for vh in holders:
            name_l = (vh["owner_name"] or "").lower()
            if any(w in name_l for w in ("binance", "coinbase", "okx", "bybit")):
                cex_pct += vh["pct"]
        assert cex_pct == 35.0
        assert cex_pct >= 30

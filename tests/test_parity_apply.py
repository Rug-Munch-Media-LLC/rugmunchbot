"""Fail-open contract for the parity leg (cohorts / KOL / impact).

Every failure mode must leave the scan payload exactly as the vendored
enrichment chain left it — no section keys, no fabricated zeros — and record
``parity_status='unavailable'`` so a card shows nothing instead of lying.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import respx

from rugmunchbot.tools.scanner import _parity_apply as pa

BASE = "http://scanner.test"
ADDR = "So11111111111111111111111111111111111111112"
OK_BODY = {
    "status": "ok",
    "cohorts": {"team": {"count": 3}},
    "kol": {"kol": [{"handle": "@whale"}]},
    "impact": {"pair_address": "PAIR"},
    "risk_score": 99,  # service-only field — must NOT leak onto the bot payload
}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Start every test unconfigured; each test opts in to a URL."""
    monkeypatch.delenv("SCANNER_SERVICE_URL", raising=False)
    monkeypatch.delenv("SCANNER_DIRECT_URL", raising=False)
    monkeypatch.setattr(pa, "PARITY_TIMEOUT", 0.3)


def _payload() -> dict:
    return {"address": ADDR, "score": 7, "risk_level": "LOW"}


def _assert_untouched(out: dict, before: set[str]) -> None:
    assert out["parity_status"] == pa.UNAVAILABLE
    assert set(out) == before | {"parity_status"}
    for key in ("cohorts", "kol", "impact"):
        assert key not in out


async def test_url_unset_skips_the_call_and_keeps_payload():
    data = _payload()
    before = set(data)
    out = await pa.apply_parity(data, ADDR, "solana")
    assert out is data
    _assert_untouched(out, before)


@respx.mock
async def test_non_200_is_unavailable(monkeypatch):
    monkeypatch.setenv("SCANNER_SERVICE_URL", BASE)
    respx.post(f"{BASE}/scan").mock(return_value=httpx.Response(503))
    data = _payload()
    before = set(data)
    out = await pa.apply_parity(data, ADDR, "solana")
    _assert_untouched(out, before)


@respx.mock
async def test_non_json_is_unavailable(monkeypatch):
    monkeypatch.setenv("SCANNER_SERVICE_URL", BASE)
    respx.post(f"{BASE}/scan").mock(return_value=httpx.Response(200, content=b"<html>nope</html>"))
    data = _payload()
    before = set(data)
    out = await pa.apply_parity(data, ADDR, "solana")
    _assert_untouched(out, before)


@respx.mock
async def test_status_not_ok_is_unavailable(monkeypatch):
    monkeypatch.setenv("SCANNER_SERVICE_URL", BASE)
    respx.post(f"{BASE}/scan").mock(
        return_value=httpx.Response(200, json={"status": "error", "cohorts": {"team": {}}})
    )
    data = _payload()
    before = set(data)
    out = await pa.apply_parity(data, ADDR, "solana")
    _assert_untouched(out, before)


@respx.mock
async def test_transport_error_is_unavailable(monkeypatch):
    monkeypatch.setenv("SCANNER_SERVICE_URL", BASE)
    respx.post(f"{BASE}/scan").mock(side_effect=httpx.ConnectError("refused"))
    data = _payload()
    before = set(data)
    out = await pa.apply_parity(data, ADDR, "solana")
    _assert_untouched(out, before)


async def test_timeout_returns_within_budget_and_keeps_payload(monkeypatch):
    monkeypatch.setenv("SCANNER_SERVICE_URL", BASE)
    monkeypatch.setattr(pa, "PARITY_TIMEOUT", 0.05)

    async def _wedged(address: str, chain: str) -> dict:
        await asyncio.sleep(5)
        return {"cohorts": {"team": {"count": 1}}}

    monkeypatch.setattr(pa, "fetch_parity", _wedged)
    data = _payload()
    before = set(data)
    started = time.monotonic()
    out = await pa.apply_parity(data, ADDR, "solana")
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"parity gate leaked {elapsed:.2f}s into the scan"
    _assert_untouched(out, before)


@respx.mock
async def test_success_merges_only_the_three_sections(monkeypatch):
    monkeypatch.setenv("SCANNER_SERVICE_URL", BASE)
    respx.post(f"{BASE}/scan").mock(return_value=httpx.Response(200, json=OK_BODY))
    data = _payload()
    out = await pa.apply_parity(data, ADDR, "solana")
    assert out["parity_status"] == pa.OK
    assert out["cohorts"] == OK_BODY["cohorts"]
    assert out["kol"] == OK_BODY["kol"]
    assert out["impact"] == OK_BODY["impact"]
    assert "risk_score" not in out  # bot scan stays authoritative
    assert out["score"] == 7


@respx.mock
async def test_omitted_section_becomes_a_reason_block(monkeypatch):
    monkeypatch.setenv("SCANNER_SERVICE_URL", BASE)
    respx.post(f"{BASE}/scan").mock(
        return_value=httpx.Response(200, json={"status": "ok", "cohorts": {"team": {"count": 1}}})
    )
    out = await pa.apply_parity(_payload(), ADDR, "solana")
    assert out["parity_status"] == pa.OK
    assert out["kol"] == {"reason": "service omitted kol"}
    assert out["impact"] == {"reason": "service omitted impact"}

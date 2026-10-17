"""Report guardrails tests (2026-10-17).

Covers the fact-recency gate (reject undated old claims) and link
validation (dead-link detection, fail-open on network error). No live
network — httpx is monkeypatched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugmunchbot.channels import report_guardrails as rg


def _iso(days_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def test_recency_gate_no_dates_passes() -> None:
    ok, reason = rg.recency_gate("No dates here, just prose about a token.")
    assert ok is True
    assert reason == "no_dated_claims"


def test_recency_gate_fresh_iso_passes() -> None:
    ok, _ = rg.recency_gate(f"Reported on {_iso(0.5)} by the desk.")
    assert ok is True


def test_recency_gate_stale_iso_rejected() -> None:
    ok, reason = rg.recency_gate(f"The exploit happened on {_iso(30)}.")
    assert ok is False
    assert "stale_newest_claim" in reason


def test_recency_gate_prose_date_fresh() -> None:
    today = datetime.now(UTC)
    prose = today.strftime("%B %d, %Y").lstrip("0").replace(" 0", " ")
    ok, _ = rg.recency_gate(f"Published {prose}.")
    assert ok is True


def test_recency_gate_prose_date_stale() -> None:
    old = datetime.now(UTC) - timedelta(days=90)
    prose = old.strftime("%B %d, %Y").lstrip("0").replace(" 0", " ")
    ok, reason = rg.recency_gate(f"Published {prose}.")
    assert ok is False
    assert "stale" in reason


def test_recency_gate_newest_wins() -> None:
    """A stale claim alongside a fresh one passes (newest is fresh)."""
    text = f"Old news from {_iso(60)}; today {_iso(0.2)} it moved again."
    ok, _ = rg.recency_gate(text)
    assert ok is True


def test_extract_links_distinct_ordered() -> None:
    text = "See https://a.com and https://b.com and https://a.com again."
    assert rg.extract_links(text) == ["https://a.com", "https://b.com"]


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status_code = status


class _FakeClient:
    def __init__(self, statuses: dict[str, int], *, raise_on: set[str] | None = None) -> None:
        self._statuses = statuses
        self._raise_on = raise_on or set()

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def head(self, url: str) -> _FakeResp:
        if url in self._raise_on:
            raise ConnectionError("boom")
        return _FakeResp(self._statuses.get(url, 200))

    async def get(self, url: str) -> _FakeResp:
        if url in self._raise_on:
            raise ConnectionError("boom")
        return _FakeResp(self._statuses.get(url, 200))


@pytest.mark.asyncio
async def test_validate_links_detects_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _FakeClient({"https://dead.com": 404, "https://ok.com": 200})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
    dead = await rg.validate_links("https://ok.com and https://dead.com")
    assert dead == ["https://dead.com"]


@pytest.mark.asyncio
async def test_validate_links_fail_open_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _FakeClient({}, raise_on={"https://flaky.com"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
    dead = await rg.validate_links("https://flaky.com")
    assert dead == []


@pytest.mark.asyncio
async def test_report_guardrails_combines(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _FakeClient({"https://dead.com": 500})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
    ok, reasons = await rg.report_guardrails(f"Stale claim from {_iso(40)}. Link https://dead.com")
    assert ok is False
    assert any("stale" in r for r in reasons)
    assert any("dead_links" in r for r in reasons)


@pytest.mark.asyncio
async def test_report_guardrails_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    fake = _FakeClient({"https://ok.com": 200})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: fake)
    ok, reasons = await rg.report_guardrails(f"Fresh {_iso(0.1)}. Link https://ok.com")
    assert ok is True
    assert reasons == []


def test_report_guardrails_never_raises_on_bad_input() -> None:
    ok, _ = rg.recency_gate("")
    assert ok is True

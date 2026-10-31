"""The scanner seam: premium derives from an injected provider, never imports it."""

from __future__ import annotations

import asyncio

import pytest

from rugmunchbot.premium import scan_port, service


@pytest.fixture(autouse=True)
def _clean_port():
    scan_port.clear()
    yield
    scan_port.clear()


def test_unregistered_provider_raises_a_named_error() -> None:
    with pytest.raises(scan_port.ScanUnavailable):
        asyncio.run(service.scan_payload("mint", "solana"))


def test_registered_provider_is_used() -> None:
    calls: list[tuple[str, str]] = []

    async def fake(token: str, chain: str) -> dict:
        calls.append((token, chain))
        return {"token": token, "chain": chain}

    scan_port.register(fake)
    assert asyncio.run(service.scan_payload("mint", "solana")) == {
        "token": "mint",
        "chain": "solana",
    }
    assert calls == [("mint", "solana")]


def test_explicit_provider_wins_over_the_registered_one() -> None:
    async def registered(token: str, chain: str) -> dict:
        return {"from": "registered"}

    async def explicit(token: str, chain: str) -> dict:
        return {"from": "explicit"}

    scan_port.register(registered)
    assert asyncio.run(service.scan_payload("m", "solana", scanner=explicit)) == {
        "from": "explicit"
    }


def test_none_payload_becomes_an_empty_dict() -> None:
    async def refused(token: str, chain: str) -> None:
        return None

    assert asyncio.run(service.scan_payload("m", "solana", scanner=refused)) == {}

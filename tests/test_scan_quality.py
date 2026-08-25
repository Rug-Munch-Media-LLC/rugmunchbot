"""Scan-target resolution + citation quality tests (2026-08-25 quality push).

* resolve_scan_target — $TICKER→address via DexScreener search, address
  pass-through, fail-closed on unresolvable targets.
* _source_is_junk — dictionary/find-my-phone/content-farm blocklist.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.handlers.chat_ai._llm._sources import _source_is_junk
from rugmunchbot.tools.scanner._resolve_target import (
    ResolvedTarget,
    _pick_best_pair,
    resolve_scan_target,
)

# ── target resolution ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_address_passes_through(monkeypatch) -> None:
    addr = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    called = False

    def _boom(*a: Any, **k: Any):  # search must NOT be called for addresses
        nonlocal called
        called = True
        raise AssertionError("search called for address input")

    monkeypatch.setattr("rugmunchbot.tools.scanner._resolve_target.http_get_json", _boom)
    r = await resolve_scan_target(addr)
    assert isinstance(r, ResolvedTarget)
    assert not called


@pytest.mark.asyncio
async def test_ticker_resolves_to_deepest_liquidity_pair(monkeypatch) -> None:
    pairs = {
        "pairs": [
            {
                "baseToken": {"symbol": "SMOLCAT", "address": "AAAsmall"},
                "chainId": "solana",
                "liquidity": {"usd": 1000},
            },
            {
                "baseToken": {"symbol": "SMOLCAT", "address": "BBBbig"},
                "chainId": "solana",
                "liquidity": {"usd": 48480},
            },
            {
                "baseToken": {"symbol": "OTHER", "address": "CCCother"},
                "chainId": "solana",
                "liquidity": {"usd": 999999},
            },
        ]
    }

    async def _fake_get(url: str, **k: Any):
        return pairs

    monkeypatch.setattr("rugmunchbot.tools.scanner._resolve_target.http_get_json", _fake_get)
    r = await resolve_scan_target("$smolcat")
    assert r is not None
    assert r.address == "BBBbig"  # deepest liquidity among SYMBOL matches
    assert r.chain == "solana"
    assert r.resolved_from == "dexscreener_search"


@pytest.mark.asyncio
async def test_unresolvable_ticker_fails_closed(monkeypatch) -> None:
    async def _empty(url: str, **k: Any):
        return {"pairs": []}

    monkeypatch.setattr("rugmunchbot.tools.scanner._resolve_target.http_get_json", _empty)
    assert await resolve_scan_target("$nosuchtoken") is None


def test_pick_best_pair_prefers_symbol_match() -> None:
    pairs = [
        {"baseToken": {"symbol": "X", "address": "clone"}, "liquidity": {"usd": 5}},
        {"baseToken": {"symbol": "TARGET", "address": "real"}, "liquidity": {"usd": 1}},
    ]
    best = _pick_best_pair(pairs, "target")
    assert best["baseToken"]["address"] == "real"


# ── citation quality ─────────────────────────────────────────────────────


def test_dictionary_domains_are_junk() -> None:
    for url in (
        "https://www.merriam-webster.com/dictionary/give",
        "https://www.dictionary.com/browse/give",
        "https://minecraft.fandom.com/wiki/Commands/give",
        "https://myaccount.google.com/intro/find-your-phone",
        "https://www.icloud.com/find",
    ):
        assert _source_is_junk(url), url


def test_real_sources_not_junk() -> None:
    for url in (
        "https://dexscreener.com/solana/abc",
        "https://www.coindesk.com/markets/2026/08/25/x",
        "https://solscan.io/account/ABC",
        "https://rugmunch.io/scanner",
    ):
        assert not _source_is_junk(url), url


# ── market snapshot txns ─────────────────────────────────────────────────


def test_market_snapshot_carries_txn_flow() -> None:
    from rugmunchbot.tools.scanner._market import _ds_market_snapshot

    snap = _ds_market_snapshot(
        [
            {
                "liquidity": {"usd": 50000},
                "volume": {"h24": 100000},
                "txns": {"h24": {"buys": 1200, "sells": 800}, "h1": {"buys": 50, "sells": 90}},
            }
        ]
    )
    assert snap["txns_tf"]["h24"] == {"buys": 1200, "sells": 800}
    assert snap["txns_tf"]["h1"]["sells"] > snap["txns_tf"]["h1"]["buys"]


def test_merge_writes_txns_keys() -> None:
    from rugmunchbot.tools.scanner._market import _ds_market_snapshot, _merge_market_snapshot

    result: dict[str, Any] = {}
    snap = _ds_market_snapshot(
        [{"liquidity": {"usd": 10}, "txns": {"h6": {"buys": 7, "sells": 3}}}]
    )
    _merge_market_snapshot(result, snap)
    assert result["txns_6h"] == {"buys": 7, "sells": 3}

"""Wave 8 — clickhouse_fetchers: alpha-channel real-data fetchers.

All expectations probed against the implementation before asserting (oracle
policy). ClickHouse access is mocked at ``_ch_query``; the smart-money feed is
mocked at the module attribute (imported into this module's namespace).
"""

from __future__ import annotations

from typing import Any

import pytest

import rugmunchbot.channels.clickhouse_fetchers as chf
from rugmunchbot.channels.clickhouse_fetchers import (
    _fmt_usd,
    _rows_to_early_entries,
    _rows_to_whale_moves,
    _short_addr,
    fetch_smart_money_wallets,
    fetch_whale_moves,
)

# ── pure helpers ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("addr", "expected"),
    [
        ("So11111111111111111111111111111111111111112", "So1111...1112"),
        ("short", "short"),
        ("exactly12chr", "exactly12chr"),  # <= 12 untouched
    ],
)
def test_short_addr(addr: str, expected: str) -> None:
    assert _short_addr(addr) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(12345.67, "$12,346"), (0, "$0"), (None, "unknown"), ("x", "unknown")],
)
def test_fmt_usd(value: Any, expected: str) -> None:
    assert _fmt_usd(value) == expected


# ── whale moves ─────────────────────────────────────────────────────────


def _whale_row(tx: str, frm: str, to: str, token: str, value: float) -> tuple[Any, ...]:
    return ("solana", "ts", tx, frm, to, token, value, "transfer")


def test_rows_to_whale_moves_normalizes_and_skips() -> None:
    rows = [
        _whale_row(
            "txhash1234567890abcdef",
            "FromWallet111111",
            "ToWallet2222222222",
            "TokenAddr333333333333",
            25000.0,
        ),
        _whale_row("tx2", "f", "t", "", 999.0),  # no token address → skipped
        _whale_row("tx3", "f", "t", "Tok4", 0.0),  # zero value → skipped
    ]
    moves = _rows_to_whale_moves(rows, "solana", 5)
    assert len(moves) == 1
    m = moves[0]
    assert m["kind"] == "whale_move"
    assert m["title"] == "Whale: $25,000 TokenA...3333 moved"
    assert "from FromWa...1111" in m["body"]
    assert "to ToWall...2222" in m["body"]
    assert "tx txhash...cdef" in m["body"]
    assert m["value_usd"] == 25000.0
    assert m["source"] == "token_transfers"


def test_rows_to_whale_moves_respects_limit() -> None:
    rows = [_whale_row(f"tx{i}", "f", "t", f"Tok{i}", 10_000.0 + i) for i in range(5)]
    assert len(_rows_to_whale_moves(rows, "solana", 2)) == 2


async def test_fetch_whale_moves_via_mocked_ch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_query(sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
        captured["sql"] = sql
        captured["params"] = params
        return [_whale_row("txA", "f", "t", "TokX", 50_000.0)]

    monkeypatch.setattr(chf, "_ch_query", fake_query)
    moves = await fetch_whale_moves(chain="solana", limit=3, min_value_usd=10_000.0)
    assert len(moves) == 1
    assert moves[0]["token_symbol"] == "TokX"
    assert "wallet_memory.token_transfers" in captured["sql"]
    assert captured["params"]["min_val"] == 10_000.0
    assert captured["params"]["limit"] == 3
    assert "AND chain_id = %(chain)s" in captured["sql"]


# ── early entries ───────────────────────────────────────────────────────


def test_rows_to_early_entries_exclusions_and_delta() -> None:
    deploys = [("Deployer111", "TokenA44444444444", "solana", 100)]
    per_token = {
        "TokenA44444444444": [
            (101, "x", "Buyer1AAA", "t", 5000.0, "h1"),
            (102, "x", "deployer111", "t", 9000.0, "h2"),  # deployer (case-insens.) skipped
            (103, "x", "Buyer1AAA", "t", 7000.0, "h3"),  # duplicate wallet skipped
            (104, "x", "", "t", 8000.0, "h4"),  # empty wallet skipped
            (105, "x", "Buyer2BBB", "t", 0.0, "h5"),  # zero value skipped
        ]
    }
    entries = _rows_to_early_entries(deploys, per_token, "solana", 3)
    assert len(entries) == 1
    e = entries[0]
    assert e["wallet"] == "Buyer1AAA"
    assert e["block_delta"] == 1
    assert e["title"] == "🐣 Early whale: $5,000 in TokenA...4444"
    assert "within 1 blocks of deploy" in e["body"]


def test_rows_to_early_entries_malformed_deploy_skipped() -> None:
    deploys = [(None, "", "solana", 1)]  # no token address → skipped
    assert _rows_to_early_entries(deploys, {}, "solana", 3) == []


# ── smart money feed ────────────────────────────────────────────────────


async def test_smart_money_feed_failure_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(**kw: Any) -> dict[str, Any]:
        raise ConnectionError("feed down")

    monkeypatch.setattr(chf, "smart_money_feed", boom)
    assert await fetch_smart_money_wallets("solana", 5) == []


async def test_smart_money_feed_non_dict_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def weird(**kw: Any) -> Any:
        return ["not", "a", "dict"]

    monkeypatch.setattr(chf, "smart_money_feed", weird)
    assert await fetch_smart_money_wallets("solana", 5) == []


async def test_smart_money_feed_normalizes_wallets(monkeypatch: pytest.MonkeyPatch) -> None:
    async def feed(**kw: Any) -> dict[str, Any]:
        return {
            "smart_money_wallets": [
                {
                    "address": " WalletAAA ",
                    "entity": "KOL X",
                    "signal": "buy",
                    "entity_type": "kol",
                    "estimated_usd": 120_000,
                },
                {"address": "", "entity": "no addr"},  # skipped
                {"address": "WalletBBB"},  # label falls back to "Smart wallet"
                "garbage-row",  # non-dict skipped
            ]
        }

    monkeypatch.setattr(chf, "smart_money_feed", feed)
    out = await fetch_smart_money_wallets("solana", 5)
    assert len(out) == 2
    assert out[0]["address"] == "WalletAAA"  # stripped
    assert out[0]["label"] == "KOL X"
    assert out[0]["tags"] == ["buy", "kol"]
    assert out[0]["pnl_usd"] == 120_000
    assert out[1]["label"] == "Smart wallet"
    assert out[1]["tags"] == []

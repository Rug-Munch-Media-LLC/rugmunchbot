"""Test wave 13 — charting ASCII/pair helpers + webhook SSRF guards.

Contracts read from source (2026-08-22). Pure logic only; DNS paths are
stubbed at ``socket.getaddrinfo`` (stdlib boundary, not the unit under test).
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from rugmunchbot.handlers import webhook as webhook_module
from rugmunchbot.handlers.webhook import (
    _assert_safe_target,
    _is_blocked_address,
    _validate_webhook_url,
)
from rugmunchbot.tools.charting import (
    _bias,
    _candle_direction_row,
    _is_pair,
    _levels_line,
    _normalize_chain,
    _normalize_pairs,
    _pair_snapshot,
    _pick_best_pair,
    _price_ladder,
    _slice_candles,
    _sparkline,
    _summary_text,
    _to_float,
)

# ── charting: coercion / pair selection ──────────────────────────────────────


def test_to_float_coercion() -> None:
    assert _to_float("12.5") == 12.5
    assert _to_float(7) == 7.0
    assert _to_float(None) is None
    assert _to_float("") is None
    assert _to_float("junk") is None


def test_normalize_chain_lowercases_and_canonicalizes() -> None:
    assert _normalize_chain(" ETH ") == "ethereum"  # alias table is applied
    assert _normalize_chain("solana") == "solana"
    assert _normalize_chain("unknown-chain") == "unknown-chain"
    assert _normalize_chain("") == ""


def test_normalize_pairs_accepts_dict_list_and_filters_junk() -> None:
    p = {"pairAddress": "0xp", "baseToken": {"symbol": "A"}}
    assert _normalize_pairs({"pairs": [p]}) == [p]
    assert _normalize_pairs([p]) == [p]
    assert _normalize_pairs([p, "junk", 42]) == [p]
    assert _normalize_pairs(None) == []
    assert _normalize_pairs(42) == []


def test_is_pair_requires_address_and_base_token() -> None:
    assert _is_pair({"pairAddress": "0xp", "baseToken": {}}) is True
    assert _is_pair({"pairAddress": "", "baseToken": {}}) is False
    assert _is_pair({"pairAddress": "0xp"}) is False


def test_pick_best_pair_by_h24_volume() -> None:
    low = {"volume": {"h24": 100}}
    high = {"volume": {"h24": 900}}
    assert _pick_best_pair([low, high, {}]) is high
    assert _pick_best_pair([]) is None


def test_pair_snapshot_maps_real_fields() -> None:
    pair = {
        "chainId": "eth",
        "dexId": "uniswap",
        "pairAddress": "0xpair",
        "priceUsd": "0.0042",
        "baseToken": {"address": "0xbase", "symbol": "pepe", "name": "Pepe"},
        "volume": {"h24": 1234.5},
        "priceChange": {"h1": 1.0, "h6": -2.0, "h24": 12.0},
        "liquidity": {"usd": 50000},
    }
    snap = _pair_snapshot(pair, "", "")
    assert snap["address"] == "0xbase"  # falls back to baseToken address
    assert snap["symbol"] == "PEPE"
    assert snap["chain"] == "ethereum"  # alias canonicalized
    assert snap["price_usd"] == 0.0042
    assert snap["change_24h"] == 12.0
    assert snap["volume_24h"] == 1234.5
    assert snap["liquidity_usd"] == 50000.0


def test_pair_snapshot_falls_back_to_request_symbol() -> None:
    snap = _pair_snapshot({"chainId": "sol"}, "0xabc", "$foo")
    assert snap["address"] == "0xabc"
    assert snap["symbol"] == "$FOO"  # request symbol uppercased verbatim ($ kept)
    assert snap["liquidity_usd"] is None


# ── charting: ASCII rendering ────────────────────────────────────────────────


def test_slice_candles_per_timeframe() -> None:
    candles = [{"i": i} for i in range(30)]
    assert len(_slice_candles(candles, "24h")) == 24
    assert len(_slice_candles(candles, "7d")) == 7
    assert len(_slice_candles(candles, "bogus")) == 24  # default window
    assert _slice_candles([], "24h") == []


def test_sparkline_empty_flat_and_rising() -> None:
    assert _sparkline([]) == ""
    assert _sparkline([5.0, 5.0, 5.0]) == "---"  # zero span -> middle rung
    rising = _sparkline([0.0, 3.0, 6.0])
    assert rising[0] == "_"
    assert rising[-1] == "#"


def test_sparkline_samples_down_to_width() -> None:
    closes = [float(i) for i in range(100)]
    out = _sparkline(closes, width=10)
    assert len(out) == 10
    assert out[0] == "_"
    assert out[-1] == "#"


def test_candle_direction_row_symbols() -> None:
    candles = [
        {"open": 1, "close": 2},  # up
        {"open": 2, "close": 1},  # down
        {"open": 1, "close": 1},  # flat
        {"open": None, "close": 1},  # unknown
        {},  # unknown
    ]
    assert _candle_direction_row(candles) == "+-=.."


def test_price_ladder_labels_high_low_and_marks_bands() -> None:
    ladder = _price_ladder([1.0, 2.0, 3.0], width=10, height=2)
    top, bottom = ladder.split("\n")
    assert top.strip().startswith("3")  # high label on first row
    assert top.endswith(".**")
    assert bottom.strip().startswith("1")  # low label on last row
    assert bottom.endswith("**.")
    assert _price_ladder([]) == ""


def test_summary_text_full_and_degraded() -> None:
    full = _summary_text(
        {"rsi": 62.34, "ema9": 1.23456, "ema21": 1.1, "macd_label": "bullish", "trend": "BULLISH"}
    )
    assert full == "RSI-14 62.3 · EMA9 1.23456/EMA21 1.1 · MACD bullish · trend BULLISH"
    assert _summary_text({}) == "RSI-14 n/a · EMA n/a · MACD n/a · trend NEUTRAL"


def test_bias_scores_trend_and_rsi() -> None:
    assert _bias({"trend": "BULLISH", "rsi": 60}) == "bullish"  # +2
    assert _bias({"trend": "BEARISH", "rsi": 40}) == "bearish"  # -2
    assert _bias({"trend": "BULLISH", "rsi": 50}) == "neutral"  # +1 only
    assert _bias({}) == "neutral"


def test_levels_line_lists_only_present_fields() -> None:
    assert _levels_line({}) == ""
    line = _levels_line({"price": 0.5, "high": 1, "low": 0.25, "support": 0.3, "resistance": 0.9})
    assert line == "last 0.5 · high 1 · low 0.25 · support 0.3 · resistance 0.9"


# ── webhook: SSRF guards ─────────────────────────────────────────────────────


def test_validate_webhook_url_accepts_plain_http_urls() -> None:
    assert _validate_webhook_url("https://example.com/hook") == (True, "")
    assert _validate_webhook_url("http://hooks.example.io/x") == (True, "")


def test_validate_webhook_url_rejects_scheme_garbage_and_credentials() -> None:
    assert _validate_webhook_url("ftp://example.com")[0] is False
    assert _validate_webhook_url("not a url")[0] is False
    ok, err = _validate_webhook_url("https://user:pass@example.com/hook")
    assert ok is False
    assert "credentials" in err


@pytest.mark.parametrize(
    "ip",
    [
        "10.1.2.3",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.0.5",
        "0.0.0.0",  # noqa: S104 — unspecified address under test, not a bind
        "::1",
        "fc00::1",
        "fe80::1",
        "224.0.0.1",  # multicast
    ],
)
def test_is_blocked_address_blocks_internal_ranges(ip: str) -> None:
    assert _is_blocked_address(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2606:4700::1111"])
def test_is_blocked_address_allows_public_ips(ip: str) -> None:
    assert _is_blocked_address(ipaddress.ip_address(ip)) is False


def test_assert_safe_target_rejects_invalid_url_before_dns() -> None:
    with pytest.raises(ValueError, match="Invalid URL"):
        _assert_safe_target("https://user:pass@example.com")


def test_assert_safe_target_reports_unresolvable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(webhook_module.socket, "getaddrinfo", boom)
    with pytest.raises(ValueError, match="Could not resolve host"):
        _assert_safe_target("https://no-such-host.invalid/hook")


def test_assert_safe_target_blocks_private_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def private(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.9", 0))]

    monkeypatch.setattr(webhook_module.socket, "getaddrinfo", private)
    with pytest.raises(ValueError, match="blocked"):
        _assert_safe_target("https://internal.example.com/hook")


def test_assert_safe_target_allows_public_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def public(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(webhook_module.socket, "getaddrinfo", public)
    _assert_safe_target("https://example.com/hook")  # must not raise

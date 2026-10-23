"""Market selection contracts — Gamma-shaped fixture payloads, pure functions."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rugmunchbot.polymarket.markets import select_markets

_FIXTURE = Path(__file__).parent / "fixtures" / "polymarket" / "markets.json"
_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _raw() -> list[dict]:
    return json.loads(_FIXTURE.read_text())


def test_filters_thin_flat_and_off_category_markets() -> None:
    picked = select_markets(
        _raw(),
        now=_NOW,
        min_abs_change_1d=0.05,
        categories=("crypto", "macro"),
        limit=10,
    )
    ids = [m.condition_id for m in picked]
    assert ids == ["0xaaa111", "0x111bbb", "0xddd444"]
    # thin liquidity, flat odds, sports, closed
    for excluded in ("0xbbb222", "0xccc333", "0xeee555", "0xfff666"):
        assert excluded not in ids


def test_ranking_prefers_volume_change_and_near_resolution() -> None:
    picked = select_markets(_raw(), now=_NOW, min_abs_change_1d=0.05, limit=3)
    assert picked[0].condition_id == "0xaaa111"  # highest 24h volume + 36h to end
    assert picked[0].hours_to_resolution is not None
    assert 0 < picked[0].hours_to_resolution < 48


def test_yes_price_uses_outcome_labels_not_list_order() -> None:
    picked = select_markets(_raw(), now=_NOW, categories=("crypto",), limit=10)
    sol = next(m for m in picked if m.condition_id == "0x111bbb")
    assert sol.yes_price == 0.75  # outcomes are ["No", "Yes"]
    btc = next(m for m in picked if m.condition_id == "0xaaa111")
    assert btc.yes_price == 0.62


def test_market_without_real_numbers_is_dropped() -> None:
    assert select_markets([{"conditionId": "0xdead", "question": "?", "slug": "x"}], now=_NOW) == []


def test_limit_zero_and_category_case() -> None:
    assert select_markets(_raw(), now=_NOW, limit=0) == []
    assert select_markets(_raw(), now=_NOW, categories=("CRYPTO",), limit=10) == []


def test_event_slug_wins_over_market_slug() -> None:
    picked = select_markets(_raw(), now=_NOW, limit=1)
    assert picked[0].url == "https://polymarket.com/event/btc-150k-dec31"


def test_category_falls_back_to_keywords_when_gamma_has_no_tags() -> None:
    row = {
        "conditionId": "0xkw0001",
        "question": "Will the Fed cut rates in December?",
        "slug": "fed-cut-dec",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.55", "0.45"]',
        "volumeNum": 5_000_000,
        "volume24hr": 500_000,
        "liquidityNum": 100_000,
        "oneDayPriceChange": 0.1,
        "endDateIso": "2026-12-01T00:00:00Z",
    }
    picked = select_markets([row], now=_NOW, categories=("macro",))
    assert [m.category for m in picked] == ["macro"]
    assert select_markets([row], now=_NOW, categories=("sports",)) == []


def test_market_past_its_end_date_is_skipped() -> None:
    row = {
        "conditionId": "0xpast001",
        "question": "Will Bitcoin close green today?",
        "slug": "btc-green",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]',
        "volumeNum": 5_000_000,
        "volume24hr": 500_000,
        "liquidityNum": 100_000,
        "oneDayPriceChange": 0.1,
        "endDateIso": "2026-10-20T00:00:00Z",
    }
    assert select_markets([row], now=_NOW, categories=()) == []

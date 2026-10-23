"""Engine contracts: dry-run default, dedupe, rate limit, honest failures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from rugmunchbot.polymarket.config import OUR_WALLET, PolySettings
from rugmunchbot.polymarket.dedupe import Dedupe
from rugmunchbot.polymarket.engine import build_drafts
from rugmunchbot.polymarket.http import PolymarketApiError
from rugmunchbot.polymarket.ratelimit import PostRateLimiter

_FIX = Path(__file__).parent / "fixtures" / "polymarket"
_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _load(name: str):
    return json.loads((_FIX / name).read_text())


class FakeData:
    def __init__(self, *, markets_fail: bool = False, wallet_fail: bool = False):
        self.markets_fail = markets_fail
        self.wallet_fail = wallet_fail
        self.trade_calls: list[str] = []

    async def markets(self, limit: int = 60):
        if self.markets_fail:
            raise PolymarketApiError("gamma down")
        return _load("markets.json")

    async def trades(self, condition_id: str, *, min_cash: float, limit: int = 20):
        self.trade_calls.append(condition_id)
        return _load("trades.json")

    async def traded_count(self, wallet: str):
        return _load("traded_counts.json").get(wallet)

    async def wallet_value(self, wallet: str):
        if self.wallet_fail:
            raise PolymarketApiError("data api down")
        return _load("value.json")[0]["value"]

    async def wallet_positions(self, wallet: str, limit: int = 50):
        if self.wallet_fail:
            raise PolymarketApiError("data api down")
        return _load("positions.json")


def _settings(**overrides) -> PolySettings:
    base: dict = {"categories": ("crypto", "macro"), "max_markets": 3, "min_post_interval_s": 0}
    base.update(overrides)
    return PolySettings(**base)


async def test_dry_run_is_default_and_publisher_is_not_called() -> None:
    sent: list[str] = []

    async def publish(draft) -> None:  # pragma: no cover - must not run
        sent.append(draft.condition_id)

    drafts = await build_drafts(FakeData(), _settings(), now=_NOW, publish=publish)
    assert len(drafts) == 3
    assert all(d.dry_run for d in drafts)
    assert sent == []


async def test_publish_runs_only_with_dry_run_disabled() -> None:
    sent: list[str] = []

    async def publish(draft) -> None:
        sent.append(draft.condition_id)

    drafts = await build_drafts(FakeData(), _settings(dry_run=False), now=_NOW, publish=publish)
    assert [d.condition_id for d in drafts] == sent
    assert all(not d.dry_run for d in drafts)


async def test_dedupe_blocks_a_second_cycle_for_the_same_market() -> None:
    data = FakeData()
    dedupe = Dedupe(ttl_s=3600, clock=lambda: 1000.0)
    first = await build_drafts(data, _settings(), now=_NOW, dedupe=dedupe)
    second = await build_drafts(data, _settings(), now=_NOW, dedupe=dedupe)
    assert len(first) == 3
    assert second == []


async def test_rate_limit_caps_one_card_per_interval() -> None:
    gate = PostRateLimiter(min_interval_s=600, clock=lambda: 100.0)
    drafts = await build_drafts(
        FakeData(), _settings(min_post_interval_s=600), now=_NOW, limiter=gate
    )
    assert len(drafts) == 1


async def test_market_failure_yields_no_fabricated_card() -> None:
    assert await build_drafts(FakeData(markets_fail=True), _settings(), now=_NOW) == []


async def test_wallet_failure_renders_unavailable_not_zero() -> None:
    drafts = await build_drafts(FakeData(wallet_fail=True), _settings(), now=_NOW)
    assert drafts
    assert "value unavailable" in drafts[0].text


async def test_cards_never_carry_a_referral_link_by_default() -> None:
    drafts = await build_drafts(FakeData(), _settings(), now=_NOW)
    assert all("Referral:" not in d.text for d in drafts)


def test_settings_reject_bogus_referral_and_builder_values(monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKET_REFERRAL_URL", "https://example.com/?ref=me")
    monkeypatch.setenv("POLYMARKET_BUILDER_CODE", "not-bytes32")
    settings = PolySettings.from_env()
    assert settings.referral_url is None
    assert settings.builder_code is None
    assert settings.dry_run is True
    assert settings.our_wallet == OUR_WALLET


def test_settings_accept_valid_values(monkeypatch) -> None:
    monkeypatch.setenv("POLYMARKET_REFERRAL_URL", "https://polymarket.com/?r=abc")
    monkeypatch.setenv("POLYMARKET_BUILDER_CODE", "0x" + "cd" * 32)
    monkeypatch.setenv("POLYMARKET_DRY_RUN", "false")
    settings = PolySettings.from_env()
    assert settings.referral_url == "https://polymarket.com/?r=abc"
    assert settings.builder_code == "0x" + "cd" * 32
    assert settings.dry_run is False

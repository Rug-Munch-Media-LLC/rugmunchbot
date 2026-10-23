"""Config, ledger and grammar for /buybot — pure, offline."""

from __future__ import annotations

import pytest

from rugmunchbot.buybot.commands import Intent, apply_intent, parse
from rugmunchbot.buybot.ledger import CardLedger
from rugmunchbot.buybot.models import BuybotConfig
from rugmunchbot.community.config_models import CommunitySettings
from rugmunchbot.community.models import Chain, EmojiConfig, TokenConfig

MINT = "So11111111111111111111111111111111111111112"
EVM = "0x" + "a1" * 20


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


def _configured(**overrides) -> BuybotConfig:
    token = TokenConfig(address=MINT, symbol="BONK", name="Bonk", chain=Chain.SOLANA)
    return BuybotConfig(
        chat_id=-100,
        chain=Chain.SOLANA,
        token=token,
        **{"enabled": True, **overrides},
    )


def test_defaults_are_disabled_and_tokenless() -> None:
    config = BuybotConfig(chat_id=-1)
    assert config.enabled is False
    assert config.token is None
    assert config.fields == ("amount", "usd", "price", "mcap", "liquidity", "risk")


def test_enabling_without_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="set a token"):
        BuybotConfig(chat_id=-1, enabled=True)


def test_fields_are_canonicalised_and_unknown_names_rejected() -> None:
    config = _configured(fields=("risk", "amount", "mcap"))
    assert config.fields == ("amount", "mcap", "risk")
    with pytest.raises(ValueError, match="unknown fields"):
        _configured(fields=("amount", "holders"))


def test_from_community_reuses_setup_models() -> None:
    settings = CommunitySettings(
        chat_id=-100,
        token=TokenConfig(address=EVM, symbol="pepe", name="Pepe", chain=Chain.ETHEREUM),
        emojis=EmojiConfig(),
    )
    config = BuybotConfig.from_community(-100, settings)
    assert config.token is settings.token
    assert config.amounts is settings.amounts
    assert config.enabled is False


def test_ledger_dedupes_inside_the_window_only() -> None:
    clock = FakeClock()
    ledger = CardLedger(clock=clock)
    assert ledger.claim("card:1", window_s=20) is True
    assert ledger.claim("card:1", window_s=20) is False
    clock.tick(21)
    assert ledger.claim("card:1", window_s=20) is True


def test_ledger_caps_cards_per_minute_and_counts_drops() -> None:
    clock = FakeClock()
    ledger = CardLedger(clock=clock)
    for _ in range(10):
        assert ledger.allow_card(-100, max_per_min=10) is True
    assert ledger.allow_card(-100, max_per_min=10) is False
    assert ledger.allow_card(-100, max_per_min=10) is False
    assert ledger.drops(-100) == 2
    assert ledger.allow_card(-200, max_per_min=10) is True
    clock.tick(61)
    assert ledger.allow_card(-100, max_per_min=10) is True


def test_parse_handles_empty_alias_and_errors() -> None:
    assert parse([]) is None
    assert parse(["amounts", "0.1,1"]) == Intent("presets", ("0.1,1",))
    with pytest.raises(ValueError, match="unknown option"):
        parse(["nope", "x"])
    with pytest.raises(ValueError, match="presets"):
        parse(["presets"])


def test_apply_token_presets_and_emoji() -> None:
    config = BuybotConfig(chat_id=-100)
    config = apply_intent(config, parse(["token", "solana", MINT, "bonk"]))
    assert config.token is not None
    assert config.token.symbol == "BONK"
    config = apply_intent(config, parse(["presets", "0.25,2"]))
    assert config.presets == [0.25, 2.0]
    config = apply_intent(config, parse(["emoji", "🔥", "Ape"]))
    assert config.emojis.buy.emoji == "🔥"
    assert config.emojis.buy.label == "Ape"
    config = apply_intent(config, parse(["cap", "5"]))
    assert config.max_cards_per_min == 5


def test_apply_rejects_a_bad_address_and_keeps_config_untouched() -> None:
    config = BuybotConfig(chat_id=-100)
    with pytest.raises(ValueError, match="solana address"):
        apply_intent(config, parse(["token", "solana", "not-an-address"]))
    assert config.token is None


def test_apply_rejects_unknown_chain() -> None:
    with pytest.raises(ValueError, match="chain"):
        apply_intent(BuybotConfig(chat_id=-100), parse(["token", "dogechain", MINT]))

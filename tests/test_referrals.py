"""Referral helper contracts — tier mapping, disclosure, missing config."""

from __future__ import annotations

import pytest

from rugmunchbot.config import JUPITER_FEE_ACCOUNT, JUPITER_ULTRA_ACCOUNT
from rugmunchbot.formatters._base._links import footer_links, scan_shill_footer
from rugmunchbot.referrals import (
    DISCLOSURE,
    PLATFORM_FEE_BPS,
    STANDARD,
    ULTRA,
    disclosure_text,
    jupiter_referral,
    jupiter_swap_params,
    polymarket_referral,
    referral_line,
    resolve_tier,
)

ADDR = "So11111111111111111111111111111111111111112"


@pytest.mark.parametrize(
    ("purpose", "tier"),
    [
        ("scan", STANDARD),
        ("card", STANDARD),
        ("price", STANDARD),
        ("trending", STANDARD),
        ("alert", STANDARD),
        ("news", STANDARD),
        ("content", STANDARD),
        ("deep", ULTRA),
        ("deepscan", ULTRA),
        ("premium", ULTRA),
        ("watch", ULTRA),
        ("DeepScan", ULTRA),
        ("  scan  ", STANDARD),
        ("", STANDARD),
        ("unknown-surface", STANDARD),
    ],
)
def test_resolve_tier(purpose: str, tier: str) -> None:
    assert resolve_tier(purpose) == tier


def test_regular_surface_uses_standard_account() -> None:
    ref = jupiter_referral(token=ADDR, purpose="scan")
    assert ref.tier == STANDARD
    assert ref.account == JUPITER_FEE_ACCOUNT
    assert ref.account != JUPITER_ULTRA_ACCOUNT
    assert ADDR in (ref.url or "")


def test_deep_surface_uses_ultra_account() -> None:
    ref = jupiter_referral(token=ADDR, purpose="deepscan")
    assert ref.tier == ULTRA
    assert ref.account == JUPITER_ULTRA_ACCOUNT


def test_non_solana_chain_yields_no_link_with_reason() -> None:
    ref = jupiter_referral(token="0xabc", chain="ethereum", purpose="scan")
    assert ref.available is False
    assert ref.url is None
    assert ref.account is None
    assert ref.reason == "jupiter:unsupported-chain:ethereum"


def test_polymarket_is_null_with_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RMI_REF_POLYMARKET", raising=False)
    ref = polymarket_referral()
    assert ref.available is False
    assert ref.url is None
    assert ref.as_dict()["url"] is None
    assert ref.as_dict()["reason"] == "polymarket:not-configured"


def test_polymarket_accepts_https_override_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RMI_REF_POLYMARKET", "https://polymarket.com/?r=rmi")
    assert polymarket_referral().url == "https://polymarket.com/?r=rmi"
    monkeypatch.setenv("RMI_REF_POLYMARKET", "javascript:alert(1)")
    assert polymarket_referral().url is None
    assert polymarket_referral().reason == "polymarket:invalid-url"


def test_disclosure_present_only_when_link_available() -> None:
    assert disclosure_text(jupiter_referral(token=ADDR)) == DISCLOSURE
    assert disclosure_text(jupiter_referral(chain="bsc")) == ""
    assert "fee" in DISCLOSURE.lower()


def test_referral_line_carries_disclosure_and_tier_label() -> None:
    line = referral_line(token=ADDR, purpose="scan")
    assert DISCLOSURE in line
    assert "Trade on Jupiter" in line
    assert "Ultra" not in line
    assert "jup.ag/swap/SOL-" in line
    assert "?ref=" not in line  # Jupiter attribution is on-chain, not a query code
    ultra = referral_line(token=ADDR, purpose="deepscan")
    assert "Ultra" in ultra
    assert referral_line(chain="arbitrum") == ""


def test_swap_params_expose_public_account_only() -> None:
    params = jupiter_swap_params(purpose="deepscan")
    assert params["referralAccount"] == JUPITER_ULTRA_ACCOUNT
    assert params["platformFeeBps"] == PLATFORM_FEE_BPS == 50
    assert set(params) == {"referralAccount", "platformFeeBps"}
    assert jupiter_swap_params(chain="bsc") == {}


def test_footer_gets_referral_and_disclosure() -> None:
    generic = footer_links(purpose="trending")
    assert DISCLOSURE in generic
    assert "jup.ag/swap/SOL-USDC" in generic
    token_footer = footer_links(ADDR, "solana", purpose="scan")
    assert DISCLOSURE in token_footer
    assert ADDR in token_footer
    # EVM: Jupiter is Solana-only, but GMGN is a real configured referral —
    # so a disclosure is still required, exactly once.
    evm = footer_links("0xabc", "ethereum", purpose="scan")
    assert evm.count(DISCLOSURE) == 1
    assert "jup.ag" not in evm


def test_scan_shill_footer_is_tier_aware() -> None:
    assert "Ultra" not in scan_shill_footer(purpose="scan")
    assert "Ultra" in scan_shill_footer(purpose="deepscan")


def test_no_private_key_material_is_exposed() -> None:
    for tier in ("scan", "deepscan"):
        ref = jupiter_referral(token=ADDR, purpose=tier)
        for value in ref.as_dict().values():
            assert "api_key" not in str(value).lower()
        assert ref.account
        assert len(ref.account) == 44  # base58 pubkey, not a key

"""Test wave 16 — scan-report field extractors + scam-watch classification.

Contracts read from source (2026-08-22). Pure functions only: no network,
no DB, no Telegram.
"""

from __future__ import annotations

from types import SimpleNamespace

from rugmunchbot.channels.scam_watch._classify import (
    classify_entity,
    extract_token_addresses,
)
from rugmunchbot.formatters._scan_report_common._fields import (
    _bundle_wallet_count,
    _cex_funded_pct,
    _fake_volume_pct,
    _float_or_none,
    _freeze_auth,
    _fresh_wallet_pct,
    _holders_count,
    _lp_lock_pct,
    _mev_note,
    _mint_auth,
    _tax_pct_str,
    _token_2022_flag,
    _volume_up_price_flat_churn,
)

_EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
_SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ── scam_watch classification ────────────────────────────────────────────────


def test_classify_entity_kinds() -> None:
    assert classify_entity("") == ""
    assert classify_entity("$PEPE") == "ticker"
    assert classify_entity(_EVM) == "evm"
    assert classify_entity(_SOL) == "solana"
    assert classify_entity("just a word") == ""


def test_extract_token_addresses_splits_and_dedupes() -> None:
    article = SimpleNamespace(entities=["$pepe", "$PEPE", _EVM, _EVM.lower(), _SOL, "noise"])
    addresses, tickers = extract_token_addresses(article)
    # Dedupe is on the EXACT string — a checksummed address and its
    # all-lowercase twin are two entries (documented quirk).
    assert addresses == [
        {"address": _EVM, "chain": "ethereum"},
        {"address": _EVM.lower(), "chain": "ethereum"},
        {"address": _SOL, "chain": "solana"},
    ]
    # Tickers are $-stripped; case variants stay distinct.
    assert tickers == ["pepe", "PEPE"]


def test_extract_token_addresses_empty_entities() -> None:
    assert extract_token_addresses(SimpleNamespace(entities=None)) == ([], [])


# ── field extractors ─────────────────────────────────────────────────────────


def test_lp_lock_pct_prefers_sentinel_then_legacy_shapes() -> None:
    sentinel = {"sentinel": {"free": {"liquidity_verification": {"total_locked_percentage": 87.5}}}}
    assert _lp_lock_pct(sentinel) == 87.5
    assert _lp_lock_pct({"lp_locked": 0.42}) == 42.0  # 0-1 fraction scaled
    assert _lp_lock_pct({"lp_locked": 65}) == 65.0  # already 0-100
    assert _lp_lock_pct({}) is None
    assert _lp_lock_pct({"lp_locked": "junk"}) is None


def test_mint_auth_probe_chain() -> None:
    assert _mint_auth({"mintable": True}) is True
    assert _mint_auth({"risk_signals": {"mintable": False}}) is False
    assert _mint_auth({"mint_authority": "SomeWallet"}) is True
    # Junk authority strings are treated as unknown, not dangerous.
    assert _mint_auth({"mint_authority": "none"}) is None
    assert _mint_auth({}) is None
    assert _mint_auth({"mintable": "yes"}) is None  # non-bool -> unknown


def test_freeze_auth_probe_chain() -> None:
    assert _freeze_auth({"freezable": True}) is True
    assert _freeze_auth({"risk_signals": {"freezable": False}}) is False
    assert _freeze_auth({"can_blacklist": True}) is True
    assert _freeze_auth({"freeze_authority": "Auth"}) is True
    assert _freeze_auth({"freeze_authority": "unknown"}) is None
    assert _freeze_auth({}) is None


def test_float_or_none_basics() -> None:
    assert _float_or_none("3.5") == 3.5
    assert _float_or_none(None) is None
    assert _float_or_none("junk") is None


def test_bundle_wallet_count_priority_order() -> None:
    assert _bundle_wallet_count({"launch_buyers": 7}) == 7
    blocks = {"cluster_blocks": [{"buyer_count": 3}, {"buyer_count": 2}, "junk"]}
    assert _bundle_wallet_count(blocks) == 5
    assert _bundle_wallet_count({"common_funders": ["a", "b"]}) == 2
    # Zero-sum blocks fall through to funders/None — never a fabricated 0.
    assert _bundle_wallet_count({"cluster_blocks": [{"buyer_count": 0}]}) is None
    assert _bundle_wallet_count({}) is None


def test_cex_funded_pct_probes_every_provider_shape() -> None:
    assert _cex_funded_pct({"wallet_metrics": {"result": "ok", "cex_funded_pct": 12.5}}) == 12.5
    # An unavailable engine result is skipped, not trusted.
    assert (
        _cex_funded_pct(
            {
                "wallet_metrics": {"result": "unavailable", "cex_funded_pct": 99},
                "holder_analysis": {"exchange_funded_pct": 30},
            }
        )
        == 30.0
    )
    assert _cex_funded_pct({"funding_analysis": {"exchange_funded_pct": 5}}) == 5.0
    assert _cex_funded_pct({"evidence": {"funding_analysis": {"exchange_funded_pct": 8}}}) == 8.0
    assert _cex_funded_pct({"exchange_funded_pct": "9.5"}) == 9.5  # legacy flat key
    assert _cex_funded_pct({}) is None


def test_holders_count_provider_shapes() -> None:
    assert _holders_count({"gmgn": {"holders": {"holder_count": 1234}}}) == 1234
    assert _holders_count({"sentinel": {"free": {"holder_analysis": {"total_holders": 55}}}}) == 55
    assert _holders_count({"holder_analysis": {"holder_count": 10}}) == 10
    assert _holders_count({"holders": "77"}) == 77
    assert _holders_count({}) is None
    assert _holders_count({"holders": "junk"}) is None


def test_fake_volume_pct_wash_dict_and_ratio_math() -> None:
    assert _fake_volume_pct({"wash_trading": {"fake_volume_pct": 33.3}}) == 33.3
    # ratio > 2 maps linearly: (rv - 2) * 20, clamped to 0-100.
    assert _fake_volume_pct({"volume_real_ratio": 3.0}) == 20.0
    assert _fake_volume_pct({"volume_real_ratio": 8.0}) == 100.0
    assert _fake_volume_pct({"volume_real_ratio": 1.5}) == 0.0
    assert _fake_volume_pct({}) is None


def test_volume_up_price_flat_churn_rule() -> None:
    assert _volume_up_price_flat_churn({"volume_24h": 250_000, "price_change_24h": 1.2}) == (
        "vol up, price flat"
    )
    assert _volume_up_price_flat_churn({"volume_24h": 50_000, "price_change_24h": 1.2}) is None
    assert _volume_up_price_flat_churn({"volume_24h": 250_000, "price_change_24h": 5.0}) is None
    assert _volume_up_price_flat_churn({}) is None


def test_fresh_wallet_pct_sources() -> None:
    assert _fresh_wallet_pct({"wallet_metrics": {"result": "ok", "fresh_wallet_pct": 41.0}}) == 41.0
    assert _fresh_wallet_pct({"fresh_wallet_pct": "12"}) == 12.0
    assert _fresh_wallet_pct({}) is None
    assert _fresh_wallet_pct({"fresh_wallet_pct": "junk"}) is None


def test_tax_pct_str_fraction_and_percent() -> None:
    assert _tax_pct_str(0.05) == "5.0%"
    assert _tax_pct_str(12) == "12.0%"
    assert _tax_pct_str(None) == "N/A"
    assert _tax_pct_str("junk") == "N/A"


def test_token_2022_flag_detection_paths() -> None:
    msg = "Token-2022 program — extensions may hide transfer hooks / freeze"
    assert _token_2022_flag({"token_2022": True}) == msg
    assert _token_2022_flag({"token_program": "SPL-Token-2022"}) == msg
    assert (
        _token_2022_flag(
            {"sentinel": {"free": {"metadata_fingerprint": {"token_program": "token-2022"}}}}
        )
        == msg
    )
    assert _token_2022_flag({"token_program": "spl-token"}) is None
    assert _token_2022_flag({}) is None


def test_mev_note_levels_and_warnings() -> None:
    high = {"sentinel": {"free": {"mev_detection": {"risk_level": "HIGH"}}}}
    assert _mev_note(high) == "MEV risk <b>HIGH</b> — sandwich / front-run exposure"
    warned = {"sentinel": {"free": {"mev_detection": {"warnings": ["sandwich risk seen"]}}}}
    assert _mev_note(warned) == "MEV: sandwich risk seen"
    low = {"sentinel": {"free": {"mev_detection": {"risk_level": "low"}}}}
    assert _mev_note(low) is None
    assert _mev_note({}) is None

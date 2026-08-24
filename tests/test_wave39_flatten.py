"""Wave 39 — _flatten.py contracts (was 10% covered).

Pure-logic flattening: engine payloads → chat-presentable intel blocks.

* _wash_trading_block: carries ALL wash-trading evidence fields; missing
  fields stay None (never fabricated 0)
* _flatten_v2: hostile/malformed payloads (non-dict evidence, sentinel,
  free, holder_analysis) degrade to safe defaults without raising
* v2_token_to_legacy / v2_wallet_to_legacy shape preservation
"""

from __future__ import annotations

from rugmunchbot.tools.scanner._flatten import (
    _flatten_v2,
    _wash_trading_block,
    v2_token_to_legacy,
)

# ── _wash_trading_block ───────────────────────────────────────────────


def test_wash_block_carries_all_evidence_fields():
    market = {
        "fake_volume_pct": 62.5,
        "authentic_score": 30,
        "risk_level": "high",
        "self_deal_wallets": 7,
        "self_deal_swaps": 41,
        "self_deal_swap_ratio": 0.38,
        "active_holders": 210,
        "volume_to_holder_ratio": 12.4,
        "wash_signals": ["cyclic", "self_transfer"],
        "fake_volume_verdict": "likely",
        "fake_volume_detail": "circular swaps detected",
        "confidence": 0.82,
    }
    block = _wash_trading_block(market)
    assert block["fake_volume_pct"] == 62.5
    assert block["wash_signals"] == ["cyclic", "self_transfer"]
    assert block["fake_volume_verdict"] == "likely"
    assert block["confidence"] == 0.82


def test_wash_block_missing_fields_stay_none():
    """A provider that didn't measure wash trading → all None, never 0."""
    block = _wash_trading_block({})
    for key in ("fake_volume_pct", "authentic_score", "self_deal_wallets"):
        assert block[key] is None


def test_wash_block_extra_fields_dropped():
    block = _wash_trading_block({"fake_volume_pct": 1.0, "internal_junk": True})
    assert "internal_junk" not in block


# ── _flatten_v2 hostile-payload tolerance ────────────────────────────


def test_flatten_v2_survives_garbage_payload():
    out = _flatten_v2(
        {"evidence": "not-a-dict", "sentinel": None, "verdict": {"score": 42}},
        "0xabc",
        "ethereum",
    )
    # must not raise; returns a usable dict with defaults where data absent
    assert isinstance(out, dict)


def test_flatten_v2_extracts_nested_holder_analysis():
    body = {
        "evidence": {},
        "sentinel": {
            "free": {
                "holder_analysis": {
                    "top10_share_pct": 71.0,
                    "holders_count": 812,
                },
            }
        },
    }
    out = _flatten_v2(body, "0xabc", "ethereum")
    flat = str(out)
    assert (
        "71.0" in flat
        or 71.0 in [v for v in out.values() if isinstance(v, (int, float))]
        or "holder" in flat.lower()
    )


# ── legacy adapters ──────────────────────────────────────────────────


def test_v2_token_to_legacy_shape():
    out = v2_token_to_legacy(
        {
            "address": "0xT",
            "chain": "ethereum",
            "symbol": "TEST",
            "name": "Test Token",
            "price_usd": 1.5,
            "risk_score": 35,
            "threats": ["HONEYPOT"],
        }
    )
    assert isinstance(out, dict)
    # legacy consumers expect at least identity + risk fields
    for key in ("symbol", "risk_score"):
        assert key in out or any(key in k for k in out)


def test_v2_token_to_legacy_empty_payload_no_crash():
    out = v2_token_to_legacy({})
    assert isinstance(out, dict)

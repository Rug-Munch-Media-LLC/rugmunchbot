"""Wallet intelligence classification tests."""

from rugmunchbot.tools.wallet_intelligence import classify_sample, classify_wallet


def test_cex_wallet_identified():
    r = classify_wallet("0x28c6c06298d514db089934071355e5743bf21d60", percentage=15)
    assert r["primary"] == "CEX"
    assert "Binance" in (r["cex_label"] or "")


def test_whale_detected():
    r = classify_wallet("0x1234567890abcdef", percentage=8)
    assert r["primary"] == "WHALE"


def test_organic_default():
    r = classify_wallet("0xunknownwallet", percentage=1)
    assert r["primary"] == "ORGANIC"


def test_sniper_detection():
    r = classify_wallet("0xsniper", first_seen_block=100, lp_add_block=99)
    assert "SNIPER" in r["tags"]
    assert r["primary"] == "SNIPER"


def test_insider_detection():
    r = classify_wallet("0xinsider", first_seen_block=50, lp_add_block=100)
    assert "INSIDER" in r["tags"]


def test_classify_sample_aggregates():
    holders = [
        {"address": "0x28c6c06298d514db089934071355e5743bf21d60", "percentage": 20},
        {"address": "0xrandom1", "percentage": 10},
        {"address": "0xrandom2", "percentage": 8},
        {"address": "0xrandom3", "percentage": 7},
    ]
    result = classify_sample(holders)
    assert result["total_classified"] == 4
    assert result["profile_counts"].get("CEX", 0) >= 1
    assert "Binance" in str(result["cex_breakdown"])

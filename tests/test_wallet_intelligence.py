"""Wallet intelligence classification tests.

``cex_addresses_verified.json`` (208k labels) is a PRIVATE corpus that is
deliberately gitignored (``src/rugmunchbot/tools/README.md``), so a clean
checkout — and therefore CI — has an empty CEX database. These tests inject
the single address the contract needs through the public ``CEX_ADDRESSES``
table instead of depending on a file that must never be committed.
"""

import pytest

from rugmunchbot.tools import cex_database
from rugmunchbot.tools.wallet_intelligence import classify_sample, classify_wallet

BINANCE_HOT = "0x28c6c06298d514db089934071355e5743bf21d60"


@pytest.fixture
def binance_hot() -> object:
    """Register one Binance hot wallet for the test, then restore the table."""
    entry = {"exchange": "Binance", "type": "hot", "name_tag": "Binance 15"}
    previous = cex_database.CEX_ADDRESSES.get(BINANCE_HOT)
    cex_database.CEX_ADDRESSES[BINANCE_HOT] = entry
    try:
        yield entry
    finally:
        if previous is None:
            cex_database.CEX_ADDRESSES.pop(BINANCE_HOT, None)
        else:
            cex_database.CEX_ADDRESSES[BINANCE_HOT] = previous


def test_cex_wallet_identified(binance_hot: object):
    r = classify_wallet(BINANCE_HOT, percentage=15)
    assert r["primary"] == "CEX", "CEX must outrank WHALE (product priority)"
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


def test_classify_sample_aggregates(binance_hot: object):
    holders = [
        {"address": BINANCE_HOT, "percentage": 20},
        {"address": "0xrandom1", "percentage": 10},
        {"address": "0xrandom2", "percentage": 8},
        {"address": "0xrandom3", "percentage": 7},
    ]
    result = classify_sample(holders)
    assert result["total_classified"] == 4
    assert result["profile_counts"].get("CEX", 0) >= 1
    assert "Binance" in str(result["cex_breakdown"])

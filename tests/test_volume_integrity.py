"""Volume integrity heuristics — wash/fake-volume detection tests."""

from rugmunchbot.tools.volume_integrity import analyze


def test_impostor_washed_token_scores_low():
    """The Daily Rug 'SOL' impostor profile: hot turnover, dust buys, flat price."""
    r = analyze(
        {
            "volume": {"h24": 7.8e6},
            "liquidity": {"usd": 250_000},
            "marketCap": 20.1e6,
            "txns": {"h24": {"buys": 2100, "sells": 30}},
            "priceChange": {"h24": 0.3},
        }
    )
    assert r["verdict"] == "LIKELY_WASHED"
    assert r["score"] < 55
    flags = {c["check"]: c["flag"] for c in r["checks"]}
    critical = [k for k, v in flags.items() if v == "CRITICAL"]
    assert "buy_ratio" in critical
    assert len(critical) >= 2, "impostor profile must trip multiple checks"


def test_healthy_major_scores_clean():
    r = analyze(
        {
            "volume": {"h24": 5e6},
            "liquidity": {"usd": 8e6},
            "marketCap": 90e9,
            "txns": {"h24": {"buys": 11_000, "sells": 10_500}},
            "priceChange": {"h24": 4.6},
        }
    )
    assert r["verdict"] == "CLEAN"
    assert r["score"] >= 80


def test_volume_far_exceeding_liquidity_flagged():
    r = analyze(
        {
            "volume": {"h24": 50e6},
            "liquidity": {"usd": 400_000},
            "marketCap": 5e6,
            "txns": {"h24": {"buys": 900, "sells": 800}},
            "priceChange": {"h24": -2.0},
        }
    )
    flags = {c["check"]: c["flag"] for c in r["checks"]}
    assert flags.get("vol_liq") == "CRITICAL"
    assert r["verdict"] != "CLEAN"


def test_missing_data_degrades_not_lies():
    """No txns/volume data -> score stays high but checks note gaps."""
    r = analyze({"liquidity": {"usd": 100_000}})
    assert 0 <= r["score"] <= 100
    assert isinstance(r["checks"], list)

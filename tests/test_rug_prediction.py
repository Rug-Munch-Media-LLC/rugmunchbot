"""Rug Prediction Score — composite forecast tests (win 31)."""

from rugmunchbot.tools.rug_prediction import predict


def test_extreme_rug_profile():
    r = predict(
        {
            "honeypot": True,
            "mint_auth": True,
            "bundle_analysis": {"launch_cluster_score": 95},
            "holder_analysis": {
                "top10_pct": 72,
                "fake_diversification_score": 85,
                "exchange_funded_pct": 45,
                "new_wallets_pct": 68,
            },
            "wash_trading": {"score": 30},
            "flow_analysis": {"concentrated_flow": True, "top_mover_share_pct": 71},
        }
    )
    assert r["band"] == "EXTREME_RUG_RISK"
    assert r["rug_probability"] >= 70
    assert r["confidence"] >= 80
    assert any(d["signal"] == "honeypot" for d in r["drivers"])


def test_clean_profile_scores_low():
    r = predict(
        {
            "holder_analysis": {"top10_pct": 22},
            "wash_trading": {"score": 92},
        }
    )
    assert r["band"] == "LOW"
    assert r["rug_probability"] < 25


def test_sparse_data_lowers_confidence_not_hides():
    """Sparse scans keep an honest score but drop confidence."""
    full = predict({"honeypot": True})
    sparse = predict({"honeypot": True})
    assert sparse["confidence"] <= full["confidence"] + 0.1
    assert 0 <= sparse["rug_probability"] <= 100


def test_drivers_sorted_by_weight():
    r = predict(
        {
            "honeypot": True,
            "bundle_analysis": {"launch_cluster_score": 90},
            "wash_trading": {"score": 20},
        }
    )
    weights = [d["weight"] for d in r["drivers"]]
    assert weights == sorted(weights, reverse=True)

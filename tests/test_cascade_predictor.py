"""Cascade predictor scoring tests (win 34)."""

from rugmunchbot.tools.cascade_predictor import analyze


def _row(asset, mark, oi, dist_pct=5.0):
    return {
        "asset": asset,
        "weighted_mark": mark,
        "total_oi_usd": oi,
        "long_liq_10x": mark * (1 - dist_pct / 100),
        "short_liq_10x": mark * (1 + dist_pct / 100),
        "venue_count": 3,
    }


def test_imminent_when_price_at_liq_level():
    rows = [_row("PEPE", 0.000012, 5e6, dist_pct=0.0)]
    out = analyze(rows)
    assert out[0]["band"] == "IMMINENT"
    assert out[0]["near_side"] in ("LONGS", "SHORTS")


def test_clear_when_far_from_levels():
    rows = [_row("BTC", 77000, 2e9, dist_pct=10.0)]
    out = analyze(rows)
    assert out[0]["band"] == "CLEAR"


def test_higher_oi_boosts_risk_at_same_distance():
    near_small = analyze([_row("SMALL", 100, 1e6, dist_pct=3)])[0]
    near_big = analyze([_row("BIG", 100, 500e6, dist_pct=3)])[0]
    assert near_big["cascade_risk"] > near_small["cascade_risk"]


def test_ranked_worst_first():
    rows = [
        _row("FAR", 100, 100e6, dist_pct=15),
        _row("NEAR", 100, 100e6, dist_pct=0.5),
    ]
    out = analyze(rows)
    assert out[0]["asset"] == "NEAR"

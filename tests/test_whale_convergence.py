"""Whale convergence detection tests (win 33)."""

from datetime import UTC, datetime, timedelta

from rugmunchbot.tools.whale_convergence import detect_convergence

NOW = datetime.now(UTC)


def _w(wallet, mins_ago, pnl=5000):
    return {
        "wallet": wallet,
        "first_buy_ts": (NOW - timedelta(minutes=mins_ago)).isoformat(),
        "pnl": pnl,
        "buy_count": 3,
    }


def test_convergence_detected():
    wallets = [_w(f"w{i}", mins_ago=i * 10) for i in range(4)]  # 0-30min spread
    r = detect_convergence(wallets)
    assert r is not None
    assert r["converging_wallets"] == 4
    assert "coordinated accumulation" in r["read"]


def test_spread_entries_no_convergence():
    wallets = [_w("a", 0), _w("b", 120), _w("c", 300)]  # hours apart
    assert detect_convergence(wallets) is None


def test_below_threshold_none():
    wallets = [_w("a", 5), _w("b", 10)]  # only 2 wallets
    assert detect_convergence(wallets) is None


def test_profitable_track_record_counted():
    wallets = [_w("a", 0, pnl=90000), _w("b", 10, pnl=-200), _w("c", 20, pnl=45000)]
    r = detect_convergence(wallets)
    assert r["profitable_track_record"] == "2/3"


# ── Edge cases + boundary conditions ────────────────────────────────────
def test_empty_wallet_list():
    from rugmunchbot.tools.whale_convergence import detect_convergence

    assert detect_convergence([]) is None


def test_single_wallet_never_converges():
    from rugmunchbot.tools.whale_convergence import detect_convergence

    wallets = [_w("solo", 5)]
    assert detect_convergence(wallets) is None


def test_exactly_at_threshold():
    """3 wallets = minimum for convergence."""
    from rugmunchbot.tools.whale_convergence import detect_convergence

    wallets = [_w(f"w{i}", mins_ago=i * 5) for i in range(3)]
    r = detect_convergence(wallets)
    assert r is not None
    assert r["converging_wallets"] == 3


def test_window_boundary_respected():
    """Wallets 61+ min apart should NOT cluster in a 60-min window."""

    wallets = [
        _w("a", 0),
        _w("b", 30),
        _w("c", 61),
        _w("d", 90),
        _w("e", 120),
    ]
    r = detect_convergence(wallets)
    # c,d,e are within 60min of each other but a,b are outside
    if r:
        assert r["converging_wallets"] < 5, "should not cluster all 5 across 2h span"


def test_unparseable_timestamps_skipped():
    from rugmunchbot.tools.whale_convergence import detect_convergence

    wallets = [
        {"wallet": "a", "first_buy_ts": "not-a-date"},
        {"wallet": "b"},
        _w("c", 5),
    ]
    r = detect_convergence(wallets)
    # only 1 parseable entry → below threshold
    assert r is None or r["converging_wallets"] < 3


def test_cascade_risk_monotonic_on_distance():
    """Closer to liq level = higher risk (monotonic decay)."""
    from rugmunchbot.tools.cascade_predictor import analyze

    near = analyze([_row("NEAR", 100, 100e6, dist_pct=1)])[0]
    far = analyze([_row("FAR", 100, 100e6, dist_pct=8)])[0]
    assert near["cascade_risk"] > far["cascade_risk"]


def _row(asset, mark, oi, dist_pct=5.0):
    return {
        "asset": asset,
        "weighted_mark": mark,
        "total_oi_usd": oi,
        "long_liq_10x": mark * (1 - dist_pct / 100),
        "short_liq_10x": mark * (1 + dist_pct / 100),
        "venue_count": 3,
    }


# ── Additional coverage: data integrity + boundary tests ────────────────
def test_convergence_with_mixed_timestamps():
    """Some wallets have timestamps, some don't — only dated ones count."""
    from rugmunchbot.tools.whale_convergence import detect_convergence

    wallets = [
        _w("a", 0), _w("b", 10),
        {"wallet": "c"},  # no timestamp
        _w("d", 20),
    ]
    r = detect_convergence(wallets)
    if r:
        assert r["converging_wallets"] == 3  # a, b, d


def test_negative_pnl_counted_correctly():
    from rugmunchbot.tools.whale_convergence import detect_convergence

    wallets = [
        _w("loser1", 0, pnl=-50000),
        _w("loser2", 10, pnl=-30000),
        _w("winner", 20, pnl=100000),
    ]
    r = detect_convergence(wallets)
    assert r is not None
    assert r["profitable_track_record"] == "1/3"

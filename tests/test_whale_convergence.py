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

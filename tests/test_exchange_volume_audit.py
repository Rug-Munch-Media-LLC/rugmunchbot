"""Venue-level wash detection tests."""

from rugmunchbot.tools.exchange_volume_audit import audit_venues


def _mk(ex, depth, vol_usd, trust, lot, ts="2026-08-23T15:00:00Z"):
    return {
        "market": {"name": ex, "depth_usd": depth},
        "converted_volume": {"usd": vol_usd},
        "trust_score": trust,
        "volume": lot,
        "last_traded_at": ts,
    }


def test_sloshing_venue_flagged():
    tickers = [
        _mk("CleanEx", 50e6, 20e6, "green", 123.45),
        _mk("SloshEx", 90_000, 30e6, "red", 500.0),
        _mk("SloshEx", 80_000, 28e6, "unverified", 500.0),
        _mk("SloshEx", 70_000, 31e6, "red", 500.0),
        _mk("SloshEx", 60_000, 29e6, "red", 500.0),
        _mk("SloshEx", 50_000, 27e6, "red", 500.0),
    ]
    r = audit_venues(tickers)
    by = {v["exchange"]: v for v in r["venues"]}
    assert by["SloshEx"]["verdict"] == "WASH_RISK"
    assert by["SloshEx"]["score"] < 50
    assert any("identical lot" in n for n in by["SloshEx"]["notes"])
    assert by["CleanEx"]["verdict"] == "TRUSTED"


def test_ranked_worst_first():
    tickers = [_mk("GoodEx", 10e6, 1e6, "green", 10.0), _mk("BadEx", 1_000, 9e6, "red", 77.0)]
    r = audit_venues(tickers)
    assert r["venues"][0]["exchange"] == "BadEx"

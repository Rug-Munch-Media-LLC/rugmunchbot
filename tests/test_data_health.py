"""Data-source health aggregator tests."""

from rugmunchbot.tools.data_health import record


def test_record_tracks_ema():
    record("test_src", True, 100.0)
    record("test_src", True, 200.0)
    # EMA should be between the two values
    from rugmunchbot.tools import data_health as dh

    assert 100 < dh._latency_ema.get("test_src", 0) < 200


def test_record_tracks_errors():
    record("err_src", False, 5000.0)
    from rugmunchbot.tools import data_health as dh

    assert dh._error_counts.get("err_src", 0) >= 1

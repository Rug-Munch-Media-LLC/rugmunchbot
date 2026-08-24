"""Simulated entry/exit backtest line tests (win 40)."""

from rugmunchbot.tools.simulated_entry import render_simulated_line, simulate_entry


def test_entry_math_correct():
    sim = simulate_entry(1.50, {"price_change_24h": -20.0})
    w = sim["24h"]
    # price was 1.875 twenty-four hours ago; $1k then = $800 now
    assert w["entry_price"] == 1.875
    assert w["now_worth_usd"] == 800.0


def test_missing_windows_omitted_never_faked():
    sim = simulate_entry(1.0, {"price_change_1h": 2.0})  # no 6h/24h data
    assert "1h" in sim
    assert "6h" not in sim
    assert "24h" not in sim


def test_zero_factor_guarded():
    sim = simulate_entry(1.0, {"price_change_24h": -100.0})
    assert "24h" not in sim, "went-to-zero cannot back-compute"


def test_wash_verdict_gates_rendering():
    sim = simulate_entry(1.0, {"price_change_1h": 5.0})
    line = render_simulated_line(sim, wash_verdict="LIKELY_WASHED")
    assert "withheld" in line.lower()
    clean = render_simulated_line(sim, wash_verdict="CLEAN")
    assert "$1k entry simulation" in clean

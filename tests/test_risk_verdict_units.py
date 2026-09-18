"""Unit tests for the pure risk/verdict logic in holders + investigate.

These are the bot's core judgement functions: concentration risk scoring
(holders) and the investigation verdict ladder (investigate). Pure — no
network, no Telegram.
"""

from rugmunchbot.formatters import footer_links
from rugmunchbot.handlers.holders import _holder_risk
from rugmunchbot.handlers.investigate import _determine_verdict
from rugmunchbot.handlers.market._render import (
    _change_emoji,
    _fmt_usd,
    _mover_link,
    _render_movers_lines,
    _whale_flow_summary,
)

# ── _holder_risk ───────────────────────────────────────────────────────────


def test_holder_risk_critical_single_wallet():
    risk, flags = _holder_risk(30.0, 70.0, 50)
    assert risk == "critical"
    assert any("Single wallet holds >20%" in f for f in flags)
    assert any("Top 10 hold >60%" in f for f in flags)
    assert any("Very few holders" in f for f in flags)


def test_holder_risk_high_band_and_boundary_20():
    assert _holder_risk(12.0, 45.0, 800)[0] == "high"
    # Exactly 20 is NOT >20 — falls to the high band.
    assert _holder_risk(20.0, 30.0, 800)[0] == "high"


def test_holder_risk_medium_band_and_boundary_10():
    assert _holder_risk(7.5, 30.0, 300)[0] == "medium"
    assert _holder_risk(10.0, 30.0, 300)[0] == "medium"


def test_holder_risk_low_band_distribution_flag():
    risk, flags = _holder_risk(3.0, 20.0, 5000)
    assert risk == "low"
    assert any("good distribution" in f for f in flags)
    assert len(flags) == 1  # only the top-10 flag; no holder-count flag


def test_holder_risk_low_holder_count_flag():
    _, flags = _holder_risk(3.0, 20.0, 300)
    assert any("Low holder count" in f for f in flags)


def test_holder_risk_top10_boundary_25_is_good():
    _, flags = _holder_risk(1.0, 25.0, 1000)
    assert any("good distribution" in f for f in flags)


# ── _determine_verdict ─────────────────────────────────────────────────────


def test_verdict_honeypot_forces_critical():
    verdict, _ = _determine_verdict({"honeypot": True, "risk_score": 1}, {})
    assert verdict == "CRITICAL"


def test_verdict_danger_message_has_counts():
    _, msg = _determine_verdict({"risk_score": 85, "threats": ["a", "b"]}, {"flags": ["x"]})
    assert "DANGER" in msg
    assert "2 threats" in msg
    assert "1 wallet flags" in msg


def test_verdict_bundle_lifts_medium_to_high():
    verdict, msg = _determine_verdict({"risk_score": 50, "bundle_detected": True}, {})
    assert verdict == "HIGH"  # 50 + 15 = 65
    assert "RISKY" in msg


def test_verdict_fresh_wallets_lift_to_medium_boundary():
    verdict, _ = _determine_verdict({"risk_score": 30, "fresh_wallet_pct": 60}, {})
    assert verdict == "MEDIUM"  # 30 + 10 = 40 (>=40)


def test_verdict_wallet_flags_and_low_liquidity_stack():
    scan = {"risk_score": 15, "liquidity": 500, "mcap": 50_000, "threats": ["t"]}
    verdict, msg = _determine_verdict(scan, {"flags": ["a", "b", "c"]})
    assert verdict == "LOW"  # 15 + 10 + 10 = 35
    assert "LOW RISK" in msg


def test_verdict_clean_is_safe():
    verdict, msg = _determine_verdict({"risk_score": 5}, None)
    assert verdict == "SAFE"
    assert "Clean" in msg


def test_verdict_score_is_capped_at_100():
    scan = {
        "risk_score": 95,
        "bundle_detected": True,
        "fresh_wallet_pct": 80,
        "liquidity": 100,
        "mcap": 20_000,
    }
    verdict, _ = _determine_verdict(scan, {"flags": ["a", "b", "c"]})
    assert verdict == "CRITICAL"


# ── _whale_flow_summary ────────────────────────────────────────────────────


def test_whale_flow_bullish():
    out = _whale_flow_summary(
        [
            {"type": "buy", "value_usd": 600},
            {"type": "buy", "value_usd": 400},
            {"type": "sell", "value_usd": 500},
        ]
    )
    assert out["buy_vol"] == 1000.0
    assert out["sell_vol"] == 500.0
    assert out["net"] == 500.0
    assert out["ratio"] == 2.0
    assert out["buys"] == 2
    assert out["sells"] == 1
    assert "BULLISH" in out["verdict"]


def test_whale_flow_bearish_and_neutral():
    sell = _whale_flow_summary([{"type": "sell", "value_usd": 100}])
    assert "BEARISH" in sell["verdict"]
    assert sell["ratio"] == 0.0  # no buys → ratio 0
    neutral = _whale_flow_summary(
        [{"type": "buy", "value_usd": 100}, {"type": "sell", "value_usd": 100}]
    )
    assert "NEUTRAL" in neutral["verdict"]


def test_whale_flow_tolerates_bad_values_and_defaults_to_buy():
    out = _whale_flow_summary([{"value_usd": "abc"}, {"value_usd": 50}])
    assert out["buy_vol"] == 50.0
    assert out["buys"] == 2
    empty = _whale_flow_summary([])
    assert empty["net"] == 0.0
    assert "NEUTRAL" in empty["verdict"]


# ── market formatters (split package) ──────────────────────────────────────


def test_fmt_usd_scale_ladder():
    assert _fmt_usd(None) == "N/A"
    assert _fmt_usd("abc") == "abc"
    assert _fmt_usd(3.2e12) == "$3.20T"
    assert _fmt_usd(1.5e9) == "$1.50B"
    assert _fmt_usd(2.5e6) == "$2.50M"
    assert _fmt_usd(67_234) == "$67,234.00"
    assert _fmt_usd(12.5) == "$12.5000"
    assert _fmt_usd(0.05) == "$0.050000"
    assert _fmt_usd(0.00001234) == "$0.0000123400"


def test_change_emoji_direction():
    assert _change_emoji(0) == "\U0001f7e2"
    assert _change_emoji(1.2) == "\U0001f7e2"
    assert _change_emoji(-0.1) == "\U0001f534"


def test_mover_link_routing():
    gmgn = _mover_link({"address": "0xAbC", "chain": "SOLANA", "source": "GMGN"})
    assert "dexscreener.com/solana/0xabc" in gmgn.lower()
    cg = _mover_link({"address": "bitcoin", "source": "coingecko"})
    assert cg == "https://www.coingecko.com/en/coins/bitcoin"
    assert _mover_link({}) == "https://www.coingecko.com/"


def test_render_movers_lines_structure():
    gainers = [
        {
            "symbol": "pepe",
            "name": "Pepe",
            "address": "0xabc",
            "source": "gmgn",
            "price": 0.0001,
            "volume_24h": 50_000,
            "is_new_launch": True,
        }
    ]
    lines = _render_movers_lines(gainers, [], "24h", sources=["dexscreener"])
    body = "\n".join(lines)
    assert "24H" in lines[0]
    assert "Sources: dexscreener" in body
    assert "PEPE" in body
    assert "dexscreener.com" in body
    assert "No losers" in body
    assert lines[-1] == footer_links()


def test_render_movers_lines_empty_states():
    lines = _render_movers_lines([], [], "1h")
    body = "\n".join(lines)
    assert "1H" in lines[0]
    assert "No gainers" in body
    assert "TOP LOSERS" in body

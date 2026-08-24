"""Wins 1-4: highest-traffic content-pipeline tests.

Covers the four paths every reader-facing output flows through:
AI verdict facts, news routing, alert verdicts, scan flattening.
"""


# ── Win 1: AI verdict fact extraction ───────────────────────────────────
def test_verdict_facts_extracts_all_signals():
    from rugmunchbot.tools.scanner._ai_verdict import _verdict_facts

    r = {
        "holders": 1543,
        "holder_analysis": {
            "top10_pct": 61.2,
            "top5_pct": 48.0,
            "sample_size": 300,
            "source": "ankr",
        },
        "bundle_analysis": {"launch_cluster_score": 87},
        "fresh_wallet_pct": 41.2,
        "exchange_funded_pct": 34.7,
        "flow_analysis": {
            "participants": 50,
            "top_mover_share_pct": 61.4,
            "concentrated_flow": True,
        },
    }
    f = _verdict_facts(r)
    assert f["total_holders"] == 1543
    assert f["holder_sample_size"] == 300
    assert f["holder_data_source"] == "ankr"
    assert f["bundle_cluster_score"] == 87
    assert f["flow_concentrated"] is True
    assert f["fresh_wallet_pct"] == 41.2


def test_verdict_facts_handles_empty_result():
    from rugmunchbot.tools.scanner._ai_verdict import _verdict_facts

    f = _verdict_facts({})
    assert f["total_holders"] is None or f["total_holders"] == 0
    # never raises on missing keys — fail-open contract


# ── Win 2: news routing bands ───────────────────────────────────────────
def test_route_news_bands():
    from rugmunchbot.channels.router import route_news

    assert route_news(0.1, "news") == "skip"  # below floor
    assert route_news(0.5, "news") == "news"  # mid band
    assert route_news(0.9, "news") == "main"  # main gate
    # alpha category: mid importance goes alpha, high goes main (main wins)
    assert route_news(0.5, "alpha") in ("alpha", "news")
    assert route_news(0.9, "alpha") == "main"


# ── Win 3: alert verdict derivation table ──────────────────────────────
def test_alert_verdict_flag_matching():
    from rugmunchbot.channels.formatters._alerts import alert_verdict

    honeypot = alert_verdict({"risk_flags": ["honeypot_detected"]})
    assert "honeypot" in honeypot.lower()
    lp = alert_verdict({"risk_flags": ["lp_removed_event"]})
    assert "LP removed" in lp or "liquidity" in lp.lower()


def test_alert_verdict_fallback_honest():
    from rugmunchbot.channels.formatters._alerts import alert_verdict

    generic = alert_verdict({"risk_flags": [], "risk_level": "unknown"})
    assert len(generic) > 10  # honest fallback, not empty
    critical = alert_verdict({"risk_flags": [], "risk_level": "critical"})
    assert "critical" in critical.lower()


# ── Win 4: flatten field mapping ────────────────────────────────────────
def test_flatten_surfaces_degen_intel_fields():
    """The production-audit fields must survive flattening."""
    from rugmunchbot.tools.scanner._flatten import _flatten_v2

    raw = {
        "token": {"symbol": "T", "name": "Test"},
        "sentinel": {
            "free": {
                "holder_analysis": {
                    "exchange_funded_percentage": 34.7,
                    "new_wallets_percentage": 41.2,
                    "top_10_percentage": 61.2,
                }
            }
        },
        "bundle_analysis": {"launch_cluster_score": 87},
    }
    flat = _flatten_v2(raw, "0xABC", "eth")
    ha = flat.get("holder_analysis") or {}
    ba = flat.get("bundle_analysis") or {}
    assert ha.get("exchange_funded_pct") == 34.7
    assert ha.get("new_wallets_pct") == 41.2
    assert ha.get("top_10_pct") == 61.2
    assert ba.get("launch_cluster_score") == 87

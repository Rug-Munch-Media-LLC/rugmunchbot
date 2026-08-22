"""Test wave 20 — canonical ML deep-scan report renderer.

Contracts read from source (2026-08-22). Pure rendering over a report-like
object (SimpleNamespace stands in for ``DeepScanReport``).
"""

from __future__ import annotations

from types import SimpleNamespace

from rugmunchbot.formatters._scan_report_deepscan._ml import (
    _fmt_score,
    _fmt_tax,
    _ml_verdict_sentence,
    _ml_what_to_watch,
    format_deep_ml_scan_report,
)

# ── small formatters ─────────────────────────────────────────────────────────


def test_fmt_score_probability_vs_raw() -> None:
    assert _fmt_score(0.5) == "50.0%"
    assert _fmt_score(1) == "100.0%"
    assert _fmt_score(0) == "0.0%"
    assert _fmt_score(2.5) == "2.50"  # outside 0..1 -> raw two-decimal
    assert _fmt_score(None) == "n/a"
    assert _fmt_score("junk") == "n/a"


def test_fmt_tax() -> None:
    assert _fmt_tax(5) == "5.0%"
    assert _fmt_tax("12.34") == "12.3%"
    assert _fmt_tax(None) == "n/a"


def test_ml_verdict_sentences() -> None:
    assert "avoid" in _ml_verdict_sentence("critical", {})
    assert "high caution" in _ml_verdict_sentence("high", {})
    assert "size small" in _ml_verdict_sentence("medium", {})
    assert "no major ML anomaly" in _ml_verdict_sentence("low", {})
    assert "no major ML anomaly" in _ml_verdict_sentence("", {})


def test_ml_what_to_watch_evidence_keywords_win() -> None:
    honeypot = [{"message": "Sell simulation says HONEYPOT"}]
    assert "Verify you can actually SELL" in _ml_what_to_watch("low", honeypot)
    blacklist = [{"message": "owner can BLACKLIST holders"}]
    assert "blacklist wallets" in _ml_what_to_watch("low", blacklist)
    abi = [{"message": "decompiled DANGEROUS FUNCTION found"}]
    assert "hostile" in _ml_what_to_watch("low", abi)


def test_ml_what_to_watch_level_fallbacks() -> None:
    assert "avoid this token entirely" in _ml_what_to_watch("critical", [])
    assert "exit liquidity pulls" in _ml_what_to_watch("high", [])
    assert "watch taxes" in _ml_what_to_watch("medium", [])
    assert "Standard caution" in _ml_what_to_watch("low", [])


# ── full report rendering ────────────────────────────────────────────────────


def _minimal_report() -> SimpleNamespace:
    return SimpleNamespace(
        overall_risk="unknown",
        risk_scores={},
        sell_sim={},
        decompiled={},
        findings=[],
        address="0xabc",
        chain="ethereum",
    )


def test_minimal_report_renders_neutral_skeleton() -> None:
    out = format_deep_ml_scan_report(_minimal_report())
    assert out.startswith("🔬 <b>Deep ML Scan</b>")
    assert "<code>0xabc</code>" in out
    assert "Chain: <b>ethereum</b>" in out
    assert "⚪ <b>Verdict: UNKNOWN</b>" in out
    assert "Key Metrics" in out
    assert "Sell-Sim Tax     <b>n/a</b>" in out
    assert "Findings         <b>0</b>" in out
    assert "<i>unavailable</i>" in out  # sell-sim warning fallback
    assert "Decompiled ABI: <i>unavailable</i>" in out
    assert "✅ No findings recorded" in out
    assert "Standard caution" in out


def test_full_report_metrics_verdict_and_analyst_block() -> None:
    report = SimpleNamespace(
        overall_risk="critical",
        risk_scores={
            "fraud_gnn": {"fraud_probability": 0.92, "model": "gnn-x", "is_fraud": True},
            "pyod": {"anomaly_score": 0.30, "model_type": "iforest"},
            "pygod": {"model_type": "skipped"},
        },
        sell_sim={"tax_pct": 99},
        decompiled={},
        findings=[],
        address="0xabc",
        chain="sol",
    )
    out = format_deep_ml_scan_report(report, analyst_read="🧠 <b>Analyst Read</b>\nCoordinated.")
    assert "🔴 <b>Verdict: CRITICAL</b>" in out
    assert "<b>Analyst Read</b>" in out  # AI block renders before metrics
    assert "<b>92.0%</b> (gnn-x) ⚠️ anomaly" in out
    assert "<b>30.0%</b> (iforest)" in out
    assert "pygod            <i>skipped (no transfer graph)</i>" in out
    assert "Sell-Sim Tax     <b>99.0%</b>" in out
    assert "Critical ML verdict" in out  # watch line follows the level


def test_sell_simulation_blocked_with_flags_and_source() -> None:
    report = SimpleNamespace(
        overall_risk="high",
        risk_scores={},
        sell_sim={
            "sell_sim_verdict": "blocked",
            "source": "honeypot.is",
            "sell_tax": 55,
            "simulation_success": True,
            # Honeypot evidence flag needs can_sell=True + a pair revert.
            "can_sell": True,
            "sell_to_pair_reverted": True,
            "blacklist_hit": True,
            "anti_whale_hit": True,
            "risk": "severe",
            "tax_pct": 55,
        },
        decompiled={},
        findings=[],
        address="0xabc",
        chain="eth",
    )
    out = format_deep_ml_scan_report(report)
    assert "🔴 Sell sim BLOCKED / EXTREME taxes (55.0% sell tax) <i>(honeypot.is)</i>" in out
    # Flags are space-joined after the risk/tax pair.
    assert "risk=severe · tax 55.0% · 🍅 honeypot 🚫 blacklist 🐋 anti-whale" in out
    assert "Multiple anomaly signals" in out


def test_findings_sorted_worst_first_and_capped() -> None:
    findings = [{"level": "info", "message": f"info-{i}", "module": "m"} for i in range(4)]
    findings += [
        {"level": "warning", "message": "warn-1", "module": "m"},
        {"level": "error", "message": "err-1", "module": "m"},
        {"level": "mystery", "message": "odd", "module": "m"},
    ]
    report = SimpleNamespace(
        overall_risk="low",
        risk_scores={},
        sell_sim={},
        decompiled={"success": True, "functions": ["a", "b"]},
        findings=findings,
        address="0xabc",
        chain="eth",
    )
    out = format_deep_ml_scan_report(report)
    err_pos = out.index("❌ err-1")
    warn_pos = out.index("⚠️ warn-1")
    info_pos = out.index("\u2139\ufe0f info-0")
    assert err_pos < warn_pos < info_pos  # worst first
    assert "…and 2 more" in out  # 7 findings - 5 shown
    assert "ABI Functions    <b>2</b>" in out
    assert "Decompiled ABI: <b>2</b> function(s) from bytecode" in out

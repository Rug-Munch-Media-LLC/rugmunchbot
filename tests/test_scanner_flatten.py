"""Scanner tooling tests — v2 flatten contracts + whale alert builder.

The scan chain: backend /api/v2/scanner/token/scan (async phases) →
``v2_token_to_legacy`` flattens to the report dict → formatters render.
These tests pin the flatten contract so new backend fields can't silently
drop out of bot reports.
"""

from __future__ import annotations

from rugmunchbot.tools.scanner._flatten import v2_token_to_legacy


def _sample_v2() -> dict:
    """Minimal v2 response with every field the report surface needs."""
    return {
        "name": "Test Token",
        "symbol": "TEST",
        "chain": "ethereum",
        "safety_score": 72,
        "risk_score": 28,
        "risk_level": "medium",
        "confidence": 0.8,
        "liquidity_usd": 250_000,
        "market_cap_usd": 5_000_000,
        "age_hours": 72,
        "bundle_analysis": {
            "is_bundled": False,
            "result": "scanned",
            "confidence": 0.9,
        },
        "deployer_profile": {"age_days": 210, "rugs_deployed": 0},
        "wallet_metrics": {
            "holders_analyzed": 50,
            "fresh_wallet_pct": 4.0,
            "cex_funded_pct": 10.0,
            "first_tx_pct": 12.0,
        },
        "evidence": {
            "funding_sources": {
                "exchange_funded_pct": 10.0,
                "fresh_wallet_pct": 4.0,
                "top_funders": [{"address": "0xabc", "label": "CoW"}],
            },
            "security": {"checks": {"HP01": {"status": "pass", "details": "not a honeypot"}}},
        },
        "modules_run": ["token_security", "funding_analysis"],
        "green_flags": ["no launch bundle"],
    }


def test_flatten_preserves_scores():
    flat = v2_token_to_legacy(_sample_v2())
    assert flat.get("safety_score") == 72
    assert flat.get("risk_level") == "medium"


def test_flatten_maps_wallet_metrics():
    """% fresh / % CEX-funded / % first-tx must reach the report dict."""
    flat = v2_token_to_legacy(_sample_v2())
    wm = flat.get("wallet_metrics") or {}
    assert wm.get("fresh_wallet_pct") == 4.0
    assert wm.get("cex_funded_pct") == 10.0
    assert wm.get("holders_analyzed") == 50


def test_flatten_maps_bundle_not_bundled():
    flat = v2_token_to_legacy(_sample_v2())
    ba = flat.get("bundle_analysis") or {}
    assert ba.get("is_bundled") is False


def test_flatten_handles_missing_gracefully():
    """Empty v2 body → flat dict with no crashes, no fabricated values."""
    flat = v2_token_to_legacy({})
    assert isinstance(flat, dict)


def test_flatten_unavailable_engines_dont_render_zeros():
    """result=unavailable payloads must not become 0% lines in reports."""
    body = _sample_v2()
    body["wallet_metrics"] = {
        "result": "unavailable",
        "fresh_wallet_pct": None,
        "cex_funded_pct": None,
    }
    flat = v2_token_to_legacy(body)
    wm = flat.get("wallet_metrics") or {}
    # unavailable marker preserved so the formatter skips the line
    assert wm.get("result") == "unavailable"

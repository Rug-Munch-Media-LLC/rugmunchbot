"""RiskReport scan section + shill footer tests (wave 2026-08-29).

Covers the full-product scan block wired from
``rugmunch_scanner/unified/risk_report.py`` into the bot formatter:

  deployer / holders / first-buyer / top-25 / fresh% / CEX-funded% /
  bundle / cluster / honeypot / liquidity + the ~30-factor risk score +
  rug/scam determination.

Basic scans render the concise block; deep scans the full factor table +
deeper analysis. Every scan post carries the ref links + cross-channel shill.
Pure rendering over report dicts — no network, no I/O.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._base import scan_shill_footer
from rugmunchbot.formatters._scan_report_main import format_deep_scan_report, format_scan_report
from rugmunchbot.formatters._scan_report_riskreport import (
    _build_risk_report_from_scan,
    format_risk_report_block,
)


def _rich_scan() -> dict[str, Any]:
    """A realistic scan payload carrying every RiskReport data source."""
    return {
        "name": "Synthetic Rug Token",
        "symbol": "SRUG",
        "chain": "solana",
        "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "risk_score": 88,
        "honeypot": False,
        "mintable": True,
        "owner_renounced": False,
        "buy_tax": 0.12,
        "sell_tax": 0.18,
        "lp_locked": 0.12,
        "holders": 412,
        "top_holders": [
            {"address": "0x" + "d" * 40, "pct": 25.0, "tag": "deployer"},
            *[{"address": "0x" + str(i).zfill(40), "pct": 3.0} for i in range(1, 25)],
        ],
        "deployer_address": "0x" + "d" * 40,
        "deployer_profile": {
            "total_deployments": 6,
            "rug_rate": 0.7,
            "funded_from": "coinbase",
            "wallet_age_days": 90,
        },
        "wallet_metrics": {"fresh_wallet_pct": 55.0, "cex_funded_pct": 8.0},
        "funding_analysis": {"exchange_funded_pct": 8.0, "fresh_wallet_pct": 55.0},
        "bundle_analysis": {
            "is_bundled": True,
            "confidence": 82.0,
            "launch_cluster_score": 74.0,
            "first_time_buyer_pct": 62.0,
            "deployer_funded_pct": 38.0,
            "cluster_blocks": [{"block_number": 1, "buyer_count": 7}],
            "launch_buyers": [{"address": "0xholder2"}],
        },
        "first_buyers": [{"address": "0xholder2", "block_number": 1}],
        "liquidity": 18_000.0,
        "liquidity_verification": {"total_locked_percentage": 12.0},
        "holder_overlap": {"max_overlap_pct": 41.0, "worst_token_is_scam": True},
        "evidence": {
            "security": {
                "is_honeypot": False,
                "buy_tax_pct": 12.0,
                "sell_tax_pct": 18.0,
                "is_mintable": True,
                "ownership_renounced": False,
            },
            "holders": {"holder_count": 412},
        },
        "sentinel": {
            "free": {
                "holder_analysis": {
                    "total_holders": 412,
                    "top_10_percentage": 75.5,
                    "exchange_funded_percentage": 8.0,
                    "new_wallets_percentage": 55.0,
                },
                "honeypot_detection": {"is_honeypot": False, "buy_tax": 12.0, "sell_tax": 18.0},
                "contract_authority": {"can_mint": True},
            }
        },
        "holder_analysis": {"total_holders": 412, "top_10_pct": 75.5},
        "fresh_wallet_pct": 55.0,
        "exchange_funded_pct": 8.0,
        "price": 0.0001,
        "mcap": 1_200_000.0,
        "volume_24h": 95_000.0,
    }


def test_builder_produces_full_report() -> None:
    rep = _build_risk_report_from_scan(_rich_scan())
    assert rep is not None
    # Composite + determination from the ~30-factor weighted model.
    assert rep["composite_score"] is not None
    assert rep["determination"] in ("scam", "rug", "risky", "ok", "unknown")
    assert len(rep["factors"]) >= 25
    assert rep["data_quality"]["factors_total"] >= 25
    # Every product section present.
    assert rep["deployer"]["address"]
    assert rep["holders"]["total_holders"] == 412
    assert rep["holders"]["top_25_pct"] is not None
    assert rep["first_buyer"]["address"]
    assert rep["fresh_wallet_pct"] is not None
    assert rep["cex_funded_wallet_pct"] is not None
    assert rep["bundle"]["is_bundled"] is True
    assert rep["honeypot"]["is_honeypot"] is False
    assert rep["liquidity"]["liquidity_usd"] is not None


def test_builder_verdicts() -> None:
    # Clean token → OK.
    clean = _rich_scan()
    clean["deployer_profile"] = {"total_deployments": 1, "rug_rate": 0.0}
    clean["bundle_analysis"]["is_bundled"] = False
    clean["bundle_analysis"]["launch_cluster_score"] = 5.0
    clean["holder_overlap"] = {"max_overlap_pct": 5.0}
    clean["mintable"] = False
    rep = _build_risk_report_from_scan(clean)
    assert rep is not None
    assert rep["determination"] == "ok"
    # Confirmed honeypot → scam.
    hp = _rich_scan()
    hp["honeypot"] = True
    rep_hp = _build_risk_report_from_scan(hp)
    assert rep_hp is not None
    assert rep_hp["determination"] == "scam"


def test_builder_degrades_gracefully() -> None:
    # Minimal payload: no crash, mostly-unknown factors, no fabricated score.
    rep = _build_risk_report_from_scan({"address": "0xabc", "chain": "solana"})
    assert rep is not None
    assert rep["composite_score"] is None or rep["composite_score"] < 45
    assert rep["determination"] in ("unknown", "ok")
    # No address at all → None (the formatter omits the section).
    assert _build_risk_report_from_scan({"name": "x"}) is None
    assert format_risk_report_block({"name": "x"}) == []


def test_concise_block_skips_too_thin_reports() -> None:
    """A concise RiskReport with too few known factors to declare a verdict
    is omitted entirely — a "Composite 0/100 OK · 1/30 factors known" block
    is a contradiction generator, not a finding (scans-of-interest rule:
    header score and body composite must agree)."""
    thin = _rich_scan()
    # Strip every provider payload AND flat field so almost no factor is known.
    for key in (
        "deployer_profile",
        "deployer_address",
        "holder_analysis",
        "wallet_metrics",
        "funding_analysis",
        "holder_overlap",
        "fresh_wallet_pct",
        "exchange_funded_pct",
        "top_holders",
        "honeypot",
        "mintable",
        "owner_renounced",
        "buy_tax",
        "sell_tax",
        "lp_locked",
        "liquidity",
        "liquidity_verification",
        "holders",
        "volume_24h",
    ):
        thin.pop(key, None)
    thin["bundle_analysis"] = None
    thin["first_buyers"] = None
    thin["evidence"] = {}
    thin["sentinel"] = {}
    rep = _build_risk_report_from_scan(thin)
    assert rep is not None
    assert rep["data_quality"]["factors_known"] < 6
    # Concise block skipped…
    assert format_risk_report_block(thin) == []
    # …while the rich scan still renders its concise block.
    assert format_risk_report_block(_rich_scan()) != []
    # A confirmed honeypot matters even on a thin scan — the block renders.
    thin_hp = dict(thin)
    thin_hp["honeypot"] = True
    assert format_risk_report_block(thin_hp) != []
    # Deep scans keep the full factor table (unknowns render as "—" honestly).
    assert format_risk_report_block(thin, deep=True) != []


def test_basic_block_shows_full_product_sections() -> None:
    out = "\n".join(format_risk_report_block(_rich_scan()))
    for expected in (
        "<b>Composite Risk:</b>",
        "RUG",
        "<b>Deployer:</b>",
        "<b>Holders:</b>",
        "<b>First buyer:</b>",
        "<b>Top-25:</b>",
        "<b>Bundle:</b>",
        "<b>Honeypot:</b>",
        "<b>Liquidity:</b>",
        "<b>Top Risks</b>",
    ):
        assert expected in out, f"missing {expected} in basic block"
    # Concise: NO full factor table in the basic block.
    assert "<b>Factors (" not in out


def test_deep_block_renders_full_factor_table_and_deeper_analysis() -> None:
    out = "\n".join(format_risk_report_block(_rich_scan(), deep=True))
    assert "<b>Factors (30)</b>" in out or "<b>Factors (" in out
    assert "<b>Deeper Analysis</b>" in out
    assert "Repeat deployer" in out
    assert "Holder overlap with known scams" in out
    # Unavailable cluster factors render honestly as "—", never invented.
    assert "\u2014" in out


def test_backend_provided_report_wins_over_local_builder() -> None:
    r = _rich_scan()
    # A backend-provided RiskReport dict (RiskReport.to_dict shape).
    r["risk_report"] = {
        "token_address": r["address"],
        "chain": r["chain"],
        "composite_score": 95,
        "determination": "scam",
        "summary": "backend verdict",
        "data_quality": {"factors_known": 30, "factors_total": 30, "coverage_pct": 100.0},
        "deployer": {"address": "0xbackend"},
        "holders": {"total_holders": 100},
        "first_buyer": {},
        "top_25_holders": [],
        "fresh_wallet_pct": None,
        "cex_funded_wallet_pct": None,
        "bundle": {"is_bundled": None},
        "cluster": {},
        "honeypot": {"is_honeypot": None},
        "liquidity": {"locked_pct": None, "liquidity_usd": None},
        "factors": [
            {
                "key": "honeypot",
                "label": "Honeypot (cannot sell)",
                "risk_score": 95.0,
                "status": "critical",
                "explanation": "backend data",
            }
        ],
        "deeper_analysis": {},
        "ai_assisted": False,
        "sources_used": ["backend"],
    }
    out = "\n".join(format_risk_report_block(r, deep=True))
    assert "<b>95/100</b>" in out
    assert "SCAM" in out
    # The factor row came from the backend dict (its label + score render),
    # not the local builder (which would show the whole 30-factor table).
    assert "<b>Factors (1)</b>" in out
    assert "Honeypot (cannot sell)" in out


def test_full_scan_report_includes_risk_report_and_shill() -> None:
    out = format_scan_report(_rich_scan())
    assert "<b>Risk Report</b>" in out
    assert "<b>RugMunch Intelligence</b>" in out  # shill footer
    # Ref links: trade footer + channel promo links.
    assert "RugMunchBot" in out
    assert "rugmunch.io" in out
    assert "t.me/rmiscans" in out


def test_deep_scan_report_renders_full_factor_table() -> None:
    out = format_deep_scan_report(_rich_scan())
    assert "30-factor breakdown" in out
    assert "<b>Factors (30)</b>" in out or "<b>Factors (" in out
    assert "<b>Deeper Analysis</b>" in out


def test_shill_footer_contains_all_channels() -> None:
    out = scan_shill_footer()
    for expected in (
        "https://t.me/RugMunchBot",
        "https://rugmunch.io",
        "https://t.me/rmiscans",
        "https://t.me/rmialerts",
        "https://t.me/rmicryptonews",
        "https://t.me/rmialpha",
        "https://t.me/cryptorugmuncher",
    ):
        assert expected in out, f"missing {expected} in shill footer"


# ── Ref links in BOTH regular + deep scan (2026-08-29 depth push) ─────────
# The ref-attributed trade links (jupiter / gmgn / photon / birdeye /
# dexscreener / dextools / rugcharts / rugmaps) must render in the footer of
# /scan AND /deepscan. On Solana the footer renders exactly this set.


def _assert_ref_links(out: str) -> None:
    for expected in (
        "jup.ag/swap/SOL-EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v?ref=rugmunch",  # jupiter
        "gmgn.ai/sol/token/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v?ref=rugmunch",  # gmgn
        "photon-sol.tinyastro.io/en/lp/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v?handle=rugmunch",  # photon
        "birdeye.so/token/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # birdeye
        "dexscreener.com/solana/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # dexscreener
        "dextools.io/app/en/sol/pair-explorer/EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # dextools
        "rugmunch.io/rugcharts?token=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # rugcharts
        "rugmunch.io/rugmaps?token=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # rugmaps
    ):
        assert expected in out, f"missing ref link: {expected}"


def test_regular_scan_renders_all_ref_links() -> None:
    _assert_ref_links(format_scan_report(_rich_scan()))


def test_deep_scan_renders_all_ref_links() -> None:
    _assert_ref_links(format_deep_scan_report(_rich_scan()))


# ── First-buyers cohort depth (250-wallet deep analysis) ─────────────────


def _first_buyers_payload(limit: int = 250, count: int = 30) -> dict[str, Any]:
    """Dict-shaped first-buyers payload from the deep endpoint / engine."""
    buyers = [
        {
            "wallet": f"0xwallet{i:04d}",
            "first_buy_ts": "2026-08-29T11:55:00+00:00",
            "tokens": 1_000_000_000,
            "wallet_age_bucket": "fresh" if i < 5 else ("new" if i < 10 else "established"),
            "age_at_buy_s": 60 + i,
            "bundled": i < 8,
        }
        for i in range(count)
    ]
    return {
        "buyers": buyers,
        "aggregates": {
            "unique_buyers": count,
            "fresh_wallet_pct": 16.7,
            "new_wallet_pct": 33.3,
            "established_pct": 66.7,
            "bundled_pct": 26.7,
            "median_latency_s": 90.0,
        },
        "limit": limit,
        "chain": "ethereum",
    }


def test_first_buyers_deep_renders_25_rows_and_cohort_label() -> None:
    from rugmunchbot.formatters._scan_report_common._layout import _first_buyers_rows

    r = _rich_scan()
    r["first_buyers"] = _first_buyers_payload(limit=250, count=30)
    rows = _first_buyers_rows(r, deep=True)
    body = "\n".join(rows)
    assert "first-250 cohort" in body  # explicit cohort label
    assert "<b>30</b> tracked" in body
    assert "🆕 17% fresh wallets" in body
    # Deep renders the per-wallet rows: 25 wallet rows + 1 aggregates line.
    assert len(rows) == 26
    assert "<code>0xwallet0000</code>" in body
    assert "<code>0xwallet0024</code>" in body  # row 25 present


def test_first_buyers_basic_renders_cohort_label() -> None:
    from rugmunchbot.formatters._scan_report_common._layout import _first_buyers_rows

    r = _rich_scan()
    r["first_buyers"] = _first_buyers_payload(limit=25, count=25)
    rows = _first_buyers_rows(r, deep=False)
    body = "\n".join(rows)
    assert "first-25" in body
    assert len(rows) == 1  # aggregates line only, no per-wallet rows


# ── RiskReport consumes the 250-wallet first-buyer cohort dict ───────────


def test_risk_report_consumes_first_buyer_cohort_dict() -> None:
    r = _rich_scan()
    r["first_buyers"] = _first_buyers_payload(limit=250, count=30)
    rep = _build_risk_report_from_scan(r)
    assert rep is not None
    fb = rep["first_buyer"]
    assert fb["address"] == "0xwallet0000"
    assert fb["cohort_size"] == 30
    assert fb["cohort_limit"] == 250
    assert fb["cohort_bundled_pct"] == 26.7
    # The deeper narrative names the cohort depth.
    assert "Cohort: 30 wallets (limit 250)" in str(rep["deeper_analysis"])


def test_deep_cluster_summary_is_deep_aware() -> None:
    r = _rich_scan()
    rep_basic = _build_risk_report_from_scan(r, deep=False)
    rep_deep = _build_risk_report_from_scan(r, deep=True)
    assert rep_basic is not None
    assert rep_deep is not None
    basic_summary = rep_basic["deeper_analysis"]["cluster"]["summary"]
    deep_summary = rep_deep["deeper_analysis"]["cluster"]["summary"]
    assert "see /deepscan" in basic_summary
    assert "see /deepscan" not in deep_summary


def test_deep_thin_report_headline_is_honest_not_0_of_100_ok() -> None:
    """A deep scan with <6 known factors must NOT render the self-contradictory
    "Composite Risk: 0/100 OK · 1/30 factors known" headline — it says the scan
    is pending instead, so header score and body composite always agree."""
    thin = _rich_scan()
    for key in (
        "deployer_profile",
        "deployer_address",
        "holder_analysis",
        "wallet_metrics",
        "funding_analysis",
        "holder_overlap",
        "fresh_wallet_pct",
        "exchange_funded_pct",
        "top_holders",
        "honeypot",
        "mintable",
        "owner_renounced",
        "buy_tax",
        "sell_tax",
        "lp_locked",
        "liquidity",
        "liquidity_verification",
        "holders",
        "volume_24h",
    ):
        thin.pop(key, None)
    thin["bundle_analysis"] = None
    thin["first_buyers"] = None
    thin["evidence"] = {}
    thin["sentinel"] = {}
    out = "\n".join(format_risk_report_block(thin, deep=True))
    # The self-contradictory composite headline must not appear…
    assert "<b>Composite Risk:</b>" not in out
    # …and the honest "insufficient data" marker must (factors-known coverage
    # still appears, but framed as a pending scan, not as a clean verdict).
    assert "Insufficient data" in out
    assert "scan pending" in out.lower()
    assert "needs \u2265 6" in out


def test_honeypot_render_never_bare_x() -> None:
    """The honeypot line must read "🚨 HONEYPOT" / "Not a honeypot", never a
    bare "honeypot=✗" cross that a reader mistakes for a clean verdict."""
    hp = _rich_scan()
    hp["honeypot"] = False
    out = "\n".join(format_risk_report_block(hp))
    assert "Not a honeypot" in out
    assert "honeypot=\u2717" not in out  # the bare-✗ form is gone
    hp_true = _rich_scan()
    hp_true["honeypot"] = True
    out_true = "\n".join(format_risk_report_block(hp_true))
    assert "HONEYPOT" in out_true
    assert "honeypot=\u2717" not in out_true

"""Test wave 28 — SENTINEL deep-forensic formatter (deepscan/_sentinel.py).

Behavior probed via .venv/bin/python before asserting (2026-08-23). Pure
dict->str rendering; no mocks needed.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_deepscan._sentinel import (
    _sentinel_watch_line,
    format_sentinel_deepscan_section,
    format_sentinel_scan_report,
)

ADDR = "0x" + "ab" * 20


# ── _sentinel_watch_line cascade ──


def test_watch_line_honeypot_wins_over_level() -> None:
    # Probe: keyword cascade beats the level fallback even when level is low.
    assert _sentinel_watch_line(["HONEYPOT detected"], "low") == (
        "Verify you can actually SELL before buying \u2014 honeypot signal."
    )


def test_watch_line_bundle_and_wash() -> None:
    assert _sentinel_watch_line(["bundle activity"], "critical") == (
        "Volume & concentration may be fabricated \u2014 bundle activity."
    )
    assert _sentinel_watch_line(["wash trading"], "x") == (
        "Volume may be fake \u2014 wash trading suspected."
    )


def test_watch_line_level_fallbacks() -> None:
    assert _sentinel_watch_line([], "CRITICAL") == (
        "Avoid this token \u2014 critical risk signals detected."
    )
    assert _sentinel_watch_line([], "moderate") == (
        "Proceed with extreme caution \u2014 elevated risk."
    )
    assert _sentinel_watch_line([], "") == "Low risk \u2014 standard DeFi caution still applies."


def test_watch_line_none_level_is_low_risk() -> None:
    # str(None or "") -> "" -> default branch.
    assert _sentinel_watch_line([], None) == "Low risk \u2014 standard DeFi caution still applies."  # type: ignore[arg-type]


# ── fixtures ──


def _rich() -> dict[str, Any]:
    return {
        "address": ADDR,
        "chain": "ethereum",
        "composite_risk_score": "78",
        "risk_level": "high",
        "red_flags": ["Honeypot suspected"] * 9 + ["extra"],
        "module_risk_scores": {"holder_concentration": 92, "liquidity": "55", "bad": None},
        "risk_signals": [{"signal": "x"}],
        "holder_analysis": {
            "hhi": 4200,
            "risk_level": "critical",
            "total_holders": 1234,
            "top_10_percentage": 62.5,
            "top_20_percentage": 80,
            "exchange_funded_percentage": 10,
            "new_wallets_percentage": 44,
            "warnings": ["w1", "w2", "w3", "w4"],
        },
        "liquidity_verification": {
            "total_locked_percentage": 95.0,
            "lp_burned": True,
            "fake_lock_detected": True,
            "warnings": ["lw"],
        },
        "contract_authority": {"risk_level": "medium", "warnings": ["aw"]},
        "bundle_detection": {
            "deployer_funded": ["w1"],
            "same_block_snipers": ["s1", "s2"],
            "jito_bundles_detected": 3,
            "total_bundled_percentage": "31.4",
            "risk_level": "HIGH",
            "same_slot": {
                "result": "scanned",
                "same_slot_wallets": 7,
                "same_slot_sol_spent": 12.5,
                "same_slot_held_pct": 33.3,
                "same_slot": 12345,
            },
            "warnings": ["bw"],
        },
        "exchange_funding": {
            "cex_funded_percentage": 65,
            "instant_buy_percentage": 22,
            "cex_funded_count": 13,
            "total_buyers": 20,
            "exchange_breakdown": {"binance": 8, "bybit": 5},
            "risk_level": "high",
            "warnings": ["ew"],
        },
        "contract_creation": {
            "creator_address": "0xdead",
            "block_number": 12345678,
            "tx_hash": "0xhash",
            "first_interactors": [
                {"address": "0x1", "label": "sniper", "is_fresh": True},
                {"address": "0x2", "label": "cex", "is_fresh": False},
            ],
            "fresh_wallet_percentage": 66.6,
            "deployer_funded_percentage": 15.0,
        },
        "wallet_map": {
            "wallet_map_line": "WM LINE <x>",
            "fresh_wallet_line": "fresh 40%",
            "recently_funded_line": "funded 10%",
            "cex_funding_line": "binance 5",
            "deployer_funding_line": "",
        },
        "cluster_analysis": {
            "result": "scanned",
            "cluster_line": "CLUSTER LINE",
            "bundle_line": "BLINE",
            "coordinated_buyers": 4,
            "funding_chain_buyers": 2,
            "funding_chain_depth": 2,
            "two_hop_chain_buyers": 1,
            "supply_grab": {
                "status": "holding",
                "held_pct": 12.3,
                "dumped_pct": 4.5,
                "exited_wallets": 2,
            },
            "time_nodes": 3,
        },
        "cross_token_link": {
            "recurring_cluster_line": "RECUR x3",
            "deployer_link_line": "DEPLINK",
            "funder_link_line": "FUNLINK",
        },
        "decentralization": {
            "decentralization_line": "DEC LINE",
            "bubble_line": "BUB LINE",
            "breakdown": {
                "top10_pct": 61,
                "top20_pct": 75,
                "whale_share": 30,
                "distinct_funders": 4,
                "largest_cluster_size": 9,
                "cluster_source": "kg",
                "time_nodes": 2,
            },
        },
        "dev_reputation": {
            "reputation_score": 88,
            "reputation_level": "low",
            "total_launches": 7,
            "rugged_count": 1,
            "rug_rate": 0.143,
            "warnings": ["dw"],
        },
        "deployer_timeline": {"deployer_timeline_line": "TL LINE"},
    }


# ── header / verdict ──


def test_header_address_chain_verdict() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert out.startswith("\U0001f6e9\ufe0f") is False  # guard: real header is shield emoji
    assert "\U0001f6e1\ufe0f <b>AEGIS DEEP FORENSIC SCAN</b>" in out
    assert f"<code>{ADDR}</code>" in out
    assert "Verdict: HIGH</b> \u2014 Composite 78/100" in out


def test_token_address_fallback_used_when_address_missing() -> None:
    data = _rich()
    del data["address"]
    data["token_address"] = "tokaddr"
    out = format_sentinel_deepscan_section(data)
    assert "<code>tokaddr</code>" in out


def test_analyst_read_rendered_before_risk_assessment() -> None:
    out = format_sentinel_deepscan_section(_rich(), analyst_read="AI: bundle cluster detected.")
    assert out.index("AI: bundle cluster detected.") < out.index("Risk Assessment")


def test_analyst_read_absent_by_default() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "AI:" not in out


def test_composite_score_string_coerced() -> None:
    # Probe: score arrives as string from API layer; float-coerced to 78.
    out = format_sentinel_deepscan_section({"composite_risk_score": "78", "risk_level": "high"})
    assert "Composite 78/100" in out


def test_composite_score_garbage_falls_back_to_zero() -> None:
    out = format_sentinel_deepscan_section({"composite_risk_score": object()})
    assert "Composite 0/100" in out
    assert "Verdict: UNKNOWN" in out


# ── red flags ──


def test_red_flags_capped_at_eight() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert out.count("Honeypot suspected") == 8  # 9 provided, cap is 8


def test_no_red_flags_shows_green_banner() -> None:
    out = format_sentinel_deepscan_section({})
    assert "\U0001f7e2 <b>No critical findings</b>" in out
    assert "Key Findings" not in out


# ── module risk breakdown ──


def test_module_scores_sorted_desc_with_bar() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Holder Concentration: 92/100" in out
    assert "<code>\u2b1b" * 1 in out  # filled bar starts with black squares
    i_holder = out.index("Holder Concentration")
    i_liq = out.index("Liquidity: 55/100")
    i_bad = out.index("Bad: 0/100")
    assert i_holder < i_liq < i_bad  # sorted desc, None -> 0 last


def test_module_unknown_emoji_default() -> None:
    # .title() capitalizes each word of the snake_case name.
    out = format_sentinel_deepscan_section({"module_risk_scores": {"zzz_custom": 5}})
    assert "\U0001f539 Zzz Custom: 5/100" in out


# ── holder analysis ──


def test_holder_full_render_and_warning_cap() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "HHI Concentration: <b>4200</b> (critical)" in out
    assert "Total Holders: <b>1,234</b>" in out
    assert "Top-10 Supply: <b>62%</b>" in out
    assert "Top-20 Supply: <b>80%</b>" in out
    assert "Exchange-Funded: <b>10%</b>" in out
    assert "New Wallets (<7d): <b>44%</b>" in out
    assert out.count("\u26a0\ufe0f w") == 3  # warnings capped at 3 of 4


def test_distribution_verdict_from_top10_concentrated() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Distribution verdict: <b>CONCENTRATED</b>" in out


def test_distribution_verdict_moderate_and_healthy_bands() -> None:
    moderate = format_sentinel_deepscan_section({"holder_analysis": {"top_10_percentage": 35}})
    assert "Distribution verdict: <b>MODERATE</b>" in moderate
    healthy = format_sentinel_deepscan_section({"holder_analysis": {"top_10_percentage": 10}})
    assert "Distribution verdict: <b>HEALTHY</b>" in healthy


def test_distribution_verdict_falls_back_to_hhi() -> None:
    # No top_10_percentage -> HHI bands: >=3000 concentrated, >=1500 moderate.
    conc = format_sentinel_deepscan_section({"holder_analysis": {"hhi": 3500}})
    assert "Distribution verdict: <b>CONCENTRATED</b>" in conc
    mod = format_sentinel_deepscan_section({"holder_analysis": {"hhi": 2000}})
    assert "Distribution verdict: <b>MODERATE</b>" in mod


def test_hhi_garbage_value_renders_zero() -> None:
    # Probe: hhi='oops' -> float() raises -> 0.0 rendered, no crash.
    out = format_sentinel_deepscan_section({"holder_analysis": {"hhi": "oops"}})
    assert "HHI Concentration: <b>0</b>" in out


# ── LP analysis ──


def test_lp_locked_burned_fake_lock() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "LP Locked: <b>95%</b>" in out
    assert "LP tokens burned" in out
    assert "Fake lock detected" in out


def test_lp_lock_emoji_bands() -> None:
    hi = format_sentinel_deepscan_section(
        {"liquidity_verification": {"total_locked_percentage": 85}}
    )
    mid = format_sentinel_deepscan_section(
        {"liquidity_verification": {"total_locked_percentage": 40}}
    )
    zero = format_sentinel_deepscan_section(
        {"liquidity_verification": {"total_locked_percentage": 0}}
    )
    assert "\U0001f7e2 LP Locked: <b>85%</b>" in hi
    assert "\U0001f7e1 LP Locked: <b>40%</b>" in mid
    assert "\U0001f534 LP Locked: <b>0%</b>" in zero


# ── contract authority ──


def test_contract_authority_level_and_default() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Authority Risk: <b>MEDIUM</b>" in out
    # Empty dict is falsy -> whole section skipped; missing risk_level inside
    # a NON-empty dict defaults to LOW.
    skipped = format_sentinel_deepscan_section({"contract_authority": {}})
    assert "Contract Authority" not in skipped
    default = format_sentinel_deepscan_section({"contract_authority": {"warnings": []}})
    assert "Authority Risk: <b>LOW</b>" in default


# ── bundle detection ──


def test_bundle_detected_counts_snipers_and_deployer() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Bundle detected: <b>3</b> wallets \u00b7 31% bundled" in out  # '31.4' coerced
    assert "Same-block snipers: <b>2</b>" in out
    assert "Deployer-funded wallets: <b>1</b>" in out
    assert "Jito bundles: <b>3</b>" in out


def test_bundle_same_slot_scanned_line() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert (
        "Same-slot: <b>7</b> wallets \u00b7 12.50 SOL \u00b7 <b>33.3%</b> held (slot 12345)" in out
    )


def test_bundle_not_detected_green_line() -> None:
    # Non-empty dict (empty dict skips the section) with no detection signals.
    out = format_sentinel_deepscan_section({"bundle_detection": {"jito_bundles_detected": 0}})
    assert "No bundle detected" in out


def test_bundle_same_slot_offline_note_and_own_result() -> None:
    # Fail-open branch: result != scanned + note -> offline note; when the
    # SENTINEL module itself found nothing, own-bundle count/conf shown.
    data = {
        "bundle_detection": {
            "same_slot": {
                "result": "unavailable",
                "note": "offline",
                "bundle_wallets": 2,
                "confidence": 55,
            }
        }
    }
    out = format_sentinel_deepscan_section(data)
    assert "Same-slot detail unavailable \u2014 data source offline" in out
    assert "Own bundle result: <b>2</b> wallets \u00b7 conf <b>55%</b>" in out


def test_bundle_risk_level_alone_triggers_detection() -> None:
    data = {"bundle_detection": {"risk_level": "high"}}
    out = format_sentinel_deepscan_section(data)
    # Honest + fail-open: no wallet lists → the verdict renders without a
    # fabricated "0 wallets" count (2026-08-29 bundle_analysis shape).
    assert "Bundle detected: <b>YES</b>" in out
    assert "Risk: <b>HIGH</b>" in out


# ── exchange funding ──


def test_exchange_funding_full_render() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "CEX-funded: <b>65%</b> (13/20 buyers)" in out
    assert "Instant-buy (fund\u2192buy <60s): <b>22%</b>" in out
    assert "binance: 8 \u00b7 bybit: 5" in out  # breakdown sorted desc
    assert "Funding verdict: <b>HIGH</b>" in out


def test_exchange_cex_emoji_bands() -> None:
    hi = format_sentinel_deepscan_section({"exchange_funding": {"cex_funded_percentage": 60}})
    mid = format_sentinel_deepscan_section({"exchange_funding": {"cex_funded_percentage": 30}})
    lo = format_sentinel_deepscan_section({"exchange_funding": {"cex_funded_percentage": 5}})
    assert "\U0001f534 CEX-funded: <b>60%</b>" in hi
    assert "\U0001f7e1 CEX-funded: <b>30%</b>" in mid
    assert "\U0001f7e2 CEX-funded: <b>5%</b>" in lo


def test_exchange_instant_buy_hidden_when_zero() -> None:
    out = format_sentinel_deepscan_section({"exchange_funding": {"instant_buy_percentage": 0}})
    assert "Instant-buy" not in out


# ── contract creation ──


def test_contract_creation_full_render() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Creator: <code>0xdead</code>" in out
    assert "Block: <b>12,345,678</b>" in out
    assert "Tx: <code>0xhash</code>" in out
    assert "First Interactors (2):" in out
    assert "1. <code>0x1</code> \U0001f195 sniper" in out  # fresh badge
    assert "2. <code>0x2</code> cex" in out  # no badge
    assert "Fresh Wallets at Launch: <b>67%</b>" in out
    assert "Deployer-Funded at Launch: <b>15%</b>" in out


def test_creation_absent_section_skipped() -> None:
    out = format_sentinel_deepscan_section({})
    assert "Contract Creation Analysis" not in out


# ── wallet map ──


def test_wallet_map_lines_html_escaped_and_empty_omitted() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "<code>WM LINE &lt;x&gt;</code>" in out  # escaped
    assert "fresh 40%" in out
    assert "funded 10%" in out
    assert "binance 5" in out
    # Empty deployer funding line omitted entirely (never a bare dash).
    tail = out[out.index("First-20 Wallet Map") : out.index("Cluster Analysis")]
    assert tail.count("\u2014") == 0


def test_wallet_map_all_dashes_renders_placeholder_only() -> None:
    # Non-empty dict required (empty dict skips the section); missing line
    # keys fall back to the em-dash placeholder.
    out = format_sentinel_deepscan_section({"wallet_map": {"wallet_map_line": None}})
    assert "First-20 Wallet Map" in out
    assert "<code>\u2014</code>" in out


# ── cluster analysis ──


def test_cluster_scanned_full_render() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "<code>CLUSTER LINE</code>" in out
    assert "<code>BLINE</code>" in out
    assert "Coordinated buys (same block + shared funder): <b>4</b>" in out
    assert "Funding-chain wallets (funder = launch buyer): <b>2</b>" in out
    assert "2-hop funding chain: <b>1</b> wallets" in out  # depth >= 2 gate
    assert "Supply grab: <b>HOLDING</b> \u2014 holding 12.3% (dumped 4.5%) \u00b7 2 exited" in out
    assert "Time nodes (funded same hour): <b>3</b>" in out
    assert "RECUR x3" in out  # cross-token recurring appended inside cluster block


def test_cluster_two_hop_suppressed_below_depth_2() -> None:
    out = format_sentinel_deepscan_section(
        {
            "cluster_analysis": {
                "result": "scanned",
                "funding_chain_depth": 1,
                "two_hop_chain_buyers": 9,
            }
        }
    )
    assert "2-hop funding chain" not in out


def test_cluster_supply_grab_dumped_variant() -> None:
    out = format_sentinel_deepscan_section(
        {
            "cluster_analysis": {
                "result": "scanned",
                "supply_grab": {"status": "dumped", "held_pct": 1, "dumped_pct": 99},
            }
        }
    )
    assert "Supply grab: <b>DUMPED</b>" in out


def test_cluster_error_with_note_renders_note() -> None:
    out = format_sentinel_deepscan_section(
        {"cluster_analysis": {"result": "error", "note": "source down"}}
    )
    assert "<i>source down</i>" in out


def test_cluster_error_without_note_renders_dash() -> None:
    out = format_sentinel_deepscan_section({"cluster_analysis": {"result": "error"}})
    assert "<code>\u2014</code>" in out


# ── decentralization ──


def test_decentralization_full_render() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "<code>DEC LINE</code>" in out
    assert "<code>BUB LINE</code>" in out
    assert "Top-10: <b>61%</b> | Top-20: <b>75%</b> | Whales (>=5%): <b>30%</b>" in out
    assert "Funding clusters: <b>4</b> (Kg) | Largest: <b>9</b>" in out  # source title-cased
    assert "Time nodes (funded same-time): <b>2</b>" in out


def test_decentralization_empty_lines_dash_fallback() -> None:
    out = format_sentinel_deepscan_section(
        {"decentralization": {"decentralization_line": "\u2014"}}
    )
    assert "<code>\u2014</code>" in out


# ── deployer reputation / timeline / cross-token ──


def test_dev_reputation_full_render() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Reputation: <b>88/100</b> (Low)" in out
    assert "Launches: <b>7</b> | Rugged: <b>1</b>" in out
    assert "Rug Rate: <b>14%</b>" in out  # 0.143 * 100 rounded


def test_deployer_timeline_skipped_for_dash() -> None:
    out = format_sentinel_deepscan_section(
        {"deployer_timeline": {"deployer_timeline_line": "\u2014"}}
    )
    assert "Deployer History Timeline" not in out


def test_cross_token_linkage_both_lines() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Cross-Token Linkage" in out
    assert "DEPLINK" in out
    assert "FUNLINK" in out


def test_cross_token_linkage_absent_when_dashes() -> None:
    out = format_sentinel_deepscan_section({})
    assert "Cross-Token Linkage" not in out


# ── recommendations / watch / footer ──


def test_recommendations_reflect_honeypot_flag() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert "Avoid this token \u2014 critical risk signals detected" in out
    assert "verify you can actually SELL before buying" in out


def test_watch_line_present_and_dyor_footer() -> None:
    out = format_sentinel_deepscan_section({})
    assert "<b>Watch:</b> Low risk \u2014 standard DeFi caution still applies." in out
    assert "always DYOR" in out


def test_footer_links_use_address_and_chain() -> None:
    out = format_sentinel_deepscan_section(_rich())
    assert f"dexscreener.com/ethereum/{ADDR}" in out


def test_minimal_render_has_no_optional_sections() -> None:
    out = format_sentinel_deepscan_section({})
    for absent in (
        "Holder Analysis",
        "LP Analysis",
        "Contract Authority",
        "Bundle Detection",
        "Exchange Funding",
        "Module Risk Breakdown",
    ):
        assert absent not in out


def test_output_collapses_blank_runs() -> None:
    out = format_sentinel_deepscan_section({})
    assert "\n\n\n" not in out


# ── standalone wrapper ──


def test_format_sentinel_scan_report_delegates() -> None:
    data = _rich()
    assert format_sentinel_scan_report(data) == format_sentinel_deepscan_section(data)
    assert format_sentinel_scan_report({}, analyst_read="AR") == format_sentinel_deepscan_section(
        {}, analyst_read="AR"
    )

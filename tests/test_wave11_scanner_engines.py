"""Test wave 11 — scanner_engines pure helpers (holder clusters, behavioral
fingerprint similarity, wallet-flow summary).

Contracts read from source (2026-08-22). Pure logic only: no ClickHouse,
no network, no Redis.
"""

from __future__ import annotations

from rugmunchbot._vendor.scanner_engines._holder_clusters_helpers import (
    _build_bundle_clusters,
    _build_funder_clusters,
    _build_same_block_clusters,
    _classify_concentration,
    _classify_holder,
    _compute_gini,
    _compute_hhi,
)
from rugmunchbot._vendor.scanner_engines.behavioral_fingerprint import (
    _DOW_BINS,
    _HOURS_BINS,
    _confidence,
    _cosine,
    _log_scale,
    _norm,
    _normalize_hist,
    _num,
    _rel_diff,
    compare_fingerprints,
    matched_features,
)
from rugmunchbot._vendor.scanner_engines.wallet_flow import (
    _classify_node,
    _rank_counterparties,
    _summarize_flow,
)

# ── holder concentration metrics ─────────────────────────────────────────────


def test_hhi_single_holder_is_max() -> None:
    assert _compute_hhi([100.0]) == 10000.0


def test_hhi_equal_quarters() -> None:
    assert _compute_hhi([25.0, 25.0, 25.0, 25.0]) == 2500.0


def test_hhi_empty_or_zero_total_is_max() -> None:
    assert _compute_hhi([]) == 10000.0
    assert _compute_hhi([0.0, 0.0]) == 10000.0


def test_gini_perfect_equality_is_zero() -> None:
    assert _compute_gini([10.0, 10.0]) == 0.0


def test_gini_two_balances_half_split() -> None:
    # [0, 100]: weighted=1*0+2*100, total=100, n=2 -> 2*200/(2*100) - 3/2 = 0.5
    assert _compute_gini([0.0, 100.0]) == 0.5


def test_gini_degenerate_inputs_are_zero() -> None:
    assert _compute_gini([5.0]) == 0.0
    assert _compute_gini([]) == 0.0
    assert _compute_gini([0.0, 0.0]) == 0.0


def test_concentration_threshold_boundaries() -> None:
    assert _classify_concentration(80.0) == "EXTREME"
    assert _classify_concentration(79.9) == "HIGH"
    assert _classify_concentration(50.0) == "HIGH"
    assert _classify_concentration(49.9) == "MEDIUM"
    assert _classify_concentration(30.0) == "MEDIUM"
    assert _classify_concentration(29.9) == "LOW"


# ── holder labeling priority chain ───────────────────────────────────────────


def test_label_deployer_wins_over_everything() -> None:
    holder = {
        "address": "0xaaa",
        "labels": [{"label_category": "exchange", "label_name": "binance hot wallet"}],
        "balance_pct": 99.0,
    }
    assert _classify_holder(holder, {"deployer": "0xAAA"}) == "deployer"


def test_label_institutional_keywords_beat_sniper_window() -> None:
    holder = {
        "address": "0xbbb",
        "labels": [{"label_category": "", "label_subtype": "", "label_name": "Tornado Cash"}],
        "first_seen": 1000,
        "first_block": 10,
        "balance_pct": 50.0,
    }
    deploy = {"deployer": "0xddd", "deploy_ts": 1000, "deploy_block": 10}
    assert _classify_holder(holder, deploy) == "mixer"


def test_label_sniper_by_timestamp_and_by_block() -> None:
    by_time = {"address": "0x1", "first_seen": 1059}
    assert _classify_holder(by_time, {"deploy_ts": 1000}) == "sniper"
    # Negative diff (seen before deploy) is NOT a sniper.
    early = {"address": "0x2", "first_seen": 900}
    assert _classify_holder(early, {"deploy_ts": 1000}) != "sniper"
    by_block = {"address": "0x3", "first_block": 12}
    assert _classify_holder(by_block, {"deploy_block": 10}) == "sniper"


def test_label_team_then_bot_then_deployer_funding_then_whale() -> None:
    team = {"address": "0x4", "labels": [{"label_name": "treasury multisig"}]}
    assert _classify_holder(team, None) == "team"
    bot = {"address": "0x5", "labels": [{"label_name": "mev bot"}]}
    assert _classify_holder(bot, None) == "bot"
    funded = {"address": "0x6", "funder": "0xDev"}
    assert _classify_holder(funded, {"deployer": "0xdEV"}) == "team"
    whale = {"address": "0x7", "balance_pct": 5.0}
    assert _classify_holder(whale, None) == "whale"
    assert _classify_holder({"address": "0x8", "balance_pct": 4.9}, None) == "normal"


# ── cluster detection passes ─────────────────────────────────────────────────


def _fi(funder: str | None, block: int | None = None, tx: str | None = None) -> dict[str, object]:
    return {"funder": funder, "first_block": block, "first_tx": tx}


def test_funder_cluster_groups_shared_funder() -> None:
    first_in = {
        "0xa": _fi("0xf", 100, "0xtx1"),
        "0xb": _fi("0xf", 110),
        "0xc": _fi("0xf", 120),
        "0xd": _fi(None),
    }
    pools = {"0xpool"}
    clusters = _build_funder_clusters(first_in, "0xdep", pools, "0xtoken")
    assert len(clusters) == 1
    c = clusters[0]
    assert c["id"] == "funder:0"
    assert sorted(c["addresses"]) == ["0xa", "0xb", "0xc"]
    assert c["reason"] == "shared_funder"
    assert c["confidence"] == 0.55
    assert c["evidence"]["funder_is_deployer"] is False
    assert c["evidence"]["first_tx_hashes"] == ["0xtx1"]


def test_funder_cluster_grouping_is_case_sensitive() -> None:
    # Funders are grouped by EXACT string — "0xF" and "0xf" are different
    # groups, so a 2+1 split stays below the cluster minimum.
    first_in = {
        "0xa": _fi("0xf"),
        "0xb": _fi("0xf"),
        "0xc": _fi("0xF"),
    }
    assert _build_funder_clusters(first_in, None, set(), "0xtok") == []


def test_funder_cluster_deployer_reason_and_confidence_cap() -> None:
    addrs = {f"0x{i}": _fi("0xdep") for i in range(8)}
    clusters = _build_funder_clusters(addrs, "0xdep", set(), "0xtok")
    assert len(clusters) == 1
    assert clusters[0]["reason"] == "deployer_funded"
    # 0.55 + 0.1*(8-3) + 0.05 = 1.10 -> capped at 0.95
    assert clusters[0]["confidence"] == 0.95


def test_funder_cluster_excludes_pools_token_and_burned_sources() -> None:
    first_in = {
        "0xa": _fi("0xpool"),
        "0xb": _fi("0xtok"),
        "0xc": _fi("0x0000000000000000000000000000000000000000"),
        "0xd": _fi("0x000000000000000000000000000000000000dead"),
    }
    assert _build_funder_clusters(first_in, None, {"0xpool"}, "0xtok") == []


def test_funder_cluster_below_min_group_size_is_skipped() -> None:
    first_in = {"0xa": _fi("0xf"), "0xb": _fi("0xf")}
    assert _build_funder_clusters(first_in, None, set(), "0xtok") == []


def test_same_block_cluster_detects_simultaneous_buys_sorted_by_block() -> None:
    first_buy = {
        "0xa": {"first_block": 200, "first_tx": "0xt2"},
        "0xb": {"first_block": 200},
        "0xc": {"first_block": 200, "first_tx": "0xt3"},
        "0xd": {"first_block": 100},
        "0xe": {"first_block": 100},
        "0xf": {"first_block": 300},
    }
    clusters = _build_same_block_clusters(first_buy)
    # Block 100 has only 2 buyers (< min 3) and block 300 a single buyer —
    # both are skipped, but their positions still consume enumerate indices,
    # so the surviving block-200 cluster keeps id "block:1".
    assert len(clusters) == 1
    assert clusters[0]["id"] == "block:1"
    assert clusters[0]["first_block"] == 200
    assert clusters[0]["addresses"] == ["0xa", "0xb", "0xc"]
    assert clusters[0]["evidence"]["first_tx_hashes"] == ["0xt2", "0xt3"]


def test_same_block_cluster_needs_three_buyers() -> None:
    first_buy = {
        "0xa": {"first_block": 1},
        "0xb": {"first_block": 1},
    }
    assert _build_same_block_clusters(first_buy) == []


def test_bundle_cluster_maps_blocks_to_first_in_holders() -> None:
    bundle = {
        "confidence": 80,
        "bundle_blocks": [
            {"block_number": 500, "buyer_count": 3, "total_buys": 9},
            {"block_number": 600},  # no holders in first_in at this block
            {"buyer_count": 5},  # no block number
        ],
    }
    first_in = {
        "0xa": {"first_block": 500},
        "0xb": {"first_block": 500},
        "0xc": {"first_block": 500},
    }
    clusters = _build_bundle_clusters(bundle, first_in)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["id"] == "bundle:500"
    assert c["addresses"] == ["0xa", "0xb", "0xc"]
    assert c["confidence"] == 0.8
    assert c["evidence"]["buyer_count"] == 3
    assert c["evidence"]["total_buys"] == 9


def test_bundle_cluster_confidence_clamps_and_tolerates_garbage() -> None:
    first_in = {"0xa": {"first_block": 7}, "0xb": {"first_block": 7}}
    low = _build_bundle_clusters(
        {"confidence": 10, "bundle_blocks": [{"block_number": 7}]}, first_in
    )
    garbage = _build_bundle_clusters(
        {"confidence": "not-a-number", "bundle_blocks": [{"block_number": 7}]}, first_in
    )
    assert low[0]["confidence"] == 0.4
    assert garbage[0]["confidence"] == 0.4
    assert _build_bundle_clusters(None, first_in) == []


# ── behavioral fingerprint similarity ────────────────────────────────────────


def _rich_fp() -> dict[str, object]:
    hours = [0.0] * _HOURS_BINS
    hours[14] = 0.5
    hours[15] = 0.5
    return {
        "fingerprint": {
            "tx_per_day": 10.0,
            "active_days": 30,
            "burst_score": 0.2,
            "swap_per_day": 5.0,
            "dex_diversity": 3,
            "token_diversity": 10,
            "median_value": 100.0,
            "value_spread": 4.0,
            "top_protocols": ["uniswap", "aave"],
            "active_hours": hours,
            "active_dow": [0.0] * _DOW_BINS,
        }
    }


def test_compare_identical_fingerprints_score_one() -> None:
    fp = _rich_fp()
    assert compare_fingerprints(fp, dict(fp)) == 1.0


def test_compare_empty_or_missing_fingerprints_score_zero() -> None:
    assert compare_fingerprints({}, {}) == 0.0
    assert compare_fingerprints(_rich_fp(), {}) == 0.0


def test_matched_features_full_overlap_lists_every_group() -> None:
    other = _rich_fp()
    other["fingerprint"] = dict(other["fingerprint"])  # type: ignore[index]
    other["fingerprint"]["tx_per_day"] = 11.0  # type: ignore[index]
    other["fingerprint"]["active_days"] = 32  # type: ignore[index]
    other["fingerprint"]["swap_per_day"] = 6.0  # type: ignore[index]
    other["fingerprint"]["token_diversity"] = 12  # type: ignore[index]
    other["fingerprint"]["median_value"] = 150.0  # type: ignore[index]
    other["fingerprint"]["top_protocols"] = ["uniswap", "curve"]  # type: ignore[index]
    matched = matched_features(_rich_fp(), other)
    assert matched == [
        "tx_frequency",
        "activity_level",
        "burst_pattern",
        "swap_frequency",
        "dex_diversity",
        "token_diversity",
        "temporal_pattern",
        "value_distribution",
        "protocol_overlap",
    ]


def test_matched_features_dissimilar_wallets_match_nothing_strong() -> None:
    a = _rich_fp()
    b: dict[str, object] = {
        "fingerprint": {
            "tx_per_day": 100.0,
            "active_days": 1,
            "burst_score": 0.95,
            "swap_per_day": 90.0,
            "dex_diversity": 30,
            "token_diversity": 400,
            "median_value": 100000.0,
            "top_protocols": ["curve"],
            "active_hours": [0.0] * _HOURS_BINS,
        }
    }
    matched = matched_features(a, b)
    assert "tx_frequency" not in matched
    assert "activity_level" not in matched
    assert "temporal_pattern" not in matched
    assert "value_distribution" not in matched
    assert "protocol_overlap" not in matched


def test_matched_features_missing_side_is_not_evidence() -> None:
    assert matched_features(_rich_fp(), {}) == []


def test_confidence_data_volume_steps() -> None:
    assert _confidence(0, 0, 0, []) == 0.3
    assert _confidence(10, 2, 0, []) == 0.5
    assert _confidence(10, 3, 0, []) == 0.7
    assert _confidence(10, 3, 5, []) == 0.9
    assert _confidence(10, 3, 5, ["uniswap"]) == 1.0


def test_cosine_similarity_edges() -> None:
    # 3-4-5 triple keeps norms exact so identical vectors score exactly 1.0.
    assert _cosine([3.0, 4.0], [3.0, 4.0]) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert _cosine([1.0], [1.0, 1.0]) == 0.0  # length mismatch
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector has no direction


def test_normalize_hist_bins_and_ignores_out_of_range() -> None:
    hist = _normalize_hist({0: 1, 23: 3}, 24)
    assert hist[0] == 0.25
    assert hist[23] == 0.75
    assert sum(hist) == 1.0
    # Out-of-range bins are dropped from the histogram but their counts
    # still count toward the normalisation total (documented quirk).
    sparse = _normalize_hist({0: 2, 99: 8}, 24)
    assert sparse[0] == 0.2
    assert sum(sparse) == 0.2
    assert _normalize_hist({}, 7) == [0.0] * 7


def test_numeric_feature_helpers() -> None:
    assert _num({"k": "12.5"}, "k") == 12.5
    assert _num({"k": None}, "k") == 0.0
    assert _num({"k": "abc"}, "k") == 0.0
    assert _norm(5.0, 10.0) == 0.5
    assert _norm(15.0, 10.0) == 1.0  # clamped
    assert _norm(1.0, 0.0) == 0.0  # bad cap
    assert _log_scale(0.0, 100.0) == 0.0
    assert _log_scale(100.0, 100.0) == 1.0
    assert _log_scale(-5.0, 100.0) == 0.0  # negative clamped to zero
    assert _rel_diff(10.0, 10.0) == 0.0
    assert _rel_diff(5.0, 10.0) == 0.5
    assert _rel_diff(0.0, 0.0) == 0.0


# ── wallet flow summary / ranking / node classification ──────────────────────

_W = "0xwallet"
_CEX = "0xcex"
_A = "0xaaaa"
_B = "0xbbbb"


def _flow_graph() -> dict[str, object]:
    return {
        "wallet": _W.upper(),  # summary must lowercase it
        "chain": "eth",
        "hops": 2,
        "nodes": [
            {"id": _W, "type": "EOA", "label": ""},
            {"id": _CEX, "type": "CEX", "label": "Binance"},
            {"id": _A, "type": "EOA", "label": ""},
            {"id": _B, "type": "EOA", "label": ""},
        ],
        "edges": [
            {"from": _A, "to": _W, "usd": 100, "tx_count": 2},
            {"from": _B, "to": _W, "usd": 10, "tx_count": 1},
            {"from": _W, "to": _CEX, "usd": 300, "tx_count": 1},
            {"from": _W, "to": _A, "usd": 50, "tx_count": 1},
            {"from": _W, "to": _B, "usd": 20, "tx_count": 2},
        ],
    }


def test_summarize_flow_totals_counterparties_and_exchange_share() -> None:
    s = _summarize_flow(_flow_graph())
    assert s["wallet"] == _W
    assert s["in_usd"] == 110.0
    assert s["out_usd"] == 370.0
    assert s["net_usd"] == 260.0
    assert s["in_tx_count"] == 3
    assert s["out_tx_count"] == 4
    assert s["usd_unavailable"] is False
    assert s["exchange"] == {
        "in_usd": 0.0,
        "out_usd": 300.0,
        "tx_count": 1,
        "share_pct": 62.5,
        "share_tx_pct": 14.3,
    }
    assert s["counterparty_in"][0] == {
        "address": _A,
        "usd": 100.0,
        "tx_count": 2,
        "type": "EOA",
        "label": None,
    }
    assert [c["address"] for c in s["counterparty_out"]] == [_CEX, _A, _B]


def test_summarize_flow_flags_reciprocal_wash_pairs() -> None:
    graph = _flow_graph()
    graph["edges"] = [  # type: ignore[arg-type]
        *graph["edges"],  # type: ignore[arg-type]
        {"from": _B, "to": _W, "usd": 5, "tx_count": 1},
    ]
    s = _summarize_flow(graph)
    # Two canonical round trips: A<->W (2+1 txs) and B<->W (2+2 txs).
    assert s["wash"] == {"reciprocal_pairs": 2, "tx_count": 7, "signal": True}


def test_summarize_flow_without_usd_marks_unavailable() -> None:
    graph = {
        "wallet": _W,
        "nodes": [],
        "edges": [{"from": _A, "to": _W, "usd": 0, "tx_count": 3}],
    }
    s = _summarize_flow(graph)
    assert s["usd_unavailable"] is True
    assert s["exchange"]["share_pct"] == 0.0
    assert s["exchange"]["share_tx_pct"] == 0.0
    assert s["wash"]["signal"] is False


def test_rank_counterparties_sums_both_directions_and_filters_junk() -> None:
    zero = "0x0000000000000000000000000000000000000000"
    edge_raw = {
        (_W, _A, "tok"): {"raw": 100.0},
        (_B, _W, "tok"): {"raw": 50.0},
        ("0xcccc", "0xdddd", "tok"): {"raw": 999.0},  # unrelated edge
        (_W, "", "tok"): {"raw": 5.0},  # empty address filtered
        (_W, zero, "tok"): {"raw": 7.0},  # zero address filtered
    }
    ranked = _rank_counterparties(_W, edge_raw)
    assert ranked == [(_A, 100.0), (_B, 50.0)]


def test_classify_node_priority_labels_then_contract_then_eoa() -> None:
    labels_cex = [{"label_category": "exchange", "label_name": "Coinbase"}]
    labels_dex = [{"label_category": "", "label_name": "Uniswap V3 Router"}]
    labels_bridge = [{"label_category": "bridge", "label_name": ""}]
    labels_mixer = [{"label_category": "", "label_name": "tornado"}]
    assert _classify_node("0x1", labels_cex, set()) == ("CEX", "Coinbase")
    assert _classify_node("0x2", labels_dex, set()) == ("DEX", "Uniswap V3 Router")
    assert _classify_node("0x3", labels_bridge, set())[0] == "bridge"
    assert _classify_node("0x4", labels_mixer, set())[0] == "mixer"
    assert _classify_node("0x5", [], {"0x5"}) == ("contract", None)
    assert _classify_node("0x6", [], set()) == ("EOA", None)

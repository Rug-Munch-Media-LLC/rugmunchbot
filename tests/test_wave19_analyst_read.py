"""Test wave 19 — deepscan Analyst Read (intel builder + fail-open block).

Contracts read from source (2026-08-22). Pure logic + one async renderer
whose LLM dependency is stubbed at ``rugmunchbot.llm_config``.
"""

from __future__ import annotations

import pytest

from rugmunchbot.formatters._scan_report_deepscan._analyst import (
    _as_dict,
    _build_bundle_intel,
    _safe_float,
    _safe_int,
    render_analyst_read_block,
)

# ── tiny coercion helpers ────────────────────────────────────────────────────


def test_as_dict_and_safe_coercions() -> None:
    assert _as_dict({"k": {"a": 1}}, "k") == {"a": 1}
    assert _as_dict({"k": "junk"}, "k") == {}
    assert _as_dict({}, "k") == {}
    assert _safe_int("7") == 7
    assert _safe_int(None) is None
    assert _safe_int("x") is None
    assert _safe_float("2.5") == 2.5
    assert _safe_float(None) is None


# ── bundle/cluster intel assembly ────────────────────────────────────────────


def _full_scan() -> dict[str, object]:
    return {
        "bundle_detection": {
            "same_slot": {
                "bundle_wallets": 6,
                "same_slot_wallets": 4,
                "same_slot_sol_spent": 12.345,
                "same_slot_held_pct": 55.55,
            },
            "deployer_funded": ["a", "b"],
            "same_block_snipers": ["c"],
        },
        "wallet_map": {
            "roles": {"dev": 1, "whale": 2, "fish": 0},
            "buyer_timing": {
                "fresh_wallet_pct": 66.66,
                "recently_funded_pct": 40.0,
                "fresh_window_hours": 48,
            },
        },
        "holder_analysis": {"top_10_percentage": 78.88, "hhi": 2500.4},
        "contract_creation": {"deployer_funded_percentage": 33.33},
        "cluster_analysis": {
            "result": "scanned",
            "distinct_funders": 2,
            "funded_wallets": 9,
            "largest_cluster_size": 5,
            "coordinated_buyers": 7,
            "verdict": "coordinated",
            "funding_chain_depth": 2,
            "two_hop_chain_buyers": 3,
            "time_nodes": 4,
            "supply_grab": {"status": "holding", "held_pct": 12.34, "dumped_pct": 1.5},
        },
    }


def test_build_bundle_intel_full_payload() -> None:
    intel = _build_bundle_intel(_full_scan())
    # Own-engine bundle count wins over the deployer+sniper sum (3).
    assert intel["bundle_wallet_count"] == 6
    assert intel["same_slot_wallets"] == 4
    assert intel["same_slot_sol_spent"] == 12.35
    assert intel["same_slot_held_pct"] == 55.5  # round(55.55, 1) -> float repr dips
    assert intel["wallet_roles"] == {"dev": 1, "whale": 2}  # zero roles dropped
    assert intel["deployer_funded_pct"] == 33.3
    assert intel["holder_concentration"] == {"top10_pct": 78.9, "hhi": 2500.0}
    assert intel["buyer_timing"] == {
        "fresh_wallet_pct": 67.0,
        "recently_funded_pct": 40.0,
        "fresh_window_hours": 48,
    }
    assert intel["funding_clusters"] == {
        "distinct_funders": 2,
        "funded_wallets": 9,
        "largest_cluster_size": 5,
        "coordinated_buyers": 7,
        "cluster_verdict": "coordinated",
        "funding_chain_depth": 2,
        "two_hop_chain_buyers": 3,
        "time_nodes": 4,
        "supply_grab": {"status": "holding", "held_pct": 12.3, "dumped_pct": 1.5},
    }


def test_build_bundle_intel_bundle_count_fallback_sum() -> None:
    data = {"bundle_detection": {"deployer_funded": ["a"], "same_block_snipers": ["b", "c"]}}
    assert _build_bundle_intel(data)["bundle_wallet_count"] == 3
    # Nothing measurable -> key absent entirely (never a fabricated 0).
    assert "bundle_wallet_count" not in _build_bundle_intel({})


def test_build_bundle_intel_skips_unscanned_cluster() -> None:
    data = {"cluster_analysis": {"result": "unavailable", "distinct_funders": 5}}
    assert "funding_clusters" not in _build_bundle_intel(data)


def test_build_bundle_intel_supply_grab_status_gate() -> None:
    base = {"cluster_analysis": {"result": "scanned"}}
    none_status = dict(
        base, cluster_analysis={"result": "scanned", "supply_grab": {"status": "none"}}
    )
    # A scanned cluster with nothing measurable emits NO funding_clusters
    # key at all — even when a supply_grab carried a "none" status.
    assert "funding_clusters" not in _build_bundle_intel(none_status)
    dumped = dict(
        base,
        cluster_analysis={
            "result": "scanned",
            "supply_grab": {"status": "dumped", "held_pct": 3, "dumped_pct": 97},
        },
    )
    intel = _build_bundle_intel(dumped)["funding_clusters"]["supply_grab"]
    assert intel == {"status": "dumped", "held_pct": 3.0, "dumped_pct": 97.0}


def test_build_bundle_intel_empty_data_is_empty() -> None:
    assert _build_bundle_intel({}) == {}


# ── fail-open renderer ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_render_analyst_read_empty_intel_never_calls_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("LLM must not be called for empty intel")

    monkeypatch.setattr("rugmunchbot.llm_config.chat_with_fallback", boom)
    assert await render_analyst_read_block({}) == ""


@pytest.mark.asyncio
async def test_render_analyst_read_escapes_llm_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_chat(messages: object, tier: str) -> str:
        assert tier == "snappy"
        return "Coordinated launch. <b>Be careful</b>."

    monkeypatch.setattr("rugmunchbot.llm_config.chat_with_fallback", fake_chat)
    out = await render_analyst_read_block(_full_scan())
    assert out.startswith("\U0001f9e0 <b>Analyst Read</b>\n")
    assert "&lt;b&gt;Be careful&lt;/b&gt;" in out  # HTML-safe escape applied


@pytest.mark.asyncio
async def test_render_analyst_read_failures_return_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def explode(messages: object, tier: str) -> str:
        raise RuntimeError("rate limited")

    monkeypatch.setattr("rugmunchbot.llm_config.chat_with_fallback", explode)
    assert await render_analyst_read_block(_full_scan()) == ""

    async def blank(messages: object, tier: str) -> str:
        return "   "

    monkeypatch.setattr("rugmunchbot.llm_config.chat_with_fallback", blank)
    assert await render_analyst_read_block(_full_scan()) == ""

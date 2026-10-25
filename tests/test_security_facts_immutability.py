"""Regression — the verdict fact projection must not mutate the scan result.

Pre-split, ``_verdict_facts`` merged the v2 evidence security checks straight
into the caller's ``result["security_checks"]`` dict (``checks[str(code)] =
detail`` on the object it was handed). The split copies before merging, so the
projected facts must stay identical to the old projection while the caller's
payload is left untouched — the same result dict is re-rendered by the report
formatters after the verdict runs.
"""

from __future__ import annotations

import copy

import pytest

from rugmunchbot.tools.scanner._ai_verdict import _verdict_facts

RICH_RESULT = {
    "name": "T",
    "symbol": "TST",
    "chain": "eth",
    "risk_score": 42.75,
    "risk_level": "high",
    "honeypot": False,
    "mintable": True,
    "can_blacklist": False,
    "owner_renounced": True,
    "contract_verified": True,
    "buy_tax": 1.5,
    "sell_tax": 2.0,
    "lp_locked": 0.85,
    "holders": 812,
    "holder_analysis": {"top_10_pct": 61.2, "top5_pct": 48.0, "sample_size": 300, "source": "ankr"},
    "bundle_analysis": {"launch_cluster_score": 87},
    "flow_analysis": {"participants": 50, "top_mover_share_pct": 61.4, "concentrated_flow": True},
    "rug_prediction": {"rug_probability": 7, "band": "LOW"},
    "deployer_profile": {"total_tokens_launched": 4, "rugged_pct": 50},
    "bundle_detected": True,
    "fresh_wallet_pct": 41.2,
    "exchange_funded_pct": 34.7,
    "threats": ["A", "B"],
    "security_checks": {"LP01": {"status": "pass"}},
}

# Captured from the pre-split implementation (main, commit 255fce6) — the
# fact set the prompt and the grounding check consume.
RICH_EXPECTED = {
    "name": "T",
    "symbol": "TST",
    "chain": "eth",
    "risk_score": 42.8,
    "risk_level": "high",
    "honeypot": False,
    "mintable": True,
    "can_blacklist": False,
    "owner_renounced": True,
    "contract_verified": True,
    "buy_tax": 1.5,
    "sell_tax": 2.0,
    "lp_locked": 0.85,
    "total_holders": 812,
    "top_10_holder_pct": 61.2,
    "top5_holder_pct": 48.0,
    "holder_sample_size": 300,
    "holder_data_source": "ankr",
    "bundle_cluster_score": 87,
    "flow_participants": 50,
    "flow_top_mover_share_pct": 61.4,
    "flow_concentrated": True,
    "rug_prediction": 7,
    "rug_prediction_band": "LOW",
    "deployer_tokens_launched": 4,
    "deployer_rugged_pct": 50,
    "bundle_detected": True,
    "fresh_wallet_pct": 41.2,
    "exchange_funded_pct": 34.7,
    "flagged_security_checks": {"LP01": "pass"},
    "threats": ["A", "B"],
}


def test_facts_identical_to_pre_split_projection():
    """Output values AND key order match the mutating implementation exactly."""
    facts = _verdict_facts(copy.deepcopy(RICH_RESULT))
    assert facts == RICH_EXPECTED
    assert list(facts) == list(RICH_EXPECTED)


def test_caller_security_checks_not_mutated():
    result = {
        "security_checks": {"HP01": {"status": "pass"}, "LP01": {"status": "fail"}},
        "evidence": {
            "security": {
                "checks": {
                    "HP01": {"status": "fail"},
                    "CR03": {"status": "pass"},
                    "HP04": {"status": "warn"},
                }
            }
        },
    }
    before = copy.deepcopy(result)
    facts = _verdict_facts(result)
    assert result == before
    assert sorted(result["security_checks"]) == ["HP01", "LP01"]
    assert facts["honeypot"] == "pass"  # legacy field wins over evidence
    assert facts["mintable"] == "pass"
    assert facts["flagged_security_checks"] == {
        "HP01": "pass",
        "LP01": "fail",
        "CR03": "pass",
    }


def test_evidence_checks_fill_gaps_without_creating_security_checks():
    result = {"evidence": {"security": {"checks": {"HP01": {"status": "fail"}}}}}
    facts = _verdict_facts(result)
    assert "security_checks" not in result
    assert facts["honeypot"] == "fail"
    assert facts["mintable"] is None
    assert facts["flagged_security_checks"] == {"HP01": "fail"}


@pytest.mark.parametrize(
    ("checks", "evidence_checks", "honeypot", "mintable"),
    [
        (None, None, None, None),
        ("junk", "junk", None, None),
        ({}, {}, None, None),
        ({}, {"HP01": {"status": "pass"}}, "pass", None),
        ({}, {"HP04": {"status": "fail"}}, "fail", None),
        ({"HP01": {"status": "pass"}}, {"HP01": {"status": "fail"}}, "pass", None),
        ({"CR03": {"status": "fail"}}, {}, None, "fail"),
        ({}, {"CR03": {"status": None}}, None, None),
    ],
)
def test_missing_or_null_checks_map_correctly(checks, evidence_checks, honeypot, mintable):
    result: dict = {}
    if checks is not None:
        result["security_checks"] = checks
    if evidence_checks is not None:
        result["evidence"] = {"security": {"checks": evidence_checks}}
    before = copy.deepcopy(result)
    facts = _verdict_facts(result)
    assert (facts["honeypot"], facts["mintable"]) == (honeypot, mintable)
    assert result == before  # never mutated, whatever the input shape


def test_repeat_calls_are_stable_and_mutation_free():
    result = copy.deepcopy(RICH_RESULT)
    first = _verdict_facts(result)
    second = _verdict_facts(result)
    assert first == second == RICH_EXPECTED
    assert result == RICH_RESULT


def test_junk_locked_pct_yields_none_without_mutation():
    result = {"evidence": {"liquidity": {"locked_pct": "junk"}}}
    facts = _verdict_facts(result)
    assert facts["lp_locked"] is None
    assert result == {"evidence": {"liquidity": {"locked_pct": "junk"}}}


def test_facts_do_not_alias_caller_checks():
    result = {
        "security_checks": {"HP01": {"status": "pass"}},
        "evidence": {"security": {"checks": {"CR03": {"status": "fail"}}}},
    }
    facts = _verdict_facts(result)
    facts["flagged_security_checks"]["CR03"] = "pass"
    assert result["evidence"]["security"]["checks"]["CR03"] == {"status": "fail"}
    assert "CR03" not in result["security_checks"]

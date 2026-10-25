"""Fact projection for the snappy-tier AI verdict.

The LLM verdict may cite ONLY these facts, so every value is optional
(``None`` when absent) and nothing is derived. Two payload shapes are read:
the legacy top-level fields (``honeypot``/``mintable``/``buy_tax``…) and the
v2 ``evidence`` security/holders/liquidity blocks (HP01 honeypot, CR03
mintable, LP, holders).

Each fact family lives in its own helper and :func:`verdict_facts` composes
them in the ORIGINAL key order -- the prompt slices the serialised facts at
600 characters, so reordering would change which facts the model sees.
"""

from __future__ import annotations

from typing import Any

THREAT_CAP = 6


def _as_dict(value: object) -> dict[str, Any]:
    """The value when it is a dict, else an empty one (hostile payloads)."""
    return value if isinstance(value, dict) else {}


def _merged_checks(result: dict[str, Any]) -> dict[str, Any]:
    """Legacy ``security_checks`` first; v2 evidence checks fill the gaps."""
    evidence = _as_dict(result.get("evidence"))
    ev_checks = _as_dict(_as_dict(evidence.get("security")).get("checks"))
    checks = dict(_as_dict(result.get("security_checks")))
    for code, detail in ev_checks.items():
        if isinstance(detail, dict) and code not in checks:
            checks[str(code)] = detail
    return checks


def _check_status(checks: dict[str, Any], code: str) -> str | None:
    """Status of one check code, or ``None`` when the engine did not answer."""
    detail = checks.get(code)
    return detail.get("status") if isinstance(detail, dict) else None


def _risk_fact(result: dict[str, Any]) -> float | None:
    """Risk score rounded to one decimal, or ``None`` when not numeric."""
    risk = result.get("risk_score")
    if risk is None:
        return None
    try:
        return round(float(risk), 1)
    except (TypeError, ValueError):
        return None


def _base_facts(result: dict[str, Any]) -> dict[str, Any]:
    """Identity + headline score."""
    return {
        "name": result.get("name"),
        "symbol": result.get("symbol"),
        "chain": result.get("chain"),
        "risk_score": _risk_fact(result),
        "risk_level": result.get("risk_level"),
    }


def _lp_lock_fact(result: dict[str, Any], ev_liq: dict[str, Any]) -> float | None:
    """LP lock as a fraction, converting the evidence percentage when needed."""
    lp_locked = result.get("lp_locked")
    if lp_locked is not None or ev_liq.get("locked_pct") is None:
        return lp_locked
    try:
        return float(ev_liq["locked_pct"]) / 100
    except (TypeError, ValueError):
        return None


def _security_facts(result: dict[str, Any], checks: dict[str, Any]) -> dict[str, Any]:
    """Contract/authority facts, legacy fields winning over evidence checks.

    Explicit is-not-None checks: a meaningful False ("not a honeypot") must
    survive — `or` chains erase it to None and the LLM loses the distinction
    between "clean" and "unknown".
    """
    honeypot = result.get("honeypot")
    if honeypot is None:
        honeypot = _check_status(checks, "HP01") or _check_status(checks, "HP04")
    mintable = result.get("mintable")
    if mintable is None:
        mintable = _check_status(checks, "CR03")
    evidence = _as_dict(result.get("evidence"))
    return {
        "honeypot": honeypot,
        "mintable": mintable,
        "can_blacklist": result.get("can_blacklist"),
        "owner_renounced": result.get("owner_renounced"),
        "contract_verified": result.get("contract_verified"),
        "buy_tax": result.get("buy_tax"),
        "sell_tax": result.get("sell_tax"),
        "lp_locked": _lp_lock_fact(result, _as_dict(evidence.get("liquidity"))),
    }


def _holder_facts(result: dict[str, Any]) -> dict[str, Any]:
    """Holder count, concentration and analysis depth."""
    ev_holders = _as_dict(_as_dict(result.get("evidence")).get("holders"))
    analysis = _as_dict(result.get("holder_analysis"))
    total_holders = result.get("holders")
    if not total_holders:
        total_holders = ev_holders.get("holder_count")
    return {
        "total_holders": total_holders,
        "top_10_holder_pct": analysis.get("top_10_pct") or ev_holders.get("top_10_pct"),
        "top5_holder_pct": analysis.get("top5_pct"),
        "holder_sample_size": analysis.get("sample_size"),
        "holder_data_source": analysis.get("source"),
    }


def _signal_facts(result: dict[str, Any]) -> dict[str, Any]:
    """Bundle, flow, rug-prediction and deployer track-record facts."""
    bundle = _as_dict(result.get("bundle_analysis"))
    flow = _as_dict(result.get("flow_analysis"))
    prediction = _as_dict(result.get("rug_prediction"))
    deployer = _as_dict(result.get("deployer_profile"))
    return {
        "bundle_cluster_score": bundle.get("launch_cluster_score"),
        "flow_participants": flow.get("participants"),
        "flow_top_mover_share_pct": flow.get("top_mover_share_pct"),
        "flow_concentrated": flow.get("concentrated_flow"),
        "rug_prediction": prediction.get("rug_probability"),
        "rug_prediction_band": prediction.get("band"),
        "deployer_tokens_launched": deployer.get("total_tokens_launched"),
        "deployer_rugged_pct": deployer.get("rugged_pct"),
        "bundle_detected": result.get("bundle_detected"),
        "fresh_wallet_pct": result.get("fresh_wallet_pct"),
        "exchange_funded_pct": result.get("exchange_funded_pct"),
    }


def _flagged_checks(checks: dict[str, Any]) -> dict[str, Any]:
    """Per-code status for checks that produced a pass/fail verdict."""
    return {
        str(code): (detail.get("status") if isinstance(detail, dict) else None)
        for code, detail in checks.items()
        if isinstance(detail, dict) and detail.get("status") in ("pass", "fail")
    }


def _threats(result: dict[str, Any]) -> list[str]:
    """Risk flags (or legacy threats), truthy only, capped for the prompt."""
    raw = result.get("risk_flags") or result.get("threats") or []
    return [str(flag) for flag in raw if flag][:THREAT_CAP]


def verdict_facts(result: dict[str, Any]) -> dict[str, Any]:
    """Project the structured scan result onto the citable fact set."""
    checks = _merged_checks(result)
    return {
        **_base_facts(result),
        **_security_facts(result, checks),
        **_holder_facts(result),
        **_signal_facts(result),
        "flagged_security_checks": _flagged_checks(checks),
        "threats": _threats(result),
    }

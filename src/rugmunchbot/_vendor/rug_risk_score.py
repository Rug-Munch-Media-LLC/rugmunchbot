# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/rug_risk_score.py (Phase 2a — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Consolidated rug-risk scorer — the "25-point analysis".

Combines every enrichment signal (contract authority, supply, liquidity,
trading, deployer reputation, social cross-ref, whale flow, age) into a
single 0-100 rug-risk score, one verdict, and a per-signal breakdown.

Design
------
* PURE — ``rug_risk_score(signals)`` only reads the ``signals`` dict: no
  I/O, no DB, no network, no sibling-module imports. The caller (scan
  pipeline / bot lead) is responsible for fetching each signal and passing
  it in as a dict. Parallel enrichment modules that have not landed yet
  (deployer_reputation, wash_trading_enrich, whale_flow_enrich,
  authority_check, social_crossref, gmgn_enrich, token_launch_detector,
  shill_detector) are simply NOT required here — the scorer is decoupled
  by design and only consumes their output dicts. Importing them is never
  attempted; their result keys are parsed tolerantly instead.
* DEFENSIVE — every signal is parsed tolerantly (bool, dict, str, number
  all accepted where sensible); missing/None values mean "absence of
  evidence", never a penalty. Malformed input never raises.
* KEY-TOLERANT — both flat values (``{"honeypot": True}``) and nested
  module output (``{"security": {"honeypot": True}}``,
  ``{"deployer": {"rug_history": True}}``,
  ``{"gmgn": {"holders": {"sniper_count": 5}}}``) are accepted; every rule
  probes a list of key paths.
* MONOTONIC — adding any risk signal can only raise the score; only the
  ``kol`` mitigation subtracts.
* FAIL-OPEN — ``rug_risk_score(None)`` or a garbage dict returns a
  low-risk result (score 0, verdict "low", no flags, empty analysis).

Signal categories (the "25-point" breakdown)
---------------------------------------------
* CONTRACT (7): honeypot, mintable, freezable, lp_unlocked, renounced,
  proxy_upgradable, ownership_transferable
* SUPPLY (3): mint_supply_risk, supply_concentrated, burn_disabled
* LIQUIDITY (4): low_liquidity, lp_removed, lp_burned_pct, single_pool
* TRADING (4): wash_trading, volume_concentrated, bundle_suspect,
  sniper_cluster
* DEPLOYER (4): repeat_deployer, rug_history, bad_funder, fresh_wallet
* SOCIAL (3): known_scammer, kol_pump, coordinated_shill
* MOMENTUM (3): very_young, whale_exit, smart_money_exit

``renounced`` and ``lp_burned_pct`` are inverted protective signals: they
fire when ownership is confirmed *not* renounced / LP is confirmed mostly
*unburned* — the risky states. An explicit ``None``/missing value never
fires either (absence of evidence is not a penalty).

Weighting tiers
---------------
* near-critical (+40/+40/+40/+35): honeypot, known_scammer, rug_history,
  mintable
* high (+30/+28/+25/+25/+22/+22/+22/+20): lp_removed, mint_supply_risk,
  bad_funder, bundle_suspect, repeat_deployer, lp_unlocked, wash_trading
* medium (+18..+12): freezable, coordinated_shill, proxy_upgradable,
  supply_concentrated, whale_exit, kol_pump, ownership_transferable,
  sniper_cluster, low_liquidity, very_young, smart_money_exit
* low (+10..+6): burn_disabled, volume_concentrated, fresh_wallet,
  renounced, lp_burned_pct, single_pool
* mitigation (-10): kol_verified
* hard floor: a confirmed honeypot or known scammer is always >=
  ``CRITICAL_MIN`` (a lone honeypot is still critical).

Verdict thresholds: ``score >= 70`` → critical, ``>= 45`` → high,
``>= 25`` → medium, else low. [REVIEW] — thresholds and per-signal
weights are initial calibration; tune against labelled scan history
before release.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    #: Rule = (flag, weight, reason, group, sources) where each source is a
    #: ``(key-paths, predicate)`` pair probed in order.
    _RuleSource = tuple[tuple[tuple[str, ...], ...], Callable[[Any], bool]]
    _Rule = tuple[str, int, str, str, tuple[_RuleSource, ...]]

# ── Degodded split (2026-08-14) ────────────────────────────────────────
# Pure helpers live in sibling submodules and are re-exported here so every
# existing importer keeps the same names / objects (NO behavior change).
from ._rules import _RULES
from ._signals import (  # noqa: F401 re-export
    _bad_funder,
    _bundle_suspect,
    _coordinated_shill,
    _fresh_wallet,
    _kol_pump,
    _kol_verified,
    _low_liquidity,
    _lp_burned_pct,
    _lp_removed,
    _lp_unlocked,
    _probe,
    _renounced,
    _repeat_deployer,
    _rug_history,
    _signal_bool,
    _single_pool,
    _smart_money_exit,
    _sniper_cluster,
    _supply_concentrated,
    _to_float,
    _to_pct,
    _truthy_scalar,
    _very_young,
    _volume_concentrated,
    _whale_exit,
)
from ._text import _verdict_for, risk_analysis_text, risk_verdict_text
from ._weights import (
    BAD_FUNDER_WEIGHT,
    BUNDLE_WEIGHT,
    BUNDLED_WEIGHT,
    BURN_DISABLED_WEIGHT,
    COORDINATED_SHILL_WEIGHT,
    CRITICAL_MIN,
    DEPLOYER_REPEAT_WEIGHT,
    FREEZABLE_WEIGHT,
    FRESH_WALLET_SECONDS,
    FRESH_WALLET_WEIGHT,
    HIGH_MIN,
    HONEYPOT_WEIGHT,
    KNOWN_SCAMMER_WEIGHT,
    KOL_MITIGATION,
    KOL_PUMP_WEIGHT,
    LOW_LIQUIDITY_USD,
    LOW_LIQUIDITY_WEIGHT,
    LP_BURNED_PCT_MIN,
    LP_BURNED_PCT_WEIGHT,
    LP_REMOVED_WEIGHT,
    LP_UNLOCKED_PCT_MAX,
    LP_UNLOCKED_WEIGHT,
    MEDIUM_MIN,
    MINT_SUPPLY_RISK_WEIGHT,
    MINTABLE_WEIGHT,
    OWNERSHIP_TRANSFERABLE_WEIGHT,
    PROXY_UPGRADABLE_WEIGHT,
    RENOUNCED_WEIGHT,
    REPEAT_DEPLOYER_MIN,
    REPEAT_DEPLOYER_WEIGHT,
    RUG_HISTORY_WEIGHT,
    SINGLE_POOL_MAX,
    SINGLE_POOL_WEIGHT,
    SMART_MONEY_EXIT_WEIGHT,
    SNIPER_CLUSTER_MIN,
    SNIPER_CLUSTER_WEIGHT,
    SUPPLY_CONCENTRATED_PCT,
    SUPPLY_CONCENTRATED_WEIGHT,
    VERY_YOUNG_SECONDS,
    VERY_YOUNG_WEIGHT,
    VOLUME_CONCENTRATED_PCT,
    VOLUME_CONCENTRATED_WEIGHT,
    WASH_TRADING_WEIGHT,
    WEIGHTS,
    WHALE_EXIT_WEIGHT,
)


# ── Public API ──────────────────────────────────────────────────────────
def _clean() -> dict[str, Any]:
    return {"score": 0, "verdict": "low", "flags": [], "reasons": [], "analysis": []}


def rug_risk_score(signals: dict[str, Any]) -> dict[str, Any]:
    """Score a token's rug risk from an already-computed signal dict.

    Args:
        signals: dict of enrichment signals — flat values
            (``honeypot``, ``mintable``, ``lp_locked``, ``bundled``,
            ``wash_trading``, ``deployer_repeat``, ``rug_history``,
            ``known_scammer``, ``kol``, ``whale_flow``, ``age_seconds``,
            ``liquidity``, ...) or nested module output
            (``security``, ``deployer``, ``authority``, ``holders``,
            ``gmgn``, ``smart_money``, ``shill``, ...). Values may be
            bools, dicts, strings, or numbers; anything missing is treated
            as "no signal". Never raises.

    Returns:
        ``{"score": int (0-100), "verdict": "critical"|"high"|"medium"|"low",
        "flags": [str], "reasons": [str], "analysis": [{"signal", "weight",
        "detail"}]}`` — each contributing signal yields one machine flag,
        one human-readable reason, and one analysis entry (the "25-point
        breakdown").
    """
    if not isinstance(signals, dict):
        return _clean()

    score = 0
    flags: list[str] = []
    reasons: list[str] = []
    analysis: list[dict[str, Any]] = []

    for flag, weight, reason, _group, sources in _RULES:
        hit = False
        for paths, predicate in sources:
            try:
                fired = bool(predicate(_probe(signals, paths)))
            except Exception:  # defensive: a bad signal never raises
                fired = False
            if fired:
                hit = True
                break
        if hit:
            score += weight
            flags.append(flag)
            reasons.append(reason)
            analysis.append({"signal": flag, "weight": weight, "detail": reason})

    try:
        kol_hit = _kol_verified(_probe(signals, (("kol",), ("social", "kol"), ("crossref",))))
    except Exception:  # defensive
        kol_hit = False
    if kol_hit:
        score -= KOL_MITIGATION
        flags.append("kol_verified")
        reasons.append("wallet belongs to a verified KOL (mitigating)")
        analysis.append(
            {
                "signal": "kol_verified",
                "weight": -KOL_MITIGATION,
                "detail": "wallet belongs to a verified KOL (mitigating)",
            }
        )

    score = max(0, min(100, score))
    # Hard floor: a confirmed honeypot or known scammer is always critical —
    # identity is verified, the risk is not probabilistic.
    if any(flag in flags for flag in ("honeypot", "known_scammer")):
        score = max(score, CRITICAL_MIN)

    return {"score": score, "verdict": _verdict_for(score), "flags": flags, "reasons": reasons, "analysis": analysis}


__all__ = [
    "BAD_FUNDER_WEIGHT",
    "BUNDLED_WEIGHT",
    "BUNDLE_WEIGHT",
    "BURN_DISABLED_WEIGHT",
    "COORDINATED_SHILL_WEIGHT",
    "CRITICAL_MIN",
    "DEPLOYER_REPEAT_WEIGHT",
    "FREEZABLE_WEIGHT",
    "FRESH_WALLET_SECONDS",
    "FRESH_WALLET_WEIGHT",
    "HIGH_MIN",
    "HONEYPOT_WEIGHT",
    "KNOWN_SCAMMER_WEIGHT",
    "KOL_MITIGATION",
    "KOL_PUMP_WEIGHT",
    "LOW_LIQUIDITY_USD",
    "LOW_LIQUIDITY_WEIGHT",
    "LP_BURNED_PCT_MIN",
    "LP_BURNED_PCT_WEIGHT",
    "LP_REMOVED_WEIGHT",
    "LP_UNLOCKED_PCT_MAX",
    "LP_UNLOCKED_WEIGHT",
    "MEDIUM_MIN",
    "MINTABLE_WEIGHT",
    "MINT_SUPPLY_RISK_WEIGHT",
    "OWNERSHIP_TRANSFERABLE_WEIGHT",
    "PROXY_UPGRADABLE_WEIGHT",
    "RENOUNCED_WEIGHT",
    "REPEAT_DEPLOYER_MIN",
    "REPEAT_DEPLOYER_WEIGHT",
    "RUG_HISTORY_WEIGHT",
    "SINGLE_POOL_MAX",
    "SINGLE_POOL_WEIGHT",
    "SMART_MONEY_EXIT_WEIGHT",
    "SNIPER_CLUSTER_MIN",
    "SNIPER_CLUSTER_WEIGHT",
    "SUPPLY_CONCENTRATED_PCT",
    "SUPPLY_CONCENTRATED_WEIGHT",
    "VERY_YOUNG_SECONDS",
    "VERY_YOUNG_WEIGHT",
    "VOLUME_CONCENTRATED_PCT",
    "VOLUME_CONCENTRATED_WEIGHT",
    "WASH_TRADING_WEIGHT",
    "WEIGHTS",
    "WHALE_EXIT_WEIGHT",
    "risk_analysis_text",
    "risk_verdict_text",
    "rug_risk_score",
]

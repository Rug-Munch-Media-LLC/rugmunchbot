# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/deep_profile.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""SENTINEL deep-profile collection — module results → typed ``DeepScanProfile``.

Extracted from ``unified/scanner.py`` (Phase 5b — deep tier only) so the
SENTINEL module → typed-profile mapping lives beside the other pure mapping
helpers instead of bloating the scanner orchestration class.

This module is a pure mapping: every source module is extracted defensively
(a module that returned ``None``, failed, or lacks a field simply leaves its
target profile field at the dataclass default). Never raises — callers
additionally wrap the whole call in try/except as a final guard.

Module → profile wiring:
  * exchange_funding + holder_analysis → WalletBreakdown
  * bundle_detection                    → BundleProfile
  * dev_reputation + gas_trace +
    cross_chain_trace + sleep_cycle     → DeployerProfile
  * wash_trading / honeypot_detection / mev_detection / sentiment /
    social_velocity / pump_dump_detection / flash_loan_detection /
    oracle_manipulation                → scalar risk fields
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.unified_scanner._coercion import (
    _f,
    _funded_from,
    _honeypot_risk,
    _i,
    _mev_exposure,
    _risk_level_hot,
    _sleep_pattern,
    _social_velocity,
)
from rugmunchbot._vendor.unified_scanner.model import (
    BundleProfile,
    DeepScanProfile,
    DeployerProfile,
    WalletBreakdown,
)

__all__ = ["collect_deep_profile", "module_result"]


def module_result(sentinel: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    """Extract one SENTINEL module result from the phase dict.

    The sentinel phase dict groups results under ``free``/``premium``
    buckets; each entry is a plain dict (post ``dataclass_to_dict``) or
    ``None`` when the module failed/skipped.  Returns ``None`` for
    missing or failed modules so callers degrade gracefully.
    """
    if not isinstance(sentinel, dict):
        return None
    for bucket in ("free", "premium"):
        mods = sentinel.get(bucket)
        if isinstance(mods, dict):
            res = mods.get(key)
            if isinstance(res, dict):
                return res
    return None


def collect_deep_profile(sentinel: dict[str, Any] | None) -> DeepScanProfile:
    """Map ALL SENTINEL module results into a typed ``DeepScanProfile``.

    Every source module is extracted defensively: a module that returned
    ``None``, failed, or lacks a field simply leaves its target profile
    field at the dataclass default.  Never raises — callers additionally
    wrap this in try/except as a final guard.
    """
    profile = DeepScanProfile()

    # ── WalletBreakdown (exchange_funding + holder_analysis) ──
    funding = module_result(sentinel, "exchange_funding")
    holders = module_result(sentinel, "holder_analysis")
    if funding or holders:
        fresh_wallet_pct = _f((funding or {}).get("fresh_wallet_percentage"))
        if not fresh_wallet_pct:
            fresh_wallet_pct = _f((holders or {}).get("new_wallets_percentage"))
        exchange_funded_pct = _f((funding or {}).get("cex_funded_percentage"))
        if not exchange_funded_pct:
            exchange_funded_pct = _f((holders or {}).get("exchange_funded_percentage"))
        profile.wallet_breakdown = WalletBreakdown(
            total_holders=_i((holders or {}).get("total_holders")),
            fresh_wallet_count=_i((funding or {}).get("fresh_wallet_count")),
            fresh_wallet_pct=round(fresh_wallet_pct, 2),
            exchange_funded_count=_i((funding or {}).get("cex_funded_count")),
            exchange_funded_pct=round(exchange_funded_pct, 2),
            instant_buy_count=_i((funding or {}).get("instant_buy_count")),
            instant_buy_pct=_f((funding or {}).get("instant_buy_percentage")),
            exchange_breakdown=dict((funding or {}).get("exchange_breakdown") or {}),
            fake_diversification_score=_f((holders or {}).get("fake_diversification_score")),
            hhi=_f((holders or {}).get("hhi")),
            hhi_risk_level=str((holders or {}).get("risk_level", "unknown") or "unknown"),
        )

    # ── BundleProfile (bundle_detection) ──
    bundle = module_result(sentinel, "bundle_detection")
    if bundle:
        deployer_funded = bundle.get("deployer_funded")
        same_block = bundle.get("same_block_snipers")
        behavior_clusters = bundle.get("behavior_clusters")
        deployer_funded = deployer_funded if isinstance(deployer_funded, list) else []
        same_block = same_block if isinstance(same_block, list) else []
        behavior_clusters = behavior_clusters if isinstance(behavior_clusters, list) else []
        cross_launch = bundle.get("cross_launch_flags")
        cross_launch = cross_launch if isinstance(cross_launch, dict) else {}
        profile.bundle_profile = BundleProfile(
            bundles_detected=len(deployer_funded) + len(same_block),
            deployer_funded_wallets=len(deployer_funded),
            deployer_funded_pct=_f(bundle.get("total_bundled_percentage")),
            same_block_snipers=len(same_block),
            jito_bundles=_i(bundle.get("jito_bundles_detected")),
            behavior_clusters=len(behavior_clusters),
            cross_launch_repeat_wallets=_i(cross_launch.get("repeat_count")),
            bundle_risk_score=_i(bundle.get("risk_score")),
            bundle_risk_level=str(bundle.get("risk_level", "LOW") or "LOW"),
        )

    # ── DeployerProfile (dev_reputation + gas_trace + cross_chain_trace + sleep_cycle) ──
    dev = module_result(sentinel, "dev_reputation")
    gas = module_result(sentinel, "gas_trace")
    xchain = module_result(sentinel, "cross_chain_trace")
    sleep = module_result(sentinel, "sleep_cycle")
    if dev or gas or xchain or sleep:
        chains_involved = (xchain or {}).get("chains_involved")
        dev_chains = (dev or {}).get("chains")
        chains_active = chains_involved if isinstance(chains_involved, list) else None
        if not chains_active:
            chains_active = dev_chains if isinstance(dev_chains, list) else []
        profile.deployer_profile = DeployerProfile(
            # dev_reputation does not currently expose wallet age — the
            # field stays at its default (0) until a source exists.
            wallet_age_days=_i((dev or {}).get("wallet_age_days")),
            total_deployments=_i((dev or {}).get("total_launches")),
            rug_rate=round(_f((dev or {}).get("rug_rate")) * 100.0, 2),  # 0-1 fraction → pct
            funded_from=_funded_from(gas),
            chains_active=list(chains_active),
            sleep_pattern=_sleep_pattern(sleep),
        )

    # ── Scalar risk fields (premium modules) ──
    wash = module_result(sentinel, "wash_trading")
    profile.wash_trading_score = _f((wash or {}).get("wash_score"))
    profile.honeypot_risk = _honeypot_risk(module_result(sentinel, "honeypot_detection"))
    profile.mev_exposure = _mev_exposure(module_result(sentinel, "mev_detection"))
    profile.sentiment_score = _f((module_result(sentinel, "sentiment") or {}).get("overall_sentiment"))
    profile.social_velocity = _social_velocity(module_result(sentinel, "social_velocity"))

    pump_dump = module_result(sentinel, "pump_dump_detection") or {}
    profile.pump_dump_detected = bool(pump_dump) and (
        _f(pump_dump.get("risk_score")) >= 50 or _risk_level_hot(pump_dump)
    )

    flash = module_result(sentinel, "flash_loan_detection") or {}
    profile.flash_loan_risk = bool(flash) and (
        _f(flash.get("risk_score")) >= 50 or _risk_level_hot(flash) or _i(flash.get("suspect_trade_count")) > 0
    )

    oracle = module_result(sentinel, "oracle_manipulation") or {}
    profile.oracle_manipulation_risk = bool(oracle) and (
        _f(oracle.get("risk_score")) >= 50 or _f(oracle.get("manipulable_score")) >= 50 or _risk_level_hot(oracle)
    )

    return profile

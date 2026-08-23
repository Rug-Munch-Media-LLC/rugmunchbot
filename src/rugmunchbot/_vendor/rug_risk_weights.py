# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/_weights.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Tunable weights, thresholds, and verdict bands for the rug-risk scorer.

Extracted from ``rug_risk_score.py`` (degodding split, 2026-08-14). Pure
constants — no I/O. Re-exported from the parent module so every existing
importer keeps the same names and function/constant objects.
"""

from __future__ import annotations

# ── Tunables ────────────────────────────────────────────────────────────
HONEYPOT_WEIGHT = 40  # near-critical: confirmed sell-blocking
KNOWN_SCAMMER_WEIGHT = 40  # near-critical: identity confirmed
RUG_HISTORY_WEIGHT = 40  # near-critical: documented rug past
MINTABLE_WEIGHT = 35  # near-critical: unlimited supply lever
LP_REMOVED_WEIGHT = 30  # high: liquidity yanked (rug exit)
MINT_SUPPLY_RISK_WEIGHT = 28  # high: mint authority on live supply
BAD_FUNDER_WEIGHT = 25  # high: funded by known bad actor / mixer
BUNDLE_WEIGHT = 25  # high: coordinated launch
BUNDLED_WEIGHT = BUNDLE_WEIGHT  # backward-compat alias
REPEAT_DEPLOYER_WEIGHT = 22  # high: repeat-rug history
DEPLOYER_REPEAT_WEIGHT = REPEAT_DEPLOYER_WEIGHT  # backward-compat alias
LP_UNLOCKED_WEIGHT = 22  # high: rug exit possible
WASH_TRADING_WEIGHT = 20  # high: fake volume
FREEZABLE_WEIGHT = 18  # medium: balances can be frozen
COORDINATED_SHILL_WEIGHT = 18  # medium: shill ring activity
PROXY_UPGRADABLE_WEIGHT = 15  # medium: contract can be swapped
SUPPLY_CONCENTRATED_WEIGHT = 15  # medium: top-holder concentration
WHALE_EXIT_WEIGHT = 15  # medium: smart-money outflow
KOL_PUMP_WEIGHT = 15  # medium: KOL pushing the token
OWNERSHIP_TRANSFERABLE_WEIGHT = 12  # medium: owner can transfer
SNIPER_CLUSTER_WEIGHT = 12  # medium: coordinated early buys
LOW_LIQUIDITY_WEIGHT = 12  # medium
VERY_YOUNG_WEIGHT = 12  # medium
SMART_MONEY_EXIT_WEIGHT = 12  # medium: smart money selling
BURN_DISABLED_WEIGHT = 10  # low: no deflation lever
VOLUME_CONCENTRATED_WEIGHT = 10  # low: one maker dominates volume
FRESH_WALLET_WEIGHT = 10  # low: new / first-time deployer
RENOUNCED_WEIGHT = 8  # low: ownership NOT renounced
LP_BURNED_PCT_WEIGHT = 8  # low: LP mostly unburned
SINGLE_POOL_WEIGHT = 6  # low: one pool only
KOL_MITIGATION = 10  # verified KOL wallet reduces risk

CRITICAL_MIN = 70
HIGH_MIN = 45
MEDIUM_MIN = 25

LP_UNLOCKED_PCT_MAX = 50.0  # % locked below which LP counts as unlocked
VERY_YOUNG_SECONDS = 3600.0  # < 1h old
LOW_LIQUIDITY_USD = 10_000.0  # < $10k pooled liquidity
SUPPLY_CONCENTRATED_PCT = 60.0  # top-10 holders >= 60% of supply
LP_BURNED_PCT_MIN = 50.0  # burn % below this → "mostly unburned"
SNIPER_CLUSTER_MIN = 2  # GMGN sniper holders at/above this = cluster
VOLUME_CONCENTRATED_PCT = 40.0  # top maker share at/above this
REPEAT_DEPLOYER_MIN = 2  # prior deployments at/above this = repeat
FRESH_WALLET_SECONDS = 7 * 24 * 3600.0  # wallet younger than 7 days
SINGLE_POOL_MAX = 1  # pool_count at/below this = single pool

WEIGHTS: dict[str, int] = {
    "honeypot": HONEYPOT_WEIGHT,
    "known_scammer": KNOWN_SCAMMER_WEIGHT,
    "rug_history": RUG_HISTORY_WEIGHT,
    "mintable": MINTABLE_WEIGHT,
    "lp_removed": LP_REMOVED_WEIGHT,
    "mint_supply_risk": MINT_SUPPLY_RISK_WEIGHT,
    "bad_funder": BAD_FUNDER_WEIGHT,
    "bundle_suspect": BUNDLE_WEIGHT,
    "repeat_deployer": REPEAT_DEPLOYER_WEIGHT,
    "lp_unlocked": LP_UNLOCKED_WEIGHT,
    "wash_trading": WASH_TRADING_WEIGHT,
    "freezable": FREEZABLE_WEIGHT,
    "coordinated_shill": COORDINATED_SHILL_WEIGHT,
    "proxy_upgradable": PROXY_UPGRADABLE_WEIGHT,
    "supply_concentrated": SUPPLY_CONCENTRATED_WEIGHT,
    "whale_exit": WHALE_EXIT_WEIGHT,
    "kol_pump": KOL_PUMP_WEIGHT,
    "ownership_transferable": OWNERSHIP_TRANSFERABLE_WEIGHT,
    "sniper_cluster": SNIPER_CLUSTER_WEIGHT,
    "low_liquidity": LOW_LIQUIDITY_WEIGHT,
    "very_young": VERY_YOUNG_WEIGHT,
    "smart_money_exit": SMART_MONEY_EXIT_WEIGHT,
    "burn_disabled": BURN_DISABLED_WEIGHT,
    "volume_concentrated": VOLUME_CONCENTRATED_WEIGHT,
    "fresh_wallet": FRESH_WALLET_WEIGHT,
    "renounced": RENOUNCED_WEIGHT,
    "lp_burned_pct": LP_BURNED_PCT_WEIGHT,
    "single_pool": SINGLE_POOL_WEIGHT,
    "kol_verified": -KOL_MITIGATION,
}

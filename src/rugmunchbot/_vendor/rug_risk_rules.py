# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/_rules.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Scoring rule table for the rug-risk scorer.

Extracted from ``rug_risk_score.py`` (degodding split, 2026-08-14). Pure
data — each rule is ``(flag, weight, reason, group, sources)`` where every
source is a ``(key-paths, predicate)`` pair probed in order. Re-exported
from the parent module; ``rug_risk_score()`` iterates it unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.rug_risk_signals import (
    _bad_funder,
    _bundle_suspect,
    _coordinated_shill,
    _fresh_wallet,
    _kol_pump,
    _low_liquidity,
    _lp_burned_pct,
    _lp_removed,
    _lp_unlocked,
    _renounced,
    _repeat_deployer,
    _rug_history,
    _signal_bool,
    _single_pool,
    _smart_money_exit,
    _sniper_cluster,
    _supply_concentrated,
    _truthy_scalar,
    _very_young,
    _volume_concentrated,
    _whale_exit,
)
from rugmunchbot._vendor.rug_risk_weights import (
    BAD_FUNDER_WEIGHT,
    BUNDLE_WEIGHT,
    BURN_DISABLED_WEIGHT,
    COORDINATED_SHILL_WEIGHT,
    FREEZABLE_WEIGHT,
    FRESH_WALLET_WEIGHT,
    HONEYPOT_WEIGHT,
    KNOWN_SCAMMER_WEIGHT,
    KOL_PUMP_WEIGHT,
    LOW_LIQUIDITY_WEIGHT,
    LP_BURNED_PCT_WEIGHT,
    LP_REMOVED_WEIGHT,
    LP_UNLOCKED_WEIGHT,
    MINT_SUPPLY_RISK_WEIGHT,
    MINTABLE_WEIGHT,
    OWNERSHIP_TRANSFERABLE_WEIGHT,
    PROXY_UPGRADABLE_WEIGHT,
    RENOUNCED_WEIGHT,
    REPEAT_DEPLOYER_WEIGHT,
    RUG_HISTORY_WEIGHT,
    SINGLE_POOL_WEIGHT,
    SMART_MONEY_EXIT_WEIGHT,
    SNIPER_CLUSTER_WEIGHT,
    SUPPLY_CONCENTRATED_WEIGHT,
    VERY_YOUNG_WEIGHT,
    VOLUME_CONCENTRATED_WEIGHT,
    WASH_TRADING_WEIGHT,
    WHALE_EXIT_WEIGHT,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    #: Rule = (flag, weight, reason, group, sources) where each source is a
    #: ``(key-paths, predicate)`` pair probed in order.
    _RuleSource = tuple[tuple[tuple[str, ...], ...], Callable[[Any], bool]]
    _Rule = tuple[str, int, str, str, tuple[_RuleSource, ...]]


# ── Scoring rules: (flag, weight, reason, group, sources) ───────────────
# Ordered so flags/reasons read critical-tier first. Each rule carries one or
# more sources: ``(key-paths, predicate)`` pairs probed in order (flat + nested
# module output). The first source whose predicate fires wins the flag.
_RULES: tuple[_Rule, ...] = (
    # CONTRACT / near-critical
    (
        "honeypot",
        HONEYPOT_WEIGHT,
        "contract is a honeypot (cannot sell)",
        "CONTRACT",
        (
            (
                (("honeypot",), ("security", "honeypot")),
                lambda v: _signal_bool(v, ("is_honeypot", "honeypot")),
            ),
        ),
    ),
    (
        "known_scammer",
        KNOWN_SCAMMER_WEIGHT,
        "deployer is a known scammer",
        "SOCIAL",
        (
            (
                (("known_scammer",), ("social", "known_scammer")),
                lambda v: _signal_bool(v, ("known_scammer", "is_known_scammer")),
            ),
        ),
    ),
    (
        "rug_history",
        RUG_HISTORY_WEIGHT,
        "deployer has a documented rug history",
        "DEPLOYER",
        (
            (
                (("rug_history",), ("deployer", "rug_history"), ("deployer_reputation", "rug_history")),
                _rug_history,
            ),
        ),
    ),
    (
        "mintable",
        MINTABLE_WEIGHT,
        "token supply is mintable",
        "CONTRACT",
        (
            (
                (("mintable",), ("security", "mintable")),
                lambda v: _signal_bool(v, ("mintable", "can_mint", "is_mintable")),
            ),
        ),
    ),
    # LIQUIDITY / high
    (
        "lp_removed",
        LP_REMOVED_WEIGHT,
        "liquidity was removed (rug exit)",
        "LIQUIDITY",
        (
            (
                (("lp_removed",), ("liquidity", "lp_removed")),
                _lp_removed,
            ),
        ),
    ),
    (
        "bad_funder",
        BAD_FUNDER_WEIGHT,
        "deployer funded by a known bad actor / mixer",
        "DEPLOYER",
        (
            (
                (("bad_funder",), ("deployer", "bad_funder")),
                _bad_funder,
            ),
        ),
    ),
    (
        "mint_supply_risk",
        MINT_SUPPLY_RISK_WEIGHT,
        "mint authority poses supply risk",
        "SUPPLY",
        (
            (
                (("mint_supply_risk",),),
                lambda v: _signal_bool(v, ("mint_supply_risk", "mint_risk", "is_mint_risk")),
            ),
        ),
    ),
    (
        "bundle_suspect",
        BUNDLE_WEIGHT,
        "launch was bundled (sniper cluster)",
        "TRADING",
        (
            (
                (("bundled",), ("bundle",), ("bundle_suspect",), ("security", "bundle_suspect")),
                _bundle_suspect,
            ),
        ),
    ),
    (
        "lp_unlocked",
        LP_UNLOCKED_WEIGHT,
        "LP is unlocked (rug exit possible)",
        "CONTRACT",
        (
            # ``lp_unlocked`` carries the UNLOCKED state directly (bool/dict).
            ((("lp_unlocked",),), lambda v: _truthy_scalar(v)),
            # ``lp_locked`` carries the LOCKED state: False / low % = unlocked.
            (
                (("lp_locked",), ("security", "lp_locked"), ("authority", "lp_locked")),
                _lp_unlocked,
            ),
        ),
    ),
    (
        "repeat_deployer",
        REPEAT_DEPLOYER_WEIGHT,
        "deployer has a repeat-rug history",
        "DEPLOYER",
        (
            (
                (
                    ("deployer_repeat",),
                    ("repeat_deployer",),
                    ("deployer", "repeat_deployer"),
                    ("deployer", "prior_deployments"),
                ),
                _repeat_deployer,
            ),
        ),
    ),
    (
        "wash_trading",
        WASH_TRADING_WEIGHT,
        "wash trading detected (fake volume)",
        "TRADING",
        (
            (
                (("wash_trading",),),
                lambda v: _signal_bool(v, ("suspected", "is_wash_trading", "wash_trading", "detected")),
            ),
        ),
    ),
    # TRADING / medium
    (
        "freezable",
        FREEZABLE_WEIGHT,
        "token is freezable (balances can be frozen)",
        "CONTRACT",
        (
            (
                (("freezable",), ("freeze_authority",), ("authority", "freeze_authority")),
                lambda v: _signal_bool(v, ("freeze_authority", "freezable", "is_freezable")),
            ),
        ),
    ),
    (
        "coordinated_shill",
        COORDINATED_SHILL_WEIGHT,
        "coordinated shill activity detected",
        "SOCIAL",
        (
            (
                (("coordinated_shill",), ("shill",), ("shill_detector",)),
                _coordinated_shill,
            ),
        ),
    ),
    (
        "whale_exit",
        WHALE_EXIT_WEIGHT,
        "whale/smart-money outflow detected",
        "MOMENTUM",
        (
            (
                (("whale_flow",), ("whale",)),
                _whale_exit,
            ),
        ),
    ),
    (
        "supply_concentrated",
        SUPPLY_CONCENTRATED_WEIGHT,
        "token supply is concentrated (top holders)",
        "SUPPLY",
        (
            (
                (("supply_concentrated",), ("holders",), ("gmgn", "holders"), ("supply", "top_holder_pct")),
                _supply_concentrated,
            ),
        ),
    ),
    (
        "proxy_upgradable",
        PROXY_UPGRADABLE_WEIGHT,
        "contract is proxy-upgradable",
        "CONTRACT",
        (
            (
                (("proxy_upgradable",), ("proxy",), ("authority", "proxy_upgradable")),
                lambda v: _signal_bool(v, ("proxy", "upgradable", "is_proxy", "proxy_upgradable")),
            ),
        ),
    ),
    (
        "kol_pump",
        KOL_PUMP_WEIGHT,
        "KOL pumping/promoting the token",
        "SOCIAL",
        (
            ((("kol_pump",),), _kol_pump),
            # shill_detector returns a list of alert records.
            ((("shill",),), _kol_pump),
            ((("shill", "pattern"),), _kol_pump),
        ),
    ),
    (
        "low_liquidity",
        LOW_LIQUIDITY_WEIGHT,
        "liquidity is very low",
        "LIQUIDITY",
        (
            (
                (("liquidity",), ("liquidity_usd",), ("liquidity_usd_value",), ("pooled_liquidity",)),
                _low_liquidity,
            ),
        ),
    ),
    (
        "very_young",
        VERY_YOUNG_WEIGHT,
        "token is very young (< 1h old)",
        "MOMENTUM",
        (
            (
                (("age_seconds",), ("launch", "age_seconds")),
                _very_young,
            ),
        ),
    ),
    (
        "smart_money_exit",
        SMART_MONEY_EXIT_WEIGHT,
        "smart money is exiting",
        "MOMENTUM",
        (
            (
                (("smart_money_sold",), ("smart_money_exit",), ("smart_money",), ("gmgn", "smart_money")),
                _smart_money_exit,
            ),
        ),
    ),
    (
        "ownership_transferable",
        OWNERSHIP_TRANSFERABLE_WEIGHT,
        "ownership can be transferred",
        "CONTRACT",
        (
            (
                (
                    ("ownership_transferable",),
                    ("can_take_back_ownership",),
                    ("authority", "ownership_transferable"),
                ),
                lambda v: _signal_bool(
                    v, ("can_take_back_ownership", "ownership_transferable", "transferable", "owner_can_rug")
                ),
            ),
        ),
    ),
    (
        "sniper_cluster",
        SNIPER_CLUSTER_WEIGHT,
        "sniper cluster detected (coordinated buys)",
        "TRADING",
        (
            (
                (
                    ("sniper_cluster",),
                    ("sniper_count",),
                    ("holders", "sniper_count"),
                    ("gmgn", "holders", "sniper_count"),
                ),
                _sniper_cluster,
            ),
        ),
    ),
    (
        "volume_concentrated",
        VOLUME_CONCENTRATED_WEIGHT,
        "trade volume is concentrated in one maker",
        "TRADING",
        (
            (
                (("volume_concentrated",), ("top_maker_share_pct",), ("wash", "top_maker_share_pct")),
                _volume_concentrated,
            ),
        ),
    ),
    (
        "burn_disabled",
        BURN_DISABLED_WEIGHT,
        "token burning is disabled",
        "SUPPLY",
        (
            (
                (("burn_disabled",), ("supply", "burn_disabled")),
                lambda v: _signal_bool(v, ("burn_disabled", "is_burn_disabled")),
            ),
        ),
    ),
    (
        "fresh_wallet",
        FRESH_WALLET_WEIGHT,
        "deployer wallet is fresh / first deployment",
        "DEPLOYER",
        (
            (
                (
                    ("fresh_wallet",),
                    ("first_deployment",),
                    ("wallet_age_seconds",),
                    ("deployer", "first_deployment"),
                ),
                _fresh_wallet,
            ),
        ),
    ),
    (
        "renounced",
        RENOUNCED_WEIGHT,
        "ownership is NOT renounced (owner retains control)",
        "CONTRACT",
        (
            (
                (("renounced",), ("authority", "renounced"), ("security", "renounced")),
                _renounced,
            ),
        ),
    ),
    (
        "lp_burned_pct",
        LP_BURNED_PCT_WEIGHT,
        "LP is mostly unburned (burn < 50%)",
        "LIQUIDITY",
        (
            (
                (("lp_burned_pct",), ("lp_burned",), ("liquidity", "lp_burned_pct")),
                _lp_burned_pct,
            ),
        ),
    ),
    (
        "single_pool",
        SINGLE_POOL_WEIGHT,
        "liquidity sits in a single pool",
        "LIQUIDITY",
        (
            (
                (("single_pool",), ("pool_count",), ("liquidity", "pool_count")),
                _single_pool,
            ),
        ),
    ),
)

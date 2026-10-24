"""Risk-policy seam: the interface is public, the calibration is not.

The public repository ships the *shape* of the policy (which knobs exist, what a
rule looks like) and a deliberately invented example. The calibrated values live
in the private ``rugmunch_scanner`` package, which registers the
``rmi.risk_policy`` entry point; ``RMI_RISK_POLICY=module:attr`` overrides it.
Same contract, environment variable and entry-point group as ``rmi-backend``.
"""

from __future__ import annotations

import importlib
import logging
import os
from functools import lru_cache
from importlib.metadata import entry_points
from typing import Any, Mapping

logger = logging.getLogger(__name__)

ENV_VAR = "RMI_RISK_POLICY"
ENTRY_POINT_GROUP = "rmi.risk_policy"


class PolicyUnavailable(RuntimeError):
    """The configured policy provider could not be loaded."""


def example_policy() -> dict[str, Any]:
    """Illustrative policy: three invented rules, round invented constants."""
    return {
        "name": "example-v0",
        "rules": (
            ("honeypot", 50, "example: sells are blocked", "CONTRACT", None),
            ("lp_removed", 30, "example: liquidity withdrawn", "LIQUIDITY", None),
            ("very_young", 20, "example: brand new token", "MOMENTUM", None),
        ),
        "hard_floor_flags": ("honeypot",),
        "bands": {"critical": 70, "high": 45, "medium": 20},
        "constants": {
            "HONEYPOT_WEIGHT": 10,
            "KNOWN_SCAMMER_WEIGHT": 20,
            "RUG_HISTORY_WEIGHT": 30,
            "MINTABLE_WEIGHT": 40,
            "LP_REMOVED_WEIGHT": 50,
            "MINT_SUPPLY_RISK_WEIGHT": 10,
            "BAD_FUNDER_WEIGHT": 20,
            "BUNDLE_WEIGHT": 30,
            "REPEAT_DEPLOYER_WEIGHT": 40,
            "LP_UNLOCKED_WEIGHT": 50,
            "WASH_TRADING_WEIGHT": 10,
            "FREEZABLE_WEIGHT": 20,
            "COORDINATED_SHILL_WEIGHT": 30,
            "PROXY_UPGRADABLE_WEIGHT": 40,
            "SUPPLY_CONCENTRATED_WEIGHT": 50,
            "WHALE_EXIT_WEIGHT": 10,
            "KOL_PUMP_WEIGHT": 20,
            "OWNERSHIP_TRANSFERABLE_WEIGHT": 30,
            "SNIPER_CLUSTER_WEIGHT": 40,
            "LOW_LIQUIDITY_WEIGHT": 50,
            "VERY_YOUNG_WEIGHT": 10,
            "SMART_MONEY_EXIT_WEIGHT": 20,
            "BURN_DISABLED_WEIGHT": 30,
            "VOLUME_CONCENTRATED_WEIGHT": 40,
            "FRESH_WALLET_WEIGHT": 50,
            "RENOUNCED_WEIGHT": 10,
            "LP_BURNED_PCT_WEIGHT": 20,
            "SINGLE_POOL_WEIGHT": 30,
            "KOL_MITIGATION": 5,
            "CRITICAL_MIN": 70,
            "HIGH_MIN": 45,
            "MEDIUM_MIN": 20,
            "LP_UNLOCKED_PCT_MAX": 50.0,
            "VERY_YOUNG_SECONDS": 3600.0,
            "LOW_LIQUIDITY_USD": 10000.0,
            "SUPPLY_CONCENTRATED_PCT": 50.0,
            "LP_BURNED_PCT_MIN": 50.0,
            "SNIPER_CLUSTER_MIN": 5,
            "VOLUME_CONCENTRATED_PCT": 50.0,
            "REPEAT_DEPLOYER_MIN": 5,
            "FRESH_WALLET_SECONDS": 3600.0,
            "SINGLE_POOL_MAX": 5,
        },
        "groups": {
            "honeypot": "CONTRACT",
            "lp_removed": "LIQUIDITY",
            "very_young": "MOMENTUM",
        },
    }


def _provider_from_env() -> Any:
    target = os.environ.get(ENV_VAR, "").strip()
    if not target:
        return None
    module_name, _, attr = target.partition(":")
    try:
        return getattr(importlib.import_module(module_name), attr or "default_policy")
    except (ImportError, AttributeError) as exc:
        raise PolicyUnavailable(f"{ENV_VAR}={target!r} unusable: {exc}") from exc


def _provider_from_entry_point() -> Any:
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        return entry.load()
    return None


@lru_cache(maxsize=1)
def load_policy() -> Mapping[str, Any]:
    """Resolve the calibrated policy, falling back to the example loudly."""
    provider = _provider_from_env() or _provider_from_entry_point()
    if provider is None:
        policy = example_policy()
        logger.warning(
            "risk_policy: no calibrated provider (%s unset, no %r entry point); "
            "using %s - scores are ILLUSTRATIVE",
            ENV_VAR,
            ENTRY_POINT_GROUP,
            policy["name"],
        )
        return policy
    return provider()


def reset_policy() -> None:
    """Drop the cached policy (tests, hot reload)."""
    load_policy.cache_clear()

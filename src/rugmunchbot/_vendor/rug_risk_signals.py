# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/_signals.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Defensive, never-raising signal predicates for the rug-risk scorer.

Extracted from ``rug_risk_score.py`` (degodding split, 2026-08-14). Every
function here is PURE — it takes a single signal value and returns a bool,
probing flat and nested key paths tolerantly. Re-exported from the parent
module so existing callers (the rule table and ``rug_risk_score``) keep
the same function objects.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.rug_risk_weights import (
    FRESH_WALLET_SECONDS,
    LOW_LIQUIDITY_USD,
    LP_BURNED_PCT_MIN,
    LP_UNLOCKED_PCT_MAX,
    REPEAT_DEPLOYER_MIN,
    SINGLE_POOL_MAX,
    SNIPER_CLUSTER_MIN,
    SUPPLY_CONCENTRATED_PCT,
    VERY_YOUNG_SECONDS,
    VOLUME_CONCENTRATED_PCT,
)

# ── Signal parsing helpers (defensive, never raise) ─────────────────────


def _truthy_scalar(value: Any) -> bool:
    """Coerce a scalar to a verdict boolean (tolerant)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "critical",
            "high",
            "detected",
            "suspected",
        }
    return bool(value)


def _signal_bool(value: Any, keys: tuple[str, ...]) -> bool:
    """Truthy for bool / str / number, or dicts exposing one of ``keys``."""
    if value is None:
        return False
    if isinstance(value, dict):
        for key in keys:
            if value.get(key) is not None:
                return _truthy_scalar(value.get(key))
        return False
    return _truthy_scalar(value)


def _to_float(value: Any) -> float | None:
    """Coerce a value to float, None for missing/unparseable."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_pct(value: Any) -> float | None:
    """Coerce a percent (number or ``"85%"`` string) to float."""
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    return _to_float(value)


def _probe(signals: dict[str, Any], paths: tuple[tuple[str, ...], ...]) -> Any:
    """Return the first non-None leaf value along any nested key path.

    Walks ``signals["a"]["b"]`` for each path; a missing intermediate,
    a non-dict intermediate, or a ``None`` leaf is treated as absent.
    """
    for path in paths:
        node: Any = signals
        try:
            for key in path:
                if not isinstance(node, dict) or node.get(key) is None:
                    node = None
                    break
                node = node[key]
        except Exception:  # defensive: a bad path never raises
            node = None
        if node is not None:
            return node
    return None


def _whale_exit(value: Any) -> bool:
    """True when whale/smart-money flow indicates an exit/outflow."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value < 0  # signed net flow: negative = outflow
    if isinstance(value, str):
        s = value.strip().lower()
        return any(tok in s for tok in ("exit", "outflow", "dump", "sell"))
    if isinstance(value, dict):
        for key in ("direction", "trend", "flow", "net", "whale_flow"):
            v = value.get(key)
            if v is None:
                continue
            if isinstance(v, (int, float)) and v < 0:
                return True
            if isinstance(v, str) and any(tok in v.lower() for tok in ("exit", "outflow", "dump", "sell")):
                return True
            if v is True:
                return True
    return False


def _lp_removed(value: Any) -> bool:
    return _signal_bool(value, ("lp_removed", "liquidity_removed", "is_lp_removed"))


def _lp_unlocked(value: Any) -> bool:
    """True when LP is *confirmed* unlocked.

    bool False → unlocked; a percent (number or ``"85%"``) below
    ``LP_UNLOCKED_PCT_MAX`` → unlocked; dict exposing ``unlocked`` /
    ``lp_unlocked`` → truthy means unlocked; None → no signal.
    """
    if value is None:
        return False
    if isinstance(value, dict):
        if value.get("unlocked") is not None:
            return _truthy_scalar(value.get("unlocked"))
        if value.get("lp_unlocked") is not None:
            return _truthy_scalar(value.get("lp_unlocked"))
        locked = value.get("locked")
        if locked is not None and not isinstance(locked, (int, float)):
            return not _truthy_scalar(locked)
        pct = _to_pct(value.get("pct", value.get("liquidity_locked_pct", value.get("lp_locked"))))
        if pct is not None:
            return pct < LP_UNLOCKED_PCT_MAX
        return False
    if isinstance(value, bool):
        return not value
    pct = _to_pct(value)
    if pct is not None:
        return pct < LP_UNLOCKED_PCT_MAX
    return False


def _very_young(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("age_seconds", "launch_age_seconds", "age"):
            seconds = _to_float(value.get(key))
            if seconds is not None:
                return seconds < VERY_YOUNG_SECONDS
        return False
    seconds = _to_float(value)
    return seconds is not None and seconds < VERY_YOUNG_SECONDS


def _low_liquidity(value: Any) -> bool:
    if isinstance(value, dict):
        for key in ("liquidity_usd", "liquidity", "pooled_liquidity", "usd"):
            usd = _to_float(value.get(key))
            if usd is not None:
                return usd < LOW_LIQUIDITY_USD
        return False
    usd = _to_float(value)
    return usd is not None and usd < LOW_LIQUIDITY_USD


def _renounced(value: Any) -> bool:
    """Risk when ownership is confirmed *not* renounced.

    ``renounced: True`` (owner renounced → safe) does NOT fire; only an
    explicit ``False`` / ``"no"`` / 0 or a dict exposing a falsy
    ``renounced``/``owner_renounced`` fires. None → no signal.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {"no", "false", "0", "not renounced", "not_renounced"}
    if isinstance(value, dict):
        for key in ("renounced", "owner_renounced"):
            if value.get(key) is not None:
                return not _truthy_scalar(value.get(key))
    return False


def _supply_concentrated(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= SUPPLY_CONCENTRATED_PCT
    if isinstance(value, str):
        pct = _to_pct(value)
        return pct is not None and pct >= SUPPLY_CONCENTRATED_PCT
    if isinstance(value, dict):
        if value.get("supply_concentrated") is not None:
            return _truthy_scalar(value.get("supply_concentrated"))
        for key in ("top_holder_pct", "top10_holder_pct", "top_10_pct", "concentration_pct", "concentration"):
            pct = _to_pct(value.get(key))
            if pct is not None:
                return pct >= SUPPLY_CONCENTRATED_PCT
    return False


def _lp_burned_pct(value: Any) -> bool:
    """Risk when LP is confirmed mostly *unburned* (burn < threshold).

    ``lp_burned: True`` / a high ``lp_burned_pct`` does NOT fire; a burn
    percent below ``LP_BURNED_PCT_MIN`` fires. None → no signal.
    """
    if isinstance(value, bool):
        return not value
    if isinstance(value, dict):
        if value.get("lp_burned") is not None:
            return not _truthy_scalar(value.get("lp_burned"))
        pct = _to_pct(value.get("lp_burned_pct", value.get("burned_pct", value.get("liquidity_burned_pct"))))
        if pct is not None:
            return pct < LP_BURNED_PCT_MIN
        return False
    pct = _to_pct(value)
    return pct is not None and pct < LP_BURNED_PCT_MIN


def _single_pool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value <= SINGLE_POOL_MAX
    if isinstance(value, dict):
        if value.get("single_pool") is not None:
            return _truthy_scalar(value.get("single_pool"))
        n = value.get("pool_count", value.get("pair_count", value.get("pairs")))
        if isinstance(n, (int, float)):
            return n <= SINGLE_POOL_MAX
        if isinstance(n, list):
            return len(n) <= SINGLE_POOL_MAX
        return False
    return _truthy_scalar(value)


def _volume_concentrated(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= VOLUME_CONCENTRATED_PCT
    if isinstance(value, str):
        pct = _to_pct(value)
        return pct is not None and pct >= VOLUME_CONCENTRATED_PCT
    if isinstance(value, dict):
        if value.get("volume_concentrated") is not None:
            return _truthy_scalar(value.get("volume_concentrated"))
        for key in ("top_maker_share_pct", "top_maker_share", "share", "concentration_pct"):
            pct = _to_pct(value.get(key))
            if pct is not None:
                return pct >= VOLUME_CONCENTRATED_PCT
    return False


def _bundle_suspect(value: Any) -> bool:
    return _signal_bool(value, ("bundled", "bundle_suspect", "is_bundled", "bundle"))


def _sniper_cluster(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= SNIPER_CLUSTER_MIN
    if isinstance(value, dict):
        if value.get("sniper_cluster") is not None:
            return _truthy_scalar(value.get("sniper_cluster"))
        n = value.get("sniper_count", value.get("snipers", value.get("sniper_holders")))
        if isinstance(n, (int, float)):
            return n >= SNIPER_CLUSTER_MIN
        return False
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("sniper", "cluster"))
    return _truthy_scalar(value)


def _repeat_deployer(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value >= REPEAT_DEPLOYER_MIN
    if isinstance(value, dict):
        if value.get("repeat_deployer") is not None:
            return _truthy_scalar(value.get("repeat_deployer"))
        if value.get("deployer_repeat") is not None:
            return _truthy_scalar(value.get("deployer_repeat"))
        n = value.get("prior_deployments")
        if isinstance(n, (int, float)):
            return n >= REPEAT_DEPLOYER_MIN
        return False
    return _truthy_scalar(value)


def _rug_history(value: Any) -> bool:
    return _signal_bool(value, ("rug_history", "has_rugged", "is_rug_puller", "rug_pulls", "rugged"))


def _bad_funder(value: Any) -> bool:
    return _signal_bool(
        value,
        ("bad_funder", "funded_by_scammer", "funded_by_bad_actor", "funder_bad", "mixer_funded"),
    )


def _fresh_wallet(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value < FRESH_WALLET_SECONDS  # wallet_age_seconds
    if isinstance(value, dict):
        if value.get("fresh_wallet") is not None:
            return _truthy_scalar(value.get("fresh_wallet"))
        if value.get("first_deployment") is not None:
            return _truthy_scalar(value.get("first_deployment"))
        age = _to_float(value.get("wallet_age_seconds"))
        if age is not None:
            return age < FRESH_WALLET_SECONDS
        return False
    if isinstance(value, str):
        return value.strip().lower() in {"fresh", "new", "first"}
    return _truthy_scalar(value)


def _kol_pump(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        # shill_detector returns a list of alert records.
        for alert in value:
            if not isinstance(alert, dict):
                continue
            pattern = str(alert.get("pattern") or "")
            if "pump" in pattern.lower() or "shill" in pattern.lower():
                return True
        return False
    if isinstance(value, dict):
        if value.get("kol_pump") is not None:
            return _truthy_scalar(value.get("kol_pump"))
        pattern = str(value.get("pattern") or "")
        return "pump" in pattern.lower() or "shill" in pattern.lower()
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("pump", "shill", "promo"))
    return _truthy_scalar(value)


def _coordinated_shill(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        if value.get("coordinated_shill") is not None:
            return _truthy_scalar(value.get("coordinated_shill"))
        if value.get("coordinated") is not None:
            return _truthy_scalar(value.get("coordinated"))
        alerts = value.get("alerts") or value.get("shill_alerts")
        if isinstance(alerts, list):
            return len(alerts) >= 1
        if isinstance(alerts, (int, float)):
            return alerts >= 1
        return False
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("coordinated", "shill", "cluster"))
    return _truthy_scalar(value)


def _smart_money_exit(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value < 0  # signed net flow: negative = outflow
    if isinstance(value, dict):
        if value.get("smart_money_sold") is not None:
            return _truthy_scalar(value.get("smart_money_sold"))
        if value.get("smart_money_exit") is not None:
            return _truthy_scalar(value.get("smart_money_exit"))
        for key in ("net_bought", "buy_pressure", "net"):
            n = _to_float(value.get(key))
            if n is not None:
                return n < 0
        if value.get("sold") is not None:
            return _truthy_scalar(value.get("sold"))
        return False
    if isinstance(value, str):
        return any(tok in value.lower() for tok in ("exit", "outflow", "sold", "dump"))
    return False


def _kol_verified(value: Any) -> bool:
    """True when the wallet is a verified KOL (mitigating signal)."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        for key in ("is_kol", "kol_handle", "matched", "verified"):
            item = value.get(key)
            if item is None:
                continue
            if isinstance(item, str):
                if item.strip():
                    return True
            elif _truthy_scalar(item):
                return True
    return False

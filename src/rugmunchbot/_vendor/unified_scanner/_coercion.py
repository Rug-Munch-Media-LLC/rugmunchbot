# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/_coercion.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Defensive coercion + risk-mapping helpers for the unified token scanner.

Split out of ``scanner.py`` (degodding 2026-08-07) so the scan-orchestration
class only imports what it needs. Module results arrive as JSON-able dicts
after ``dataclass_to_dict``; a field may be missing, ``None``, or a
non-numeric string.  These helpers coerce safely so one malformed module can
never crash profile collection.
"""

from __future__ import annotations

from typing import Any

# ── SENTINEL deep-scan tier mapping ────────────────────────────────────
# ``scan_token`` exposes a quick/deep SENTINEL tier on top of the unified
# phase tiers:
#   quick            → free SENTINEL modules only (fast baseline)
#   deep             → every SENTINEL module (free + premium deep-forensic)
# Legacy tier names are accepted so callers can pass free/pro/elite too:
# free/pro behave like quick, elite/admin behave like deep.
_QUICK_SCAN_TIERS: set[str] = {"quick", "free", "pro"}
_DEEP_SCAN_TIERS: set[str] = {"deep", "elite", "admin"}


def _resolve_scan_tier(tier: str) -> tuple[str, str]:
    """Map a SENTINEL scan tier to ``(phase_tier, scan_tier_label)``.

    ``phase_tier`` is what :meth:`UnifiedTokenScanner.scan` gates on
    (premium phases unlock at elite/admin); ``scan_tier_label`` is stored
    on the result as ``ScanResult.scan_tier`` (``"quick"``/``"deep"``).
    """
    t = (tier or "quick").strip().lower()
    if t in _DEEP_SCAN_TIERS:
        return "elite", "deep"
    return "free", "quick"


def _f(value: Any, default: float = 0.0) -> float:
    """Coerce a value to float, returning ``default`` when unparseable."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _i(value: Any, default: int = 0) -> int:
    """Coerce a value to int, returning ``default`` when unparseable."""
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _risk_level_hot(module: dict[str, Any] | None) -> bool:
    """True when a module's ``risk_level`` is HIGH or CRITICAL."""
    if not isinstance(module, dict):
        return False
    return str(module.get("risk_level", "") or "").upper() in ("HIGH", "CRITICAL")


def _honeypot_risk(honeypot: dict[str, Any] | None) -> str:
    """Map a honeypot report to ``none`` / ``possible`` / ``detected``."""
    if not isinstance(honeypot, dict):
        return "unknown"
    if honeypot.get("is_honeypot"):
        return "detected"
    if _f(honeypot.get("confidence")) >= 50:
        return "possible"
    return "none"


def _mev_exposure(mev: dict[str, Any] | None) -> str:
    """Map an MEV report to a ``none``/``low``/``medium``/``high`` exposure."""
    if not isinstance(mev, dict):
        return "unknown"
    risk = str(mev.get("risk_level", "") or "").upper()
    if risk in ("CRITICAL", "HIGH"):
        return "high"
    if risk == "MEDIUM":
        return "medium"
    if risk == "LOW":
        return "low"
    score = _f(mev.get("mev_risk_score"))
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _social_velocity(social: dict[str, Any] | None) -> str:
    """Map a social-velocity report to normal/suspicious/critical."""
    if not isinstance(social, dict):
        return "normal"
    label = str(social.get("risk_label", "") or "").upper()
    if label == "CRITICAL":
        return "critical"
    if label == "SUSPICIOUS":
        return "suspicious"
    if label == "ORGANIC":
        return "normal"
    score = _f(social.get("velocity_score"))
    if score >= 70:
        return "critical"
    if score >= 40:
        return "suspicious"
    return "normal"


def _funded_from(gas_trace: dict[str, Any] | None) -> str:
    """Derive the deployer's primary funding source from the gas trace."""
    if not isinstance(gas_trace, dict):
        return "unknown"
    if gas_trace.get("cex_funded") and gas_trace.get("cex_name"):
        return str(gas_trace["cex_name"])
    traced = gas_trace.get("traced_chain") or []
    if isinstance(traced, list):
        for hop in traced:
            if isinstance(hop, dict) and hop.get("relationship") == "cex_hot_wallet" and hop.get("address"):
                return str(hop["address"])
    if str(gas_trace.get("dead_end_reason", "") or "") in ("fresh_wallet", "cex_hot_wallet", "no_funding_tx"):
        return str(gas_trace["dead_end_reason"])
    return "unknown"


def _sleep_pattern(sleep_cycle: dict[str, Any] | None) -> str:
    """Map a sleep-cycle risk label to normal/irregular/bot-like."""
    if not isinstance(sleep_cycle, dict):
        return "normal"
    risk = str(sleep_cycle.get("risk_label", "") or "").upper()
    if risk in ("CRITICAL", "HIGH"):
        return "bot-like"
    if risk == "MEDIUM":
        return "irregular"
    return "normal"


def _extract_meta_identity(meta: dict[str, Any]) -> tuple[str, str]:
    """Extract ``(name, symbol)`` from any token_metadata payload shape.

    DataBus ``token_metadata`` providers return heterogeneous shapes:

      * normalized   ``{"name": "...", "symbol": "...", ...}``
      * DexScreener  ``{"pairs": [{"baseToken": {"name": .., "symbol": ..}, ...}]}``
      * Alchemy RPC  ``{"result": {"name": .., "symbol": .., "decimals": ..}}``

    Returns ``("", "")`` when none of the shapes carry identity so callers
    can fall back to the price payload without raising.
    """
    if not isinstance(meta, dict):
        return "", ""
    name = meta.get("name", "")
    symbol = meta.get("symbol", "")
    if name or symbol:
        return str(name or ""), str(symbol or "")

    result = meta.get("result")
    if isinstance(result, dict):
        name = result.get("name", "")
        symbol = result.get("symbol", "")
        if name or symbol:
            return str(name or ""), str(symbol or "")

    pairs = meta.get("pairs")
    if isinstance(pairs, list):
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            base = pair.get("baseToken")
            if isinstance(base, dict) and (base.get("name") or base.get("symbol")):
                return str(base.get("name", "") or ""), str(base.get("symbol", "") or "")
    return "", ""

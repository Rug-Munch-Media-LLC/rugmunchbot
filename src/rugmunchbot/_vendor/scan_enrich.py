# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/scan_enrich.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Consolidated risk-signal enrichment for the RugMunchBot scan report.

Runs the scanner's enrichment modules (bundle, deployer reputation, wash
trading, whale flow, authority, social cross-ref) concurrently and attaches
one ``risk_signals`` dict to the scan result so the Telegram formatter can
render a single "why it's sus" block:

``enrich_scan(scan_result, address, chain)`` → mutates and returns the scan
result with ``scan_result["risk_signals"]`` set to::

    {
        "bundled": bool | None,          # bundle_analysis verdict
        "deployer_repeat": bool | None,  # repeat/rug deployer flag
        "wash_trading": bool | None,     # wash-trading / fake-volume flag
        "whale_flow": bool | None,       # whales exiting / smart money sold
        "mintable": bool | None,         # authority: supply can be minted
        "freezable": bool | None,        # authority: freeze authority active
        "lp_locked": bool | float | None,# authority LP lock state (bool or %)
        "known_scammer": bool | None,    # social cross-ref: scammer hit
        "kol_handle": str | None,        # social cross-ref: KOL handle
        # Pre-rendered per-module lines (pure renderers, None when clean):
        "bundle_signal": str | None,
        "reputation_signal": str | None,
        "wash_signal": str | None,
        "whale_signal": str | None,
        "authority_signal": str | None,
        "crossref_signal": str | None,
        # Consolidated 0-100 rug risk score (rug_risk_score.rug_risk_score):
        "rug_risk_score": {"score": int, "verdict": str, "flags": [...],
                           "reasons": [...]},
        "risk_verdict_text": str,        # one-line verdict (pre-rendered)
    }

Design
------
* CONCURRENT — the six modules run under one ``asyncio.gather`` with a hard
  8s budget (``_ENRICH_TIMEOUT_S``). A slow provider can never block a scan
  for longer than that; a timeout degrades to no enrichment at all.
* FAIL-OPEN per module — every entrypoint is awaited through ``_run()``;
  a module that raises (or is missing at import time) contributes ``None``
  fields instead of breaking the scan. The consolidated score is computed
  from whatever signals did land (missing = absence of evidence).
* PURE SCORER — the consolidated ``rug_risk_score`` is computed with the
  real ``rug_risk_score.rug_risk_score`` (pure, defensive, never raises).
  The scan result's own evidence (honeypot / liquidity / age) is folded in
  so the score reflects everything the scan already knows.

Modules are imported defensively (try/except ImportError) so this module
works even when an individual enrichment module is not deployable.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# Hard budget for the whole enrichment. Individual providers may time out at
# 10-20s; we never let the combined probe block the scan pipeline.
_ENRICH_TIMEOUT_S = 8.0

# ── Defensive module imports ────────────────────────────────────────────
# Every enrichment module is optional: a missing module contributes None
# fields (fail-open) instead of breaking the scan it decorates. The names
# are pre-annotated as optional so a failed import (None) type-checks.
_ResultDict = dict[str, Any]
_SignalRenderer = Callable[[_ResultDict], str | None]
_AsyncEnrich = Callable[[str, str], Awaitable[_ResultDict]]

authority_signal: _SignalRenderer | None
check_authority: _AsyncEnrich | None

try:
    from app.domains.scanner.authority_check import authority_signal, check_authority
except ImportError:  # pragma: no cover - exercised only when the module is absent
    authority_signal = None
    check_authority = None

bundle_signal: _SignalRenderer | None
detect_bundle: _AsyncEnrich | None

try:
    from app.domains.scanner.bundle_analysis import bundle_signal, detect_bundle
except ImportError:  # pragma: no cover - exercised only when the module is absent
    bundle_signal = None
    detect_bundle = None

deployer_reputation: _AsyncEnrich | None
reputation_signal: _SignalRenderer | None

try:
    from app.domains.scanner.deployer_reputation import deployer_reputation, reputation_signal
except ImportError:  # pragma: no cover - exercised only when the module is absent
    deployer_reputation = None
    reputation_signal = None

detect_wash_trading: _AsyncEnrich | None
wash_signal: Callable[[_ResultDict | None], str | None] | None

try:
    from app.domains.scanner.wash_trading_enrich import detect_wash_trading, wash_signal
except ImportError:  # pragma: no cover - exercised only when the module is absent
    detect_wash_trading = None
    wash_signal = None

whale_flow: _AsyncEnrich | None
whale_signal: Callable[[_ResultDict | None], str | None] | None

try:
    from app.domains.scanner.whale_flow_enrich import whale_flow, whale_signal
except ImportError:  # pragma: no cover - exercised only when the module is absent
    whale_flow = None
    whale_signal = None

crossref_signal: _SignalRenderer | None
crossref_wallet: Callable[[str, str], _ResultDict] | None

try:
    from app.domains.scanner.social_crossref import crossref_signal, crossref_wallet
except ImportError:  # pragma: no cover - exercised only when the module is absent
    crossref_signal = None
    crossref_wallet = None

risk_verdict_text: Callable[[_ResultDict], str] | None
rug_risk_score: Callable[[_ResultDict], _ResultDict] | None

try:
    from app.domains.scanner.rug_risk_score import risk_verdict_text, rug_risk_score
except ImportError:  # pragma: no cover - exercised only when the module is absent
    risk_verdict_text = None
    rug_risk_score = None


# ── Per-module field extraction (defensive, never raise) ────────────────


def _flag(result: Any, key: str) -> bool | None:
    """Bool flag from a module result; ``None`` when the module failed/unknown."""
    if not isinstance(result, dict):
        return None
    value = result.get(key)
    if value is None:
        return None
    return bool(value)


def _value(result: Any, key: str) -> Any:
    """Pass-through value (preserves ``None`` = unknown) for a module result."""
    if not isinstance(result, dict):
        return None
    return result.get(key)


def _repeat_flag(result: Any) -> bool | None:
    """Repeat-deployer flag: ``repeat_deployer`` or documented rug history."""
    if not isinstance(result, dict):
        return None
    repeat = result.get("repeat_deployer")
    rug = result.get("rug_history")
    if repeat is None and rug is None:
        return None
    return bool(repeat) or bool(rug)


def _whale_flag(result: Any) -> bool | None:
    """Whale-exit flag: net negative flow or smart money selling out."""
    if not isinstance(result, dict):
        return None
    if result.get("smart_money_sold") is True:
        return True
    net = result.get("net_flow")
    if isinstance(net, (int, float)):
        return net < 0
    if result.get("smart_money_bought") is True:
        return False  # smart money bought in — not an exit signal
    return None


def _lp_value(result: Any) -> Any:
    """LP lock state for the scorer: bool when known, lock % as fallback."""
    if not isinstance(result, dict):
        return None
    locked = result.get("lp_locked")
    if locked is not None:
        return locked
    return result.get("lp_lock_pct")


async def _run(entrypoint: Any, *args: Any) -> Any:
    """Await an optional async entrypoint; ``None`` on absence/failure (fail-open)."""
    if entrypoint is None:
        return None
    try:
        return await entrypoint(*args)
    except Exception as exc:  # fail-open by contract — never raises into the scan
        logger.debug(
            "scan_enrich_module_failed",
            module=getattr(entrypoint, "__name__", "?"),
            error=str(exc)[:120],
        )
        return None


def _render(renderer: Any, result: Any) -> str | None:
    """Render a module result through its pure signal renderer (None when clean)."""
    if renderer is None or not isinstance(result, dict):
        return None
    try:
        out = renderer(result)
    except Exception:
        return None
    return str(out) if out else None


# ── Public API ───────────────────────────────────────────────────────────


async def enrich_scan(scan_result: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Run all enrichment modules and attach ``risk_signals`` (fail-open).

    Returns the scan result UNCHANGED on a total failure (including when the
    combined run exceeds ``_ENRICH_TIMEOUT_S`` seconds). Individual module
    failures degrade to ``None`` fields — the scan is never blocked or failed.
    """
    if not isinstance(scan_result, dict) or not address:
        return scan_result

    try:
        bundle_result: Any
        rep_result: Any
        wash_result: Any
        whale_result: Any
        authority_result: Any
        crossref_result: Any
        (
            bundle_result,
            rep_result,
            wash_result,
            whale_result,
            authority_result,
            crossref_result,
        ) = await asyncio.wait_for(
            asyncio.gather(
                _run(detect_bundle, address, chain),
                _run(deployer_reputation, address, chain),
                _run(detect_wash_trading, address, chain),
                _run(whale_flow, address, chain),
                _run(check_authority, address, chain),
                _run(crossref_wallet, address, chain),
                return_exceptions=True,
            ),
            timeout=_ENRICH_TIMEOUT_S,
        )
    except Exception:  # hard budget exceeded — fail open, never block a scan
        return scan_result

    risk_signals: dict[str, Any] = {
        "bundled": _flag(bundle_result, "bundled"),
        "deployer_repeat": _repeat_flag(rep_result),
        "wash_trading": _flag(wash_result, "wash_trading"),
        "whale_flow": _whale_flag(whale_result),
        "mintable": _value(authority_result, "mintable"),
        "freezable": _value(authority_result, "freeze_authority"),
        "lp_locked": _lp_value(authority_result),
        "known_scammer": _value(crossref_result, "known_scammer"),
        "kol_handle": _value(crossref_result, "kol_handle"),
        "bundle_signal": _render(bundle_signal, bundle_result),
        "reputation_signal": _render(reputation_signal, rep_result),
        "wash_signal": _render(wash_signal, wash_result),
        "whale_signal": _render(whale_signal, whale_result),
        "authority_signal": _render(authority_signal, authority_result),
        "crossref_signal": _render(crossref_signal, crossref_result),
    }

    # ── Consolidated rug risk score ──
    # Fold the scan's own evidence in so the score reflects everything the
    # scan already knows (honeypot from GoPlus, liquidity, token age). The
    # scorer probes flat keys: pass through the module outputs that map to
    # its extra signals (KOL mitigation, rug history, smart-money exit).
    signals: dict[str, Any] = dict(risk_signals)
    signals["honeypot"] = scan_result.get("honeypot")
    signals["kol"] = risk_signals["kol_handle"]
    signals["rug_history"] = _value(rep_result, "rug_history")
    signals["smart_money_sold"] = _value(whale_result, "smart_money_sold")
    liquidity = scan_result.get("liquidity_usd") or scan_result.get("liquidity")
    if isinstance(liquidity, (int, float)) and liquidity > 0:
        signals["liquidity"] = liquidity
    created = scan_result.get("created_at")
    if isinstance(created, (int, float)) and created > 0:
        signals["age_seconds"] = max(0.0, (time.time() * 1000.0 - created) / 1000.0)

    if rug_risk_score is not None:
        try:
            score = rug_risk_score(signals)
            if isinstance(score, dict):
                risk_signals["rug_risk_score"] = score
                if risk_verdict_text is not None:
                    try:
                        verdict = risk_verdict_text(score)
                    except Exception:
                        verdict = None
                    if verdict:
                        risk_signals["risk_verdict_text"] = str(verdict)
        except Exception:  # noqa: S110  # best-effort scoring, never raises
            pass

    scan_result["risk_signals"] = risk_signals
    return scan_result


__all__ = ["_ENRICH_TIMEOUT_S", "enrich_scan"]

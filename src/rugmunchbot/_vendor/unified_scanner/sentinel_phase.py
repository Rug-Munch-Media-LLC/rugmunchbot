# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/sentinel_phase.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""SENTINEL deep-forensic phase for the unified token scanner.

Phase 5 of :class:`UnifiedTokenScanner` — runs the SENTINEL module fleet
directly (no DataBus hop) with a free/premium split:

  * free    — core rug-detection signals: holder HHI, LP lock verification,
              dev reputation, metadata fingerprint, contract authority
              (mint/freeze/renounce), and proxy detection. Always populated.
  * premium — every remaining SENTINEL module (bundle/sniper, wash trading,
              MEV, flash loans, pump-and-dump, oracle manipulation, governance
              attack, sentiment, pumpfun, honeypot, static analysis, decompiler,
              address labels, fund flow, contract diff, guilt association,
              bytecode similarity, block-zero sniper, gas trace, cross-chain
              trace, sleep cycle, social velocity). Gated behind the elite/admin
              API-key tier — or unlocked through the standalone x402 SENTINEL
              deep-scan endpoint (``POST /api/v1/x402-tools/sentinel_scan``).

Best-effort by design: a failing module is recorded in ``errors`` and never
crashes the scan. External calls are time-boxed so SENTINEL cannot stall the
rest of the scan.

Extension (Registry-Driven)
----------------------------
New SENTINEL modules are auto-discovered via the ``@sentinel_module``
decorator (see ``app/domains/scanners/sentinel_pipeline/_decorator.py``).
``SENTINEL_FREE_MODULES``, ``SENTINEL_PREMIUM_MODULES``, and
``SENTINEL_PREMIUM_DESCRIPTIONS`` are derived from the registry at import
time and fall back to the hardcoded tuples below when the registry is empty.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from rugmunchbot._vendor.unified_scanner.model import ScanResult

logger = get_logger("sentinel_phase")

# Per-module timeout — protects scan latency when an external API hangs.
MODULE_TIMEOUT_S = 12.0
# Timeout for best-effort deployer resolution (DexScreener lookup).
DEPLOYER_TIMEOUT_S = 4.0

# Tier values that unlock the premium SENTINEL module set. Mirrors the
# unified scanner's PREMIUM phase gate (elite/admin).
PREMIUM_UNLOCK_TIERS: tuple[str, ...] = ("elite", "admin")

# ── Legacy hardcoded module lists (fallback when registry is empty) ──

_LEGACY_FREE_MODULES: tuple[str, ...] = (
    "holder_analysis",  # holder HHI concentration
    "liquidity_verification",  # LP lock verification
    "dev_reputation",  # serial-rug dev history
    "metadata_fingerprint",  # metadata cloning / fingerprint
    "contract_authority",  # mint / freeze / renounce authority
    "proxy_detection",  # upgradeable proxy risk
)

_LEGACY_PREMIUM_MODULES: tuple[str, ...] = (
    "bundle_detection",
    "exchange_funding",
    "wash_trading",
    "sentiment",
    "pumpfun_analysis",
    "honeypot_detection",
    "mev_detection",
    "flash_loan_detection",
    "pump_dump_detection",
    "oracle_manipulation",
    "governance_attack",
    "static_analysis",
    "decompiler_analysis",
    "address_labels",
    "fund_flow",
    "contract_diff",
    "guilt_association",
    "sleep_cycle",
    "social_velocity",
    "bytecode_similarity",
    "block_zero_sniper",
    "gas_trace",
    "cross_chain_trace",
)

_LEGACY_DESCRIPTIONS: dict[str, str] = {
    "bundle_detection": "Same-block bundle/sniper detection with funding-chain analysis",
    "exchange_funding": "CEX-funded wallet detection, withdrawal clustering, deposit-to-dump patterns",
    "wash_trading": "Wash-trading probability scoring on volume authenticity",
    "sentiment": "Social sentiment scoring and artificial-hype detection",
    "pumpfun_analysis": "PumpFun bonding-curve progress, bot ratio, dev concentration",
    "honeypot_detection": "Sell-side honeypot detection (can buy but not sell)",
    "mev_detection": "MEV exposure and sandwich-attack susceptibility",
    "flash_loan_detection": "Flash-loan funded manipulation patterns",
    "pump_dump_detection": "Pump-and-dump cycle detection from on-chain volume",
    "oracle_manipulation": "Price-oracle manipulation susceptibility",
    "governance_attack": "Governance attack surface and vote manipulation",
    "static_analysis": "Static bytecode analysis for malicious patterns",
    "decompiler_analysis": "Decompiled contract review for hidden backdoors",
    "address_labels": "Known-good / known-bad address labeling of holders",
    "fund_flow": "Fund-flow visualization from deployer wallet",
    "contract_diff": "Contract diff vs known scam templates",
    "guilt_association": "Guilt-by-association for the deployer wallet",
    "sleep_cycle": "Deployer sleep-cycle / activity-pattern analysis",
    "social_velocity": "Social velocity and artificial-growth detection",
    "bytecode_similarity": "Bytecode similarity vs known scam contracts",
    "block_zero_sniper": "Block-zero sniper detection (dev/bot buys first block)",
    "gas_trace": "Three-hop gas trace of deployer funding",
    "cross_chain_trace": "Cross-chain trace of the deployer wallet",
}


def _build_module_lists_from_registry() -> tuple[tuple[str, ...], tuple[str, ...], dict[str, str]]:
    """Derive FREE/PREMIUM tuples and descriptions from the sentinel registry.

    Returns (free, premium, descriptions).  Falls back to legacy hardcoded
    values when ``_SENTINEL_REGISTRY`` is empty (e.g. before decorators fire).
    """
    try:
        from app.domains.scanners._sentinel_registry import (
            get_free_module_keys,
            get_premium_descriptions,
            get_premium_module_keys,
        )
    except ImportError:
        return _LEGACY_FREE_MODULES, _LEGACY_PREMIUM_MODULES, _LEGACY_DESCRIPTIONS

    free = get_free_module_keys()
    premium = get_premium_module_keys()
    if not free and not premium:
        # Registry not yet populated — use legacy
        return _LEGACY_FREE_MODULES, _LEGACY_PREMIUM_MODULES, _LEGACY_DESCRIPTIONS

    descriptions = get_premium_descriptions()
    # Merge with legacy descriptions for any modules missing from registry
    merged_descriptions = {**_LEGACY_DESCRIPTIONS, **descriptions}
    return free, premium, merged_descriptions


SENTINEL_FREE_MODULES, SENTINEL_PREMIUM_MODULES, SENTINEL_PREMIUM_DESCRIPTIONS = _build_module_lists_from_registry()

SENTINEL_ALL_MODULES: tuple[str, ...] = SENTINEL_FREE_MODULES + SENTINEL_PREMIUM_MODULES

UNLOCK_PROMPT = (
    "Upgrade to elite tier or run the paid x402 SENTINEL deep scan "
    "(POST /api/v1/x402-tools/sentinel_scan, $0.15) to unlock the full report."
)


def _no_deployer(module_key: str) -> None:
    """Raise for deployer-dependent modules when no deployer could be resolved."""
    raise ValueError(f"skipped: no deployer address resolved for {module_key}")


def _get_sentinel_modules() -> Any:
    """Import the SENTINEL pipeline modules lazily (keeps import graph light)."""
    from app.domains.scanners.sentinel_pipeline import modules as sentinel_modules

    sentinel_modules._ensure_modules()
    return sentinel_modules


def _get_analyzers(address: str, chain: str, deployer: str | None = None) -> dict[str, Callable[[], Any]]:
    """Build a module-key → async-callable map from the SENTINEL singletons.

    Uses the ``@sentinel_module`` registry for the common
    ``method(address, chain)`` pattern, with manual overrides for special
    cases (deployer-dependent, chain-gated, non-standard signatures).

    Freshly built on every call (never cached) so tests can patch this
    function wholesale and the module singletons stay current.
    """
    mod = _get_sentinel_modules()
    chain_lower = (chain or "").lower()

    def _guard_attr(name: str) -> Any | None:
        return getattr(mod, name, None)

    analyzers: dict[str, Callable[[], Any]] = {}

    # ── Registry-driven ──────────────────────────────────────────────
    # Most modules follow the pattern: singleton.method(address, chain)
    from app.domains.scanners._sentinel_registry import _SENTINEL_REGISTRY
    from app.domains.scanners.sentinel_pipeline.modules import _KEY_TO_SINGLETON_ATTR

    for key, entry in _SENTINEL_REGISTRY.items():
        attr_name = _KEY_TO_SINGLETON_ATTR.get(key)
        if not attr_name:
            continue
        singleton = _guard_attr(attr_name)
        if singleton is None:
            continue

        method_name = entry.get("method", "analyze")
        method = getattr(singleton, method_name, None)
        if method is None:
            continue

        needs_deployer = entry.get("needs_deployer", False)
        chain_gated = entry.get("chain_gated")

        # ── Chain-gated modules ──────────────────────────────
        if chain_gated and chain_lower != chain_gated:
            analyzers[key] = lambda: None
            continue

        # ── Deployer-dependent modules ───────────────────────
        if needs_deployer and not deployer:
            analyzers[key] = partial(_no_deployer, key)
            continue

        # ── Handle special signatures via overrides ──────────
        # Some modules have signatures that depart from the
        # standard ``method(address, chain)`` pattern.  These are
        # handled via a small override map (see below).
        if key in _ANALYZER_OVERRIDES:
            analyzers[key] = _ANALYZER_OVERRIDES[key](mod, address, chain, deployer)
            continue

        # ── Default: method(address, chain) ──────────────────
        analyzers[key] = partial(method, address, chain)

    return analyzers


# ── Analyzer override map ──────────────────────────────────────────────
# Modules whose call signature differs from the standard ``method(address,
# chain)`` pattern.  Each entry is a callable:
#     (mod, address, chain, deployer) -> lambda
#
# When you add a new module with a non-standard signature, add it here.
# Standard modules are handled automatically by ``_get_analyzers()``.

_ANALYZER_OVERRIDES: dict[str, Any] = {}


def _register_analyzer_override(key: str, builder: Any) -> None:
    """Register a custom lambda builder for a module with a non-standard signature."""
    _ANALYZER_OVERRIDES[key] = builder


# ── Non-standard signatures ───────────────────────────────────────────


def _ovr_sleep_cycle(mod: Any, address: str, chain: str, deployer: str | None) -> Callable[[], Any]:
    return lambda: mod._sleep_cycle.analyze([])


def _ovr_social_velocity(mod: Any, address: str, chain: str, deployer: str | None) -> Callable[[], Any]:
    return lambda: mod._social_velocity.analyze([])


def _ovr_dev_reputation(mod: Any, address: str, chain: str, deployer: str | None) -> Callable[[], Any]:
    if deployer:
        return partial(mod._dev_reputation.analyze, deployer, [chain])
    return lambda: _no_deployer("dev_reputation")


def _ovr_cross_chain_trace(mod: Any, address: str, chain: str, deployer: str | None) -> Callable[[], Any]:
    target = deployer if deployer else address
    return partial(mod._cross_chain_trace.analyze, target, chain)


# Wire overrides
_register_analyzer_override("sleep_cycle", _ovr_sleep_cycle)
_register_analyzer_override("social_velocity", _ovr_social_velocity)
_register_analyzer_override("dev_reputation", _ovr_dev_reputation)
_register_analyzer_override("cross_chain_trace", _ovr_cross_chain_trace)


async def _resolve_deployer(address: str, chain: str) -> str | None:
    """Best-effort deployer resolution via DexScreener (free, no API key).

    Routes through ``unified._data_sources.get_dexscreener_pairs`` — the
    single shared entry point for raw DexScreener pair payloads (the DataBus
    provider, memoized). Returns the first ``creatorAddress`` found for the
    token's pairs, or ``None`` when the lookup fails/times out. Never raises.
    """
    try:
        from rugmunchbot._vendor.unified_scanner._data_sources import get_dexscreener_pairs

        pairs = await asyncio.wait_for(
            get_dexscreener_pairs(address),
            timeout=DEPLOYER_TIMEOUT_S,
        )
        for pair in (pairs or {}).get("pairs") or []:
            creator = pair.get("creatorAddress") or ""
            if creator:
                return creator
    except (httpx.HTTPError, OSError, ValueError, TypeError, KeyError, TimeoutError):
        logger.debug("deployer_resolve_failed", address=address[:16], chain=chain)
    return None


def extract_deployer_from_security(security: Any) -> str | None:
    """Pull a deployer/creator address from core security data (best-effort).

    GoPlus-style security payloads expose the creator in a few shapes;
    we check the common key names defensively and return ``None`` when the
    field is absent or not a valid-looking address.
    """
    if not isinstance(security, dict):
        return None
    for key in ("creator_address", "creator", "deployer", "deployer_address", "owner_address"):
        value = security.get(key)
        if isinstance(value, str) and len(value) >= 32 and value not in ("", "unknown", "null"):
            return value
    # Nested "checks" / "extra" buckets sometimes carry the creator.
    for bucket in ("checks", "extra", "info"):
        value = security.get(bucket)
        if isinstance(value, dict):
            found = extract_deployer_from_security(value)
            if found:
                return found
    return None


async def run_sentinel_phase(
    address: str,
    chain: str,
    tier: str = "free",
    deployer: str | None = None,
) -> dict[str, Any]:
    """Run the SENTINEL phase: free modules always, premium when unlocked.

    Returns a plain dict (safe for JSON serialization):

    .. code-block:: python

        {
            "free": {module: dict_result | None, ...},
            "premium": {module: dict_result | None, ...} | None,
            "premium_unlocked": bool,
            "premium_modules": [module keys available at premium tier],
            "premium_preview": [{"module", "description", "unlock"}, ...],
            "errors": {module: error_string},
            "modules_run": [module keys attempted],
            "deployer_resolved": bool,
        }

    The phase never raises: module failures are captured in ``errors`` and
    the remaining modules still run.
    """
    from app.domains.scanners.sentinel_pipeline.config import dataclass_to_dict

    unlocked = (tier or "").lower() in PREMIUM_UNLOCK_TIERS
    if deployer is None:
        deployer = await _resolve_deployer(address, chain)

    analyzers = _get_analyzers(address, chain, deployer=deployer)
    free_keys = [k for k in SENTINEL_FREE_MODULES if k in analyzers]
    premium_keys = [k for k in SENTINEL_PREMIUM_MODULES if k in analyzers] if unlocked else []
    all_keys = free_keys + premium_keys

    async def _run_one(key: str, fn: Callable[[], Any]) -> tuple[str, Any, str | None]:
        try:
            raw = await asyncio.wait_for(fn(), timeout=MODULE_TIMEOUT_S)
            return key, dataclass_to_dict(raw) if raw is not None else None, None
        except TimeoutError:
            return key, None, f"timeout after {MODULE_TIMEOUT_S}s"
        except (httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
            logger.debug("sentinel_module_error", module=key, error=str(exc)[:160])
            return key, None, str(exc)[:200]
        except Exception as exc:
            logger.debug("sentinel_module_error", module=key, error=str(exc)[:160])
            return key, None, str(exc)[:200]

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    modules_run: list[str] = []

    if all_keys:
        gathered = await asyncio.gather(*(_run_one(k, analyzers[k]) for k in all_keys))
        for key, data, err in gathered:
            results[key] = data
            modules_run.append(key)
            if err:
                errors[key] = err

    return {
        "free": {k: results.get(k) for k in free_keys},
        "premium": {k: results.get(k) for k in premium_keys} if unlocked else None,
        "premium_unlocked": unlocked,
        "premium_modules": list(SENTINEL_PREMIUM_MODULES),
        "premium_preview": _build_premium_preview() if not unlocked else [],
        "errors": errors,
        "modules_run": modules_run,
        "deployer_resolved": bool(deployer),
    }


def _build_premium_preview() -> list[dict[str, str]]:
    """Locked-tier preview entries: what premium modules exist + unlock prompt."""
    return [
        {
            "module": key,
            "description": SENTINEL_PREMIUM_DESCRIPTIONS.get(key, "Deep-forensic security module"),
            "unlock": UNLOCK_PROMPT,
        }
        for key in SENTINEL_PREMIUM_MODULES
    ]


async def run_sentinel_for_result(
    r: ScanResult,
    address: str,
    chain: str,
    tier: str,
    free_modules: tuple[str, ...],
) -> None:
    """Run the SENTINEL phase and wire its results into a unified ScanResult.

    Extracted from ``unified/scanner.py::_run_sentinel_phase`` (degodding
    wave 8, 2026-08-09) so the merge logic (SENTINEL HHI promotion, free-tier
    module tracking, error fallback) lives beside the phase runner.

    Best-effort: a failing module is recorded in ``sentinel.errors`` and
    never fails the scan. Time-boxed so external API latency cannot stall
    the overall scan.
    """
    try:
        deployer = extract_deployer_from_security(r.security)
        sentinel = await asyncio.wait_for(
            run_sentinel_phase(address=address, chain=chain, tier=tier, deployer=deployer),
            timeout=30,
        )
        if not isinstance(sentinel, dict):
            return
        r.sentinel = sentinel

        # ── Wire SENTINEL holder analysis into the unified holder phase ──
        # The SENTINEL free-tier holder_analysis module computes HHI /
        # concentration directly from DexScreener/Birdeye/Solscan. When
        # the DataBus holder_health chain produced no HHI, promote the
        # SENTINEL HHI so the unified holder phase (scoring + evidence)
        # sees the deep-forensic concentration signal.
        sentinel_free = sentinel.get("free") or {}
        holder_analysis = sentinel_free.get("holder_analysis")
        if isinstance(holder_analysis, dict) and holder_analysis.get("hhi") is not None:
            hhi = holder_analysis.get("hhi")
            if not isinstance(r.holder_health, dict) or r.holder_health.get("hhi") is None:
                merged = dict(r.holder_health) if isinstance(r.holder_health, dict) else {}
                merged["hhi"] = hhi
                merged.setdefault("gini", None)
                merged["hhi_source"] = "sentinel_holder_analysis"
                r.holder_health = merged

        # Surface free-tier module names in modules_run + enrichment so
        # the SENTINEL phase is visible in metrics and the API report.
        free_ran = [k for k in free_modules if k in (sentinel.get("modules_run") or [])]
        if free_ran:
            r.modules_run.extend(free_ran)
            r.enrichment_sources.append("sentinel:free")
        if sentinel.get("premium_unlocked"):
            r.enrichment_sources.append("sentinel:premium")
    except Exception as exc:
        logger.debug("sentinel_phase_failed", error=str(exc)[:160])
        r.sentinel = {
            "free": {},
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(free_modules),
            "premium_preview": [],
            "errors": {"_phase": str(exc)[:200]},
            "modules_run": [],
            "deployer_resolved": False,
        }

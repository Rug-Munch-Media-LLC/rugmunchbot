# VENDORED 2026-08-23 from rmi-backend/app/osint/engine.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""OSINT orchestrator/engine — run every OSINT module against one target.

This is the single entry point for the :mod:`app.osint` section: given a
username, email, domain, wallet address, phone number or face reference it
dispatches to the relevant sibling module(s) and merges the per-module
:class:`app.osint.OsintReport` objects into one combined dict.

Design
------
* **Registry** — module lookups are referenced as ``(module_path, func_name)``
  tuples and imported *lazily* inside ``try/except``. A module that has not
  landed yet (or whose optional dependency is missing) is skipped and noted in
  ``errors``; it can never crash the engine.
* **Auto-detection** — ``detect_target_type`` classifies a raw target string as
  email / phone / wallet / domain / username (order matters: ``@`` wins, then
  digits, then ``0x``/base58, then dot+TLD, then username as the fallback).
* **Single-type dispatch** — ``run_osint`` runs the matching module plus
  SpiderFoot (the general engine) for ``domain`` and ``username`` targets.
* **Aggregate** — ``run_aggregate`` runs all applicable modules in parallel
  (``ThreadPoolExecutor``; the modules are blocking/sync) and merges every
  report into one dict plus a flat, deduped ``findings`` list.

Every module failure is recorded in ``errors`` — the engine never raises.

Usage::

    from rugmunchbot._vendor.osint.engine import run_osint, run_aggregate

    result = run_osint("victim@example.com", timeout=30)
    everything = run_aggregate("scam-example.com", "domain", timeout=120)
"""

from __future__ import annotations

import importlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Final, cast

from rugmunchbot._vendor.osint import OsintReport

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

__all__ = ["TARGET_TYPES", "detect_target_type", "run_aggregate", "run_osint"]

TARGET_TYPES: Final[set[str]] = {"username", "email", "domain", "wallet", "phone", "face"}

# ---------------------------------------------------------------------------
# Registry — (module path, lookup function name). Lookups are imported lazily
# inside try/except so a module that has not landed yet is skipped, not fatal.
# ---------------------------------------------------------------------------
_REGISTRY: Final[dict[str, tuple[str, str]]] = {
    "username": ("app.osint.username", "lookup_username"),
    "email": ("app.osint.email", "lookup_email"),
    "domain": ("app.osint.domain", "lookup_domain"),
    "wallet": ("app.osint.wallet", "lookup_wallet"),
    "phone": ("app.osint.phone", "lookup_phone"),
    "face": ("app.osint.face", "lookup_face"),
}

# SpiderFoot is the general OSINT engine: it accepts a target_type itself and
# fans out across 100+ modules, so it is dispatched with the resolved type.
_SPIDERFOOT_MODULE: Final[tuple[str, str]] = ("app.osint.spiderfoot_client", "lookup")

# Types for which the general engine (SpiderFoot) is also applicable.
_SPIDERFOOT_TYPES: Final[frozenset[str]] = frozenset({"domain", "username"})

# Bound on concurrent module runs in run_aggregate (registry is small today).
_MAX_WORKERS: Final[int] = 8

# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------
_BASE58_ALPHABET: Final[str] = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_WALLET_LEN_RANGE: Final[range] = range(32, 45)  # 32..44 chars

_PHONE_CLEAN_RE: Final[re.Pattern[str]] = re.compile(r"[\s().\-]")
_PHONE_RE: Final[re.Pattern[str]] = re.compile(r"\+?[0-9]{7,15}\Z")
_DOMAIN_TLD_RE: Final[re.Pattern[str]] = re.compile(r"\.[a-zA-Z]{2,63}\Z")


def detect_target_type(target: str, target_type: str = "auto") -> str:
    """Classify a raw target string into one of :data:`TARGET_TYPES`.

    Order matters: ``@`` -> email; digit run -> phone; ``0x``/base58 run ->
    wallet; dot + TLD -> domain; anything else -> username. An explicit
    ``target_type`` is returned unchanged (caller validates membership).
    """
    if target_type != "auto":
        return target_type
    value = target.strip()
    if "@" in value:
        return "email"
    if _PHONE_RE.fullmatch(_PHONE_CLEAN_RE.sub("", value)):
        return "phone"
    if value.lower().startswith("0x") or _is_base58_wallet(value):
        return "wallet"
    if "." in value and _DOMAIN_TLD_RE.search(value):
        return "domain"
    return "username"


def _is_base58_wallet(value: str) -> bool:
    """True when ``value`` looks like a Solana-style base58 address (32-44 chars)."""
    return len(value) in _WALLET_LEN_RANGE and all(c in _BASE58_ALPHABET for c in value)


# ---------------------------------------------------------------------------
# Lazy, guarded module loading
# ---------------------------------------------------------------------------
def _load_lookup(module_path: str, func_name: str) -> Callable[..., OsintReport] | None:
    """Import a module lookup lazily; return ``None`` (never raise) if unavailable."""
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:  # guarded import: any failure -> unavailable
        logger.debug("osint module %s unavailable: %s", module_path, exc)
        return None
    func = getattr(module, func_name, None)
    if callable(func):
        return cast("Callable[..., OsintReport]", func)
    return None


def _run_module(key: str, target: str, target_type: str, timeout: int) -> tuple[str, OsintReport | None, str | None]:
    """Run one registered module safely; never raises.

    Returns ``(key, report, error)`` where ``report`` is ``None`` on
    unavailable/failed lookups and ``error`` is a human-readable note (also
    ``None`` when the lookup produced a report).
    """
    if key == "spiderfoot":
        module_path, func_name = _SPIDERFOOT_MODULE
    else:
        module_path, func_name = _REGISTRY[key]
    func = _load_lookup(module_path, func_name)
    if func is None:
        return key, None, f"module not available: {key} ({module_path})"
    try:
        if key == "spiderfoot":
            report = func(target, target_type=target_type, timeout=timeout)
        else:
            report = func(target, timeout=timeout)
    except Exception as exc:  # a module failure must never crash the engine
        logger.warning("osint module %s failed for %r: %s", key, target, exc)
        return key, None, f"{key}: {type(exc).__name__}: {exc}"
    if not isinstance(report, OsintReport):  # pyright: ignore[reportUnnecessaryIsInstance] — defensive: registry modules may return anything
        return key, None, f"{key}: returned {type(report).__name__}, expected OsintReport"
    return key, report, None


def _applicable_modules(target_type: str) -> list[str]:
    """Modules to run for a resolved target type: the matching module + SpiderFoot."""
    keys = [target_type] if target_type in TARGET_TYPES else []
    if target_type in _SPIDERFOOT_TYPES:
        keys.append("spiderfoot")
    return keys


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def run_osint(target: str, target_type: str = "auto", *, timeout: int = 120) -> dict[str, Any]:
    """Run OSINT on a single target.

    Resolves ``target_type`` (auto-detection when ``"auto"``), dispatches to the
    matching module (plus SpiderFoot for ``domain``/``username``), and returns::

        {"target", "target_type", "results": {module: report.to_dict()},
         "findings": [...], "errors": [...]}

    ``findings`` is the same flat, deduped list that :func:`run_aggregate`
    emits, so both entry points share one response shape. Every module failure
    is collected in ``errors`` — this never raises.
    """
    resolved = detect_target_type(target, target_type)
    if resolved not in TARGET_TYPES:
        return {
            "target": target,
            "target_type": resolved,
            "results": {},
            "findings": [],
            "errors": [f"unknown target_type: {resolved!r}"],
        }
    results: dict[str, dict[str, Any]] = {}
    reports: dict[str, OsintReport] = {}
    errors: list[str] = []
    for key in _applicable_modules(resolved):
        _, report, error = _run_module(key, target, resolved, timeout)
        if error is not None:
            errors.append(error)
            continue
        if report is None:  # unreachable; keeps mypy narrowing honest
            errors.append(f"{key}: no report produced")
            continue
        reports[key] = report
        results[key] = report.to_dict()
    return {
        "target": target,
        "target_type": resolved,
        "results": results,
        "findings": _merge_findings(reports.values()),
        "errors": errors,
    }


def run_aggregate(target: str, target_type: str = "auto", *, timeout: int = 120) -> dict[str, Any]:
    """Run all applicable OSINT modules in parallel and merge every report.

    Same module applicability as :func:`run_osint` (matching module + SpiderFoot
    for ``domain``/``username``), but executed concurrently via a
    ``ThreadPoolExecutor`` (the module lookups are blocking/sync). Returns::

        {"target", "target_type", "results": {...}, "findings": [...], "errors": [...]}

    ``findings`` is every report's findings flattened into one list, deduped by
    (source, category, summary, confidence). Per-module errors (including the
    ones the modules recorded internally) are merged into ``errors``.
    """
    resolved = detect_target_type(target, target_type)
    if resolved not in TARGET_TYPES:
        return {
            "target": target,
            "target_type": resolved,
            "results": {},
            "findings": [],
            "errors": [f"unknown target_type: {resolved!r}"],
        }
    keys = _applicable_modules(resolved)
    reports: dict[str, OsintReport] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, min(len(keys), _MAX_WORKERS))) as executor:
        futures = {executor.submit(_run_module, key, target, resolved, timeout): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            _, report, error = future.result()  # _run_module never raises
            if error is not None:
                errors.append(error)
            elif report is not None:
                reports[key] = report

    results = {key: reports[key].to_dict() for key in keys if key in reports}
    for key in keys:
        report = reports.get(key)
        if report is not None:
            errors.extend(f"{key}: {e}" for e in report.errors)
    return {
        "target": target,
        "target_type": resolved,
        "results": results,
        "findings": _merge_findings(reports.values()),
        "errors": _dedupe(errors),
    }


def _merge_findings(reports: Iterable[OsintReport]) -> list[dict[str, Any]]:
    """Flatten findings from all reports into one deduped list (stable order)."""
    seen: set[tuple[str, str, str, str]] = set()
    merged: list[dict[str, Any]] = []
    for report in reports:
        for finding in report.findings:
            dedupe_key = (finding.source, finding.category, finding.summary, finding.confidence)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged.append(finding.to_dict())
    return merged


def _dedupe(items: list[str]) -> list[str]:
    """Order-preserving dedupe for the merged error list."""
    return list(dict.fromkeys(items))

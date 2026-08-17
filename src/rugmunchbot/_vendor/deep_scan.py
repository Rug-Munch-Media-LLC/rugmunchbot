# VENDORED 2026-08-17 from rmi-backend/app/deep_scan.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Deep-scan orchestrator — run the platform's ML + contract modules in one call.

Composes the already-added, self-contained analysis modules into a single
:func:`deep_scan_address` entry point so they actually get used together:

* :mod:`app.fraud_gnn` — ``FraudGNNDetector.predict(fingerprint)``: fraud
  probability over the :data:`FRAUD_FEATURES` fingerprint.
* :mod:`app.pyod_anomaly` — ``PyODAnomalyDetector.score(features)``: tabular
  anomaly score over the same fingerprint.
* :mod:`app.pygod_anomaly` — ``PyGODAnomalyDetector.score(graph)``: graph
  anomaly detection. Runs only when a transfer graph can be cheaply built
  (callers pass ``transfers=``); otherwise recorded as skipped.
* :mod:`app.heimdall_bytecode` — ``analyze_bytecode``: ABI extraction from raw
  bytecode (unverified contracts) after the code is fetched over RPC.
* :mod:`app.pyrevm_engine` — ``simulate_sell_round_trip``: honeypot / tax /
  blacklist / anti-whale detection via local EVM execution of real bytecode.
* Honeypot sell-sim check (``deep_scan_runners._run_honeypot_sell_sim``):
  Honeypot.is ``/v2/IsHoneypot`` (primary, free) with GoPlus
  ``token_security`` fallback (reuses the existing ``GOPLUS_API_KEY``) —
  buy/sell/transfer taxes plus a ``SELL SIM OK`` / ``SELL SIM BLOCKED``
  verdict. Complements pyrevm and is the only sell-sim source for non-EVM
  chains (e.g. Solana).
* :mod:`app.slither_analyzer` — ``analyze_source``: source-level static
  analysis. Requires verified Solidity source (``source_code=`` kwarg); when no
  source is available it is recorded as skipped.

The per-module guarded runners (one ``_run_*`` per sub-module) live in
:mod:`app.deep_scan_runners` so this module stays an orchestrator; every
runner returns ``(result, findings, severity)`` and never raises.

Guarantees
----------
* Every sub-module call is wrapped in ``try/except``; a failure becomes an
  ``error``-level finding in ``report.findings`` instead of crashing the scan.
* Missing optional dependencies (heimdall-py, pyrevm, pyod, slither binary,
  networkx) degrade the affected section to an error/skipped entry.
* :func:`deep_scan_address` never raises for module-level failures.

Risk derivation
---------------
Each sub-module contributes a severity level ``0..3`` (low/medium/high/
critical); ``overall_risk`` is the maximum level across available modules:

* fraud_gnn: ``fraud_probability >= 0.7 -> high``, ``>= 0.4 -> medium``.
* pyod: ``is_anomaly -> high``, ``anomaly_score >= 0.6 -> medium``.
* pygod: ``is_anomaly -> high`` (only when a graph was provided).
* heimdall: dangerous function names found in the decompiled ABI -> medium.
* pyrevm: ``risk`` field mapped critical/dangerous/warning/safe.
* honeypot sell-sim: ``BLOCKED`` verdict -> critical, ``EXTREME_TAXES`` -> high.
* slither: High-impact security-relevant detector -> high, Medium -> medium.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot._vendor.chain_registry import get_rpc_urls, is_evm
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.deep_scan_runners import (
    _run_decompile,
    _run_fraud_gnn,
    _run_honeypot_sell_sim,
    _run_pygod,
    _run_pyod,
    _run_sell_sim,
    _run_slither,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

__all__ = ["DeepScanReport", "deep_scan_address"]

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_CHAIN = "ethereum"
DEFAULT_RPC_TIMEOUT = 15.0

# Risk level -> human label. Higher is riskier.
RISK_LABELS: dict[int, str] = {0: "low", 1: "medium", 2: "high", 3: "critical"}


# ── Report model ─────────────────────────────────────────────────────────────


@dataclass
class DeepScanReport:
    """Outcome of a deep scan over one address."""

    address: str
    chain: str
    risk_scores: dict[str, Any]
    anomaly: dict[str, Any]
    decompiled: dict[str, Any]
    sell_sim: dict[str, Any]
    overall_risk: str
    findings: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "address": self.address,
            "chain": self.chain,
            "risk_scores": self.risk_scores,
            "anomaly": self.anomaly,
            "decompiled": self.decompiled,
            "sell_sim": self.sell_sim,
            "overall_risk": self.overall_risk,
            "findings": self.findings,
        }


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_chain(address: str, chain: str) -> str:
    """Resolve ``chain`` (``"auto"`` -> heuristic default)."""
    if chain and chain != "auto":
        return chain.lower()
    addr = (address or "").strip().lower()
    # EVM addresses are 0x + 40 hex chars; everything else is treated as Solana.
    if addr.startswith("0x") and len(addr) == 42:
        return DEFAULT_CHAIN
    return "solana"


def _build_fingerprint(address: str, chain: str) -> dict[str, Any]:
    """Build a FRAUD_FEATURES fingerprint for the address.

    Prefers the deterministic offline adapter (``app.ml_datasets.adapters``);
    falls back to an all-zeros fingerprint when it cannot be built, so the ML
    modules still receive a valid feature vector.
    """
    try:
        from app.ml_datasets.adapters import address_to_fingerprint

        return address_to_fingerprint(address, chain=chain)
    except Exception as exc:
        logger.warning("deep_scan_fingerprint_fallback", detail=str(exc))
        pyod_anomaly: Any | None
        try:
            from app import pyod_anomaly
        except ImportError:
            pyod_anomaly = None
        feature_names = getattr(pyod_anomaly, "FRAUD_FEATURES", ()) if pyod_anomaly is not None else ()
        return dict.fromkeys(feature_names, 0.0)


def _fetch_code(rpc_urls: Sequence[str], address: str, timeout: float) -> str:
    """Fetch runtime bytecode via ``eth_getCode`` across fallback RPC URLs.

    Returns the ``0x``-prefixed hex code, or ``""`` when the address has no
    code (EOA) or every RPC URL fails.
    """
    last_error: Exception | None = None
    for url in rpc_urls:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "eth_getCode", "params": [address, "latest"]},
                )
                resp.raise_for_status()
                payload = resp.json()
                if "error" in payload:
                    last_error = RuntimeError(f"rpc error: {payload['error']}")
                    continue
                result = payload.get("result")
                if isinstance(result, str) and result.startswith("0x"):
                    return result
        except (httpx.HTTPError, ValueError) as exc:  # ValueError = bad JSON
            last_error = exc
            continue
    if last_error is not None:
        logger.debug("deep_scan_fetch_code_failed: address=%s error=%s", address, str(last_error))
    return ""


# ── Public API ───────────────────────────────────────────────────────────────


def deep_scan_address(
    address: str,
    *,
    chain: str = "auto",
    transfers: Sequence[dict[str, Any]] | None = None,
    source_code: str | None = None,
    solc_version: str | None = None,
    rpc_timeout: float = DEFAULT_RPC_TIMEOUT,
) -> DeepScanReport:
    """Run the platform's ML + contract-analysis modules in one shot.

    Args:
        address: Address to scan (``0x``-prefixed EVM or Solana base58).
        chain: Chain name, or ``"auto"`` to resolve heuristically (``0x`` ->
            ethereum; otherwise solana).
        transfers: Optional transfer edges (``{"from_addr"/"source",
            "to_addr"/"target"}``) enabling PyGOD graph anomaly detection.
            When omitted, PyGOD is recorded as skipped.
        source_code: Optional verified Solidity source for Slither static
            analysis. When omitted, Slither is recorded as skipped.
        solc_version: Optional solc version hint for Slither.
        rpc_timeout: Per-RPC-request timeout in seconds.

    Returns:
        DeepScanReport — never raises for sub-module failures.
    """
    address = (address or "").strip()
    chain = _resolve_chain(address, chain)
    findings: list[dict[str, Any]] = []

    # 1) Fingerprint + ML anomaly modules.
    fingerprint = _build_fingerprint(address, chain)
    fraud_result, fraud_findings, fraud_level = _run_fraud_gnn(fingerprint)
    pyod_result, pyod_findings, pyod_level = _run_pyod(fingerprint)
    pygod_result, pygod_findings, pygod_level = _run_pygod(transfers)
    findings.extend(fraud_findings)
    findings.extend(pyod_findings)
    findings.extend(pygod_findings)

    # 2) Bytecode -> decompiled ABI (EVM only).
    decompiled: dict[str, Any]
    decompile_level = 0
    if is_evm(chain):
        code = _fetch_code(get_rpc_urls(chain), address, rpc_timeout)
        if not code or code == "0x":
            decompiled = {"success": False, "decompile_error": "no code at address (EOA or RPC unreachable)"}
            findings.append(
                {
                    "module": "heimdall_bytecode",
                    "level": "info",
                    "message": "no contract code at address; bytecode analysis skipped",
                }
            )
        else:
            decompiled, decompile_findings, decompile_level = _run_decompile(code)
            findings.extend(decompile_findings)
    else:
        decompiled = {
            "success": False,
            "decompile_error": f"bytecode analysis not supported for chain {chain!r} (EVM only)",
        }
        findings.append(
            {
                "module": "heimdall_bytecode",
                "level": "info",
                "message": f"bytecode analysis skipped: {chain} is not EVM",
            }
        )

    # 3) Sell simulation (pyrevm EVM local) + honeypot sell-sim check
    #    (Honeypot.is / GoPlus — also covers non-EVM chains) + Slither.
    sell_sim, sell_findings, sell_level = _run_sell_sim(address, chain)
    honeypot_sim, honeypot_findings, honeypot_level = _run_honeypot_sell_sim(address, chain)
    slither_result, slither_findings, slither_level = _run_slither(source_code, solc_version)
    findings.extend(sell_findings)
    findings.extend(honeypot_findings)
    findings.extend(slither_findings)

    # Merge the honeypot sell-sim check into the sell_sim payload. All its
    # keys are namespaced (``sell_sim_verdict`` / ``buy_tax`` / ...) so the
    # pyrevm fields (``simulation_success`` / ``tax_pct`` / ...) are never
    # clobbered; warnings are combined (not replaced) and the risk level is
    # the max of both.
    combined_warnings = list(sell_sim.get("warnings") or []) + list(honeypot_sim.get("warnings") or [])
    sell_sim = {**sell_sim, **honeypot_sim}
    sell_sim["warnings"] = combined_warnings
    sell_level = max(sell_level, honeypot_level)

    # 4) Aggregate risk.
    overall_level = max(fraud_level, pyod_level, pygod_level, decompile_level, sell_level, slither_level)
    overall_risk = RISK_LABELS.get(overall_level, "low")

    risk_scores: dict[str, Any] = {
        "fraud_gnn": {**fraud_result, "risk_level": fraud_level},
        "pyod": {**pyod_result, "risk_level": pyod_level},
        "pygod": {**pygod_result, "risk_level": pygod_level},
        "decompiled": {**decompiled, "risk_level": decompile_level},
        "sell_sim": {**sell_sim, "risk_level": sell_level},
        "slither": {**slither_result, "risk_level": slither_level},
        "overall_level": overall_level,
    }

    return DeepScanReport(
        address=address,
        chain=chain,
        risk_scores=risk_scores,
        anomaly={"fraud_gnn": fraud_result, "pyod": pyod_result, "pygod": pygod_result},
        decompiled=decompiled,
        sell_sim=sell_sim,
        overall_risk=overall_risk,
        findings=findings,
    )

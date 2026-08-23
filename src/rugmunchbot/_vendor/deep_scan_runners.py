# VENDORED 2026-08-23 from rmi-backend/app/deep_scan_runners.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Guarded sub-module runners for :mod:`app.deep_scan`.

Each runner executes one of the platform's ML / contract-analysis modules
(fraud_gnn, pyod, pygod, heimdall_bytecode, pyrevm_engine, slither_analyzer)
inside ``try/except`` so a missing optional dependency or module failure
becomes an ``error``-level finding instead of crashing the scan.

Contract: every runner returns ``(result_dict, findings, severity_level)``
where ``severity_level`` is ``0..3`` (low/medium/high/critical) and feeds the
aggregate risk derivation in :func:`app.deep_scan.deep_scan_address`. No
runner raises for module-level failures.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot._vendor.chain_registry import is_evm
from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger(__name__)

# ── Runner tunables ───────────────────────────────────────────────────────────

DEFAULT_DECOMPILE_TIMEOUT_SECS = 25

# pyrevm sell-simulation risk labels -> severity level.
SELL_RISK_LEVELS: dict[str, int] = {
    "critical": 3,
    "dangerous": 2,
    "warning": 1,
    "safe": 0,
    "unknown": 0,
}

# ── Honeypot sell-sim check (Honeypot.is + GoPlus fallback) ────────────────

#: Honeypot.is ``/v2/IsHoneypot`` chain IDs (EVM chains it supports).
HONEYPOT_IS_CHAIN_IDS: dict[str, str] = {
    "ethereum": "1",
    "eth": "1",
    "mainnet": "1",
    "bsc": "56",
    "bnb": "56",
    "binance": "56",
    "polygon": "137",
    "matic": "137",
    "arbitrum": "42161",
    "arb": "42161",
    "optimism": "10",
    "op": "10",
    "base": "8453",
    "avalanche": "43114",
    "avax": "43114",
    "fantom": "250",
    "ftm": "250",
    "gnosis": "100",
    "xdai": "100",
    "linea": "59144",
    "zksync": "324",
}

#: GoPlus ``token_security`` chain IDs — mirrors the scanner module map
#: (``app.domains.scanner.modules._core.GOPLUS_CHAIN_IDS``) and adds aliases.
GOPLUS_CHAIN_IDS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "1",
    "eth": "1",
    "mainnet": "1",
    "bsc": "56",
    "bnb": "56",
    "binance": "56",
    "base": "8453",
    "arbitrum": "42161",
    "arb": "42161",
    "polygon": "137",
    "matic": "137",
    "avalanche": "43114",
    "avax": "43114",
    "optimism": "10",
    "op": "10",
    "fantom": "250",
    "ftm": "250",
    "gnosis": "100",
    "xdai": "100",
    "linea": "59144",
    "zksync": "324",
}

#: Sell-tax (percent) at/above which the honeypot sell-sim verdict is
#: ``EXTREME_TAXES`` (surfaced as a red "BLOCKED / EXTREME taxes" line).
SELL_SIM_EXTREME_TAX_PCT = 25.0

#: Timeout for each honeypot sell-sim HTTP call (seconds).
HONEYPOT_SIM_TIMEOUT_SECS = 8.0

# Function-name substrings that indicate rug/exit-scam capability in a
# decompiled ABI (self-contained subset of the scanner's known patterns).
DANGEROUS_FUNCTION_PATTERNS: tuple[str, ...] = (
    "withdraw",
    "drain",
    "setowner",
    "emergencywithdraw",
    "pullfunds",
    "takefunds",
    "sweepfunds",
    "rescuetokens",
    "takeownership",
    "forcewithdraw",
    "manualwithdraw",
    "adminwithdraw",
    "ownerwithdraw",
    "setswapandliquify",
    "settradingenabled",
    "setcooldownenabled",
    "blacklist",
    "setexcludedfromfee",
    "setmaxbuyamount",
    "setmaxtxamount",
    "setmaxwalletamount",
    "setfees",
    "setfeerate",
    "settaxrate",
)


def _run_fraud_gnn(fingerprint: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    try:
        from app.fraud_gnn import get_fraud_gnn

        prob, is_fraud, model = get_fraud_gnn().predict(fingerprint)
        level = 2 if prob >= 0.7 else 1 if prob >= 0.4 else 0
        findings: list[dict[str, Any]] = [
            {
                "module": "fraud_gnn",
                "level": "warning" if level >= 1 else "info",
                "message": f"fraud probability {prob:.4f} (model={model})",
            }
        ]
        return (
            {"fraud_probability": prob, "is_fraud": is_fraud, "model": model, "risk_level": level},
            findings,
            level,
        )
    except Exception as exc:
        logger.warning("deep_scan_fraud_gnn_failed", detail=str(exc))
        return (
            {"error": str(exc), "risk_level": 0},
            [{"module": "fraud_gnn", "level": "error", "message": f"fraud_gnn failed: {exc}"}],
            0,
        )


def _run_pyod(fingerprint: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    try:
        from app.pyod_anomaly import get_pyod_anomaly

        result = get_pyod_anomaly().score(fingerprint)
        level = 2 if result.is_anomaly else 1 if result.anomaly_score >= 0.6 else 0
        findings: list[dict[str, Any]] = [
            {
                "module": "pyod_anomaly",
                "level": "warning" if level >= 1 else "info",
                "message": f"anomaly score {result.anomaly_score:.4f} (model={result.model_type})",
            }
        ]
        return (result.to_dict(), findings, level)
    except Exception as exc:
        logger.warning("deep_scan_pyod_failed", detail=str(exc))
        return (
            {"anomaly_score": 0.0, "is_anomaly": False, "model_type": "error", "error": str(exc), "risk_level": 0},
            [{"module": "pyod_anomaly", "level": "error", "message": f"pyod failed: {exc}"}],
            0,
        )


def _run_pygod(
    transfers: Sequence[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Run PyGOD graph anomaly detection when a transfer graph is available."""
    if not transfers:
        return (
            {
                "anomaly_score": 0.0,
                "is_anomaly": False,
                "model_type": "skipped",
                "warnings": ["no transfer graph provided; PyGOD skipped"],
                "risk_level": 0,
            },
            [{"module": "pygod_anomaly", "level": "info", "message": "PyGOD skipped: no transfer graph provided"}],
            0,
        )
    try:
        import networkx as nx

        from app.pygod_anomaly import get_pygod_anomaly

        graph: Any = nx.DiGraph()
        for edge in transfers:
            src = edge.get("from_addr") or edge.get("source")
            dst = edge.get("to_addr") or edge.get("target")
            if src and dst and str(src) != str(dst):
                graph.add_edge(str(src), str(dst))
        if graph.number_of_nodes() == 0:
            return (
                {
                    "anomaly_score": 0.0,
                    "is_anomaly": False,
                    "model_type": "skipped",
                    "warnings": ["transfer graph has no edges; PyGOD skipped"],
                    "risk_level": 0,
                },
                [{"module": "pygod_anomaly", "level": "info", "message": "PyGOD skipped: empty transfer graph"}],
                0,
            )

        result = get_pygod_anomaly().score(graph)
        level = 2 if result.is_anomaly else 0
        findings: list[dict[str, Any]] = [
            {
                "module": "pygod_anomaly",
                "level": "warning" if level >= 1 else "info",
                "message": f"graph anomaly score {result.anomaly_score:.4f} (model={result.model_type})",
            }
        ]
        return (result.to_dict(), findings, level)
    except Exception as exc:
        logger.warning("deep_scan_pygod_failed", detail=str(exc))
        return (
            {"anomaly_score": 0.0, "is_anomaly": False, "model_type": "error", "error": str(exc), "risk_level": 0},
            [{"module": "pygod_anomaly", "level": "error", "message": f"pygod failed: {exc}"}],
            0,
        )


def _run_decompile(code: str) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Decompile raw bytecode into an ABI (unverified contracts)."""
    try:
        from app.heimdall_bytecode import analyze_bytecode

        result = analyze_bytecode(code, rpc_url=None, timeout_secs=DEFAULT_DECOMPILE_TIMEOUT_SECS)
        payload = result.to_dict()
        findings: list[dict[str, Any]] = []
        if not result.success:
            message = result.decompile_error or result.storage_error or "unknown error"
            findings.append(
                {"module": "heimdall_bytecode", "level": "warning", "message": f"decompile incomplete: {message}"}
            )
            return payload, findings, 0

        dangerous = sorted(
            {
                str(fn.get("name", ""))
                for fn in result.functions
                if any(pattern in str(fn.get("name", "")).lower() for pattern in DANGEROUS_FUNCTION_PATTERNS)
            }
        )
        if dangerous:
            findings.append(
                {
                    "module": "heimdall_bytecode",
                    "level": "warning",
                    "message": f"dangerous function names detected: {', '.join(dangerous[:5])}",
                }
            )
            return payload, findings, 1
        findings.append(
            {
                "module": "heimdall_bytecode",
                "level": "info",
                "message": f"decompiled {len(result.functions)} functions from bytecode",
            }
        )
        return payload, findings, 0
    except Exception as exc:
        logger.warning("deep_scan_decompile_failed", detail=str(exc))
        return (
            {"success": False, "decompile_error": str(exc), "risk_level": 0},
            [{"module": "heimdall_bytecode", "level": "error", "message": f"decompile failed: {exc}"}],
            0,
        )


def _run_sell_sim(address: str, chain: str) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Run the pyrevm sell round-trip simulation (EVM chains only)."""
    if not is_evm(chain):
        return (
            {
                "simulation_success": False,
                "chain": chain,
                "warnings": ["sell simulation skipped: chain is not EVM"],
                "risk_level": 0,
            },
            [{"module": "pyrevm_engine", "level": "info", "message": f"sell simulation skipped: {chain} is not EVM"}],
            0,
        )
    try:
        from app.pyrevm_engine import simulate_sell_round_trip

        result = simulate_sell_round_trip(address, chain=chain)
        payload = asdict(result)
        level = SELL_RISK_LEVELS.get(result.risk, 0)
        findings: list[dict[str, Any]] = []
        if result.simulation_success:
            findings.append(
                {
                    "module": "pyrevm_engine",
                    "level": "warning" if level >= 2 else "info",
                    "message": f"sell simulation risk={result.risk} tax_pct={result.tax_pct}",
                }
            )
        else:
            findings.append(
                {
                    "module": "pyrevm_engine",
                    "level": "info",
                    "message": f"sell simulation failed: {'; '.join(result.warnings[:2]) or 'unknown reason'}",
                }
            )
        if result.is_honeypot:
            findings.append(
                {
                    "module": "pyrevm_engine",
                    "level": "warning",
                    "message": "honeypot signature detected (can sell, sell-to-pair reverts)",
                }
            )
        return (payload, findings, level)
    except Exception as exc:
        logger.warning("deep_scan_sell_sim_failed", detail=str(exc))
        return (
            {"simulation_success": False, "warnings": [str(exc)], "risk_level": 0},
            [{"module": "pyrevm_engine", "level": "error", "message": f"sell simulation failed: {exc}"}],
            0,
        )


def _to_float_or(value: Any, default: float) -> float:
    """Best-effort float conversion (None/bad input → ``default``)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _honeypot_is_scan(address: str, chain_id: str) -> dict[str, Any]:
    """Call Honeypot.is ``/v2/IsHoneypot`` (free, no API key).

    Returns a scanned payload with the simulation taxes and honeypot verdict,
    or an error payload. Never raises.
    """
    try:
        with httpx.Client(timeout=HONEYPOT_SIM_TIMEOUT_SECS) as client:
            resp = client.get(
                "https://api.honeypot.is/v2/IsHoneypot",
                params={"address": address, "chainID": chain_id},
            )
            if resp.status_code != 200:
                return {"result": "error", "error": f"Honeypot.is HTTP {resp.status_code}"}
            data = resp.json()
        hp = data.get("honeypotResult") or {}
        sim = hp.get("simulationResult") or {}
        return {
            "result": "scanned",
            "is_honeypot": bool(hp.get("isHoneypot") or sim.get("isHoneypot")),
            "buy_tax": _to_float_or(sim.get("buyTax"), 0.0),
            "sell_tax": _to_float_or(sim.get("sellTax"), 0.0),
            "transfer_tax": _to_float_or(sim.get("transferTax"), 0.0),
        }
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        logger.debug("honeypot_is_scan_failed: %s", str(exc)[:120])
        return {"result": "error", "error": "Honeypot.is unreachable"}


def _goplus_sell_sim_scan(address: str, chain: str) -> dict[str, Any]:
    """Call GoPlus ``token_security`` as the honeypot sell-sim fallback.

    Reuses the already-integrated ``GOPLUS_API_KEY`` (sent as the
    ``X-API-Key`` header when set; the free tier also works without it) —
    no new paid dependency. Returns a scanned payload with the tax flags
    and honeypot verdict, or an error payload. Never raises.
    """
    chain_id = GOPLUS_CHAIN_IDS.get(chain, chain)
    headers: dict[str, str] = {}
    key = os.getenv("GOPLUS_API_KEY", "")
    if key:
        headers["X-API-Key"] = key
    try:
        with httpx.Client(timeout=HONEYPOT_SIM_TIMEOUT_SECS) as client:
            resp = client.get(
                f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
                params={"contract_addresses": address.lower()},
                headers=headers,
            )
            if resp.status_code != 200:
                return {"result": "error", "error": f"GoPlus HTTP {resp.status_code}"}
            data = resp.json()
        result = data.get("result") or {}
        token = result.get(address.lower()) if isinstance(result, dict) else None
        if not isinstance(token, dict) or not token:
            return {"result": "error", "error": "Token not indexed by GoPlus"}
        return {
            "result": "scanned",
            "is_honeypot": str(token.get("is_honeypot", "0")) == "1",
            "buy_tax": _to_float_or(token.get("buy_tax"), 0.0),
            "sell_tax": _to_float_or(token.get("sell_tax"), 0.0),
            "transfer_tax": None,  # GoPlus token_security exposes no transfer-tax field
            "transfer_pausable": str(token.get("transfer_pausable", "0")) == "1",
        }
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        logger.debug("goplus_sell_sim_scan_failed: %s", str(exc)[:120])
        return {"result": "error", "error": "GoPlus unavailable"}


def _run_honeypot_sell_sim(address: str, chain: str) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Honeypot sell-simulation check — Honeypot.is primary, GoPlus fallback.

    Primary: Honeypot.is ``/v2/IsHoneypot`` (free, no key) — a real buy/sell
    simulation returning ``buyTax`` / ``sellTax`` / ``transferTax`` and an
    ``isHoneypot`` verdict (EVM chains only).
    Fallback: GoPlus ``token_security`` (reuses the existing
    ``GOPLUS_API_KEY``; no new paid dep) — ``buy_tax`` / ``sell_tax`` /
    ``is_honeypot``; also the only source for Solana.

    Verdict: ``BLOCKED`` when the source flags a honeypot (cannot sell);
    ``EXTREME_TAXES`` when sell tax >= ``SELL_SIM_EXTREME_TAX_PCT``;
    ``OK`` otherwise; ``unavailable`` when no source is reachable (fail-open).

    Returns ``(result, findings, severity 0..3)`` — never raises.
    """
    chain_norm = (chain or "").lower()
    payload: dict[str, Any] | None = None
    source_name = ""

    hp_chain_id = HONEYPOT_IS_CHAIN_IDS.get(chain_norm)
    if hp_chain_id:
        candidate = _honeypot_is_scan(address, hp_chain_id)
        if candidate.get("result") == "scanned":
            payload, source_name = candidate, "honeypot.is"

    if payload is None:
        candidate = _goplus_sell_sim_scan(address, chain_norm)
        if candidate.get("result") == "scanned":
            payload, source_name = candidate, "goplus"

    if payload is None:
        return (
            {
                "check": "honeypot_sell_sim",
                "result": "unavailable",
                "source": "unavailable",
                "sell_sim_verdict": "unavailable",
                "buy_tax": None,
                "sell_tax": None,
                "transfer_tax": None,
                "warnings": ["sell simulation data source unreachable"],
            },
            [
                {
                    "module": "honeypot_sell_sim",
                    "level": "info",
                    "message": "honeypot sell-sim check unavailable (Honeypot.is + GoPlus unreachable)",
                }
            ],
            0,
        )

    is_honeypot = bool(payload.get("is_honeypot"))
    sell_tax = _to_float_or(payload.get("sell_tax"), 0.0)
    if is_honeypot:
        verdict = "BLOCKED"
        level = 3  # critical — the source cannot sell the token back
    elif sell_tax >= SELL_SIM_EXTREME_TAX_PCT:
        verdict = "EXTREME_TAXES"
        level = 2  # high — extreme sell taxes
    else:
        verdict = "OK"
        level = 0

    result = {
        "check": "honeypot_sell_sim",
        "result": "scanned",
        "source": source_name,
        "sell_sim_verdict": verdict,
        "is_honeypot": is_honeypot,
        "buy_tax": payload.get("buy_tax"),
        "sell_tax": payload.get("sell_tax"),
        "transfer_tax": payload.get("transfer_tax"),
        "transfer_pausable": payload.get("transfer_pausable"),
        "warnings": [],
    }
    verdict_hint = "cannot sell (honeypot)" if is_honeypot else f"sell tax {sell_tax:.1f}%"
    findings = [
        {
            "module": "honeypot_sell_sim",
            "level": "warning" if level >= 2 else "info",
            "message": (
                f"honeypot sell-sim verdict={verdict} ({verdict_hint}) "
                f"source={source_name} buy_tax={result['buy_tax']} sell_tax={result['sell_tax']}"
            ),
        }
    ]
    return result, findings, level


def _run_slither(source_code: str | None, solc_version: str | None) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Run Slither source analysis when verified Solidity source is provided."""
    if not source_code or not source_code.strip():
        return (
            {"success": False, "skipped": "no verified source provided", "detectors": [], "risk_level": 0},
            [
                {
                    "module": "slither_analyzer",
                    "level": "info",
                    "message": "Slither skipped: no verified source provided",
                }
            ],
            0,
        )
    try:
        from app.slither_analyzer import analyze_source, is_security_relevant

        result = analyze_source(source_code, solc_version)
        payload = result.to_dict()
        findings: list[dict[str, Any]] = []
        if not result.success:
            findings.append(
                {"module": "slither_analyzer", "level": "warning", "message": f"slither failed: {result.error}"}
            )
            return payload, findings, 0

        high = [
            d for d in result.detectors if d.get("impact") == "High" and is_security_relevant(str(d.get("check", "")))
        ]
        medium = [
            d for d in result.detectors if d.get("impact") == "Medium" and is_security_relevant(str(d.get("check", "")))
        ]
        level = 2 if high else 1 if medium else 0
        if high:
            checks = ", ".join(str(d.get("check", "")) for d in high[:5])
            findings.append(
                {
                    "module": "slither_analyzer",
                    "level": "warning",
                    "message": f"{len(high)} high-impact security-relevant findings: {checks}",
                }
            )
        elif medium:
            findings.append(
                {
                    "module": "slither_analyzer",
                    "level": "warning",
                    "message": f"{len(medium)} medium-impact security-relevant findings",
                }
            )
        else:
            findings.append(
                {
                    "module": "slither_analyzer",
                    "level": "info",
                    "message": f"slither ran clean: {len(result.detectors)} detectors total",
                }
            )
        return (payload, findings, level)
    except Exception as exc:
        logger.warning("deep_scan_slither_failed", detail=str(exc))
        return (
            {"success": False, "detectors": [], "error": str(exc), "risk_level": 0},
            [{"module": "slither_analyzer", "level": "error", "message": f"slither failed: {exc}"}],
            0,
        )

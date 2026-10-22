"""Address extraction (Solana + EVM) and scanner-verdict mapping.

Pure text parsing here; the network call lives in :func:`scan_verdict`, the
single seam a test (or the dry-run harness) replaces. A scan failure returns
``unknown`` — moderation never blocks a message because a scanner is down.
"""

from __future__ import annotations

import asyncio
import re

from rugmunchbot.moderation.models import AddressVerdict
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.addresses")

MAX_ADDRESSES = 3
SCAN_TIMEOUT_S = 6.0

_EVM_RE = re.compile(r"\b(0x[a-fA-F0-9]{40})\b")
_SOL_RE = re.compile(r"(?<![0-9A-Za-z])([1-9A-HJ-NP-Za-km-z]{32,44})(?![0-9A-Za-z])")
_NEVER_SCAN = {"avoid", "ban"}


def _valid(addr: str) -> bool:
    """Reject look-alikes with the vendored validators (cheap, no network)."""
    try:
        from rugmunchbot._vendor.shared_utils import is_evm_address, is_solana_address

        return bool(is_evm_address(addr) or is_solana_address(addr))
    except Exception:  # pragma: no cover — validator unavailable
        return True


def extract_addresses(text: str, limit: int = MAX_ADDRESSES) -> list[str]:
    """Return up to ``limit`` distinct, validator-approved addresses."""
    if not text:
        return []
    found: list[str] = []
    for match in _EVM_RE.finditer(text):
        found.append(match.group(1))
    for match in _SOL_RE.finditer(text):
        candidate = match.group(1)
        if _EVM_RE.fullmatch(candidate):
            continue
        found.append(candidate)
    out: list[str] = []
    for addr in found:
        if addr in out or not _valid(addr):
            continue
        out.append(addr)
        if len(out) >= limit:
            break
    return out


def _chain_of(addr: str) -> str:
    return "solana" if not addr.startswith("0x") else "ethereum"


def verdict_from_payload(address: str, payload: dict | None) -> AddressVerdict:
    """Map a v2 scanner payload (safety_score/risk_level) onto a verdict."""
    chain = _chain_of(address)
    if not payload:
        return AddressVerdict(address=address, chain=chain)
    risk = str(payload.get("risk_level") or "").lower()
    try:
        score = int(payload.get("safety_score"))
    except (TypeError, ValueError):
        score = None
    if risk in _NEVER_SCAN or (score is not None and score < 20):
        verdict = "avoid"
    elif risk in {"high", "critical"} or (score is not None and score < 40):
        verdict = "caution"
    elif risk in {"low", "safe", "minimal"} or (score is not None and score >= 60):
        verdict = "ok"
    else:
        verdict = "unknown"
    flags = payload.get("risk_flags") or []
    reason = risk or (f"score {score}" if score is not None else "no signal")
    if isinstance(flags, list) and flags:
        reason = f"{reason}: {', '.join(str(f) for f in flags[:3])}"
    return AddressVerdict(
        address=address,
        chain=chain,
        risk_level=risk or "unknown",
        safety_score=score,
        verdict=verdict,
        reason=reason,
    )


async def scan_verdict(address: str) -> AddressVerdict:
    """Scan one address via the canonical scanner. Never raises."""
    try:
        from rugmunchbot.tools.scanner import scan_token_v2

        payload = await asyncio.wait_for(scan_token_v2(address), timeout=SCAN_TIMEOUT_S)
    except TimeoutError:
        logger.info("address scan timed out", extra={"address": address})
        return AddressVerdict(address=address, chain=_chain_of(address), reason="timeout")
    except Exception:
        logger.debug("address scan failed", exc_info=True)
        return AddressVerdict(address=address, chain=_chain_of(address), reason="scan-error")
    return verdict_from_payload(address, payload)


async def assess(text: str, limit: int = MAX_ADDRESSES) -> dict[str, AddressVerdict]:
    """Scan every address in *text* concurrently; returns address -> verdict."""
    addrs = extract_addresses(text, limit)
    if not addrs:
        return {}
    results = await asyncio.gather(*(scan_verdict(a) for a in addrs), return_exceptions=True)
    out: dict[str, AddressVerdict] = {}
    for addr, res in zip(addrs, results, strict=False):
        out[addr] = res if isinstance(res, AddressVerdict) else AddressVerdict(address=addr)
    return out


__all__ = ["assess", "extract_addresses", "scan_verdict", "verdict_from_payload"]

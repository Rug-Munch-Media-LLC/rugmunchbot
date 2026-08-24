# VENDORED 2026-08-24 from rmi-backend/app/osint/wallet.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
OSINT wallet attribution — attribute a crypto address to known entities.

This is the "track down the scammer's wallet" capability: given an EVM
(``0x…``) or Solana (base58) address, aggregate:

- **chain** — auto-detected (EVM vs Solana) and normalized
- **entity labels** — local known-entity registry (CEX, DeFi, mixer, scam)
- **exchange attribution** — known CEX hot/deposit wallet matches
- **scam reports** — Chainabuse public API (category + description)
- **sanctions** — local OFAC crypto-sanctions-list match (entity + program)

Contract: ``lookup_wallet(address, *, chain="auto", timeout=60) -> OsintReport``
(see :mod:`app.osint`). Every source is isolated — a failure appends to
``report.errors`` and never aborts the remaining sources.

Dependencies: stdlib + ``httpx`` + ``eth-utils`` (optional; EVM checksum
falls back to lowercase). All already pinned in pyproject.

Reuses the existing attribution stack instead of duplicating it:
``app.entity_registry`` (entity/exchange/mixer/scam index),
``app.wallet_label_loader`` (Redis-backed label store), and the on-disk
``crypto-sanctions-list`` OFAC dataset.

Usage::

    from rugmunchbot._vendor.osint.wallet import lookup_wallet

    report = lookup_wallet("0xBE0eB53FC46b790099138e3d32C721856d41e865", timeout=30)
    print(report.to_dict())
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Final, cast

try:
    import httpx
except ImportError as exc:  # pragma: no cover — httpx is a pinned runtime dependency
    from rugmunchbot._vendor.osint import OsintNotAvailable

    raise OsintNotAvailable("wallet attribution requires httpx: pip install httpx") from exc

from rugmunchbot._vendor.osint import OsintFinding, OsintReport

logger = logging.getLogger(__name__)

# ─── Tunables ────────────────────────────────────────────────────────

#: Chainabuse public screening endpoint (Basic auth with key as user+password).
CHAINABUSE_REPORTS_URL: Final = "https://api.chainabuse.com/v0/reports"
#: Chainabuse v1 endpoint used by the repo's deep-intelligence client (Bearer).
CHAINABUSE_V1_URL: Final = "https://api.chainabuse.com/api/v1/address/{address}/reports"

#: Candidate paths for the OFAC crypto-sanctions-list, cheapest-first.
SANCTIONS_PATHS: Final = (
    "/srv/crypto-sanctions-list/data/sanctioned-addresses.json",
    "/app/data/crypto-sanctions-list/data/sanctioned-addresses.json",
    "/app/data/wallet-labels/ofac_sanctions.json",
)

EVM_ADDRESS_RE: Final = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS_RE: Final = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

#: Lazy-loaded OFAC index: lowercase address -> {type, entries:[...]}.
_sanctions_cache: dict[str, Any] | None = None


def _normalize(address: str, chain: str = "auto") -> tuple[str, str | None, str | None]:
    """Normalize an address and detect its chain.

    Returns ``(display_address, chain_tag, error)`` where ``chain_tag`` is
    ``"evm"`` or ``"solana"``. EVM addresses are checksummed when eth-utils is
    importable (else lowercased); Solana/base58 addresses are validated by
    alphabet + length. A non-matching ``chain`` override surfaces as an error
    but does not abort the lookup.
    """
    raw = address.strip()
    if not raw:
        return "", None, "empty address"

    if EVM_ADDRESS_RE.match(raw):
        try:
            from eth_utils.address import to_checksum_address

            display: str = to_checksum_address(raw)
        except Exception as exc:  # checksumming is best-effort
            logger.debug("eth_utils checksum failed; using lowercase", exc_info=exc)
            display = raw.lower()
        if chain not in ("auto", "evm", "ethereum"):
            return display, "evm", f"chain override {chain!r} does not match EVM address"
        return display, "evm", None

    if SOLANA_ADDRESS_RE.match(raw):
        if chain not in ("auto", "solana", "sol"):
            return raw, "solana", f"chain override {chain!r} does not match Solana address"
        return raw, "solana", None

    return raw.lower(), None, f"unrecognized address format: {raw[:24]!r}"


def _add_finding(
    report: OsintReport,
    source: str,
    category: str,
    summary: str,
    *,
    confidence: str = "medium",
    **data: Any,
) -> None:
    """Append a finding with an explicit confidence.

    ``OsintReport.add()`` has no ``confidence`` keyword (it would land inside
    ``data``), so construct :class:`OsintFinding` directly.
    """
    report.findings.append(
        OsintFinding(
            source=source,
            category=category,
            summary=summary,
            confidence=confidence,
            data=data,
        )
    )


def _entity_labels(report: OsintReport, key: str) -> None:
    """Entity labels + exchange attribution from the local registry.

    Reuses :func:`app.entity_registry.get_entity_registry` — the internal
    CEX/DeFi/mixer/scam index (never duplicates it). When no match exists a
    low-confidence "no known entity label" finding is added so callers can
    tell a checked-but-unlabeled address apart from an unchecked one.
    """
    try:
        from app.entity_registry import (
            ENTITY_TYPE_CEX_HOT_WALLET,
            ENTITY_TYPE_EXCHANGE,
            get_entity_registry,
        )

        registry = get_entity_registry()
        match = registry.classify_address(key)
        if match is None:
            _add_finding(
                report,
                source="entity_registry",
                category="entity_label",
                summary="no known entity label",
                confidence="low",
                address=key,
            )
            return

        is_exchange = match.entity_type in (ENTITY_TYPE_CEX_HOT_WALLET, ENTITY_TYPE_EXCHANGE)
        _add_finding(
            report,
            source="entity_registry",
            category="exchange" if is_exchange else "entity_label",
            summary=(
                f"exchange: {match.entity_name} ({match.entity_type})"
                if is_exchange
                else f"{match.entity_name} ({match.entity_type})"
            ),
            confidence="high" if match.source == "internal" else "medium",
            address=match.address,
            entity=match.entity_name,
            entity_type=match.entity_type,
            source_detail=match.source,
        )
    except Exception as exc:
        report.errors.append(f"entity_registry: {exc}")


def _redis_label(report: OsintReport, key: str) -> None:
    """Best-effort label lookup from the Redis-backed wallet label loader.

    Reuses :func:`app.wallet_label_loader.lookup_wallet_label` (Solana CEX /
    DApp / DeFi labels, Ethereum phishing, OFAC entries). Silently skipped
    when Redis is unavailable — the file-based sources still run.
    """
    try:
        from rugmunchbot._vendor.redis_client import get_redis
        from app.wallet_label_loader import lookup_wallet_label

        redis_client = get_redis()
        if redis_client is None:
            return
        label = lookup_wallet_label(key, redis_client=redis_client)  # pyright: ignore[reportUnknownVariableType]
        if not label:
            return
        name = label.get("name") or label.get("tag") or label.get("label_type") or label.get("project") or "unknown"
        scalar = {k: v for k, v in label.items() if isinstance(v, (str, int, float, bool))}
        _add_finding(
            report,
            source="wallet_label_loader",
            category="entity_label",
            summary=f"label: {name}",
            confidence="medium",
            **scalar,
        )
    except Exception as exc:
        report.errors.append(f"wallet_label_loader: {exc}")


def _parse_chainabuse_reports(payload: dict[str, Any]) -> list[OsintFinding]:
    """Parse a Chainabuse API payload into findings."""
    findings: list[OsintFinding] = []
    reports = payload.get("reports")
    if not isinstance(reports, list):
        return findings
    for rpt in reports:
        if not isinstance(rpt, dict):
            continue
        rpt = cast("dict[str, Any]", rpt)
        category = str(rpt.get("category") or "unknown")
        description = str(rpt.get("description") or rpt.get("report") or "")
        summary = f"Chainabuse: {category}"
        if description:
            summary = f"{summary} — {description}"
        trusted = str(rpt.get("trusted", "")).lower() == "true"
        findings.append(
            OsintFinding(
                source="chainabuse",
                category="scam_report",
                summary=summary[:300],
                confidence="high" if trusted else "medium",
                data={
                    "address": rpt.get("address") or "",
                    "category": category,
                    "chain": rpt.get("chain") or "",
                    "report_id": rpt.get("id") or rpt.get("report_id") or "",
                    "reported_at": rpt.get("reportedAt") or rpt.get("reported_at") or rpt.get("date") or "",
                    "trusted": trusted,
                },
            )
        )
    return findings


def _chainabuse_reports(report: OsintReport, address: str, timeout: int) -> None:
    """Scam reports from Chainabuse (public API, key optional).

    Attempt order (Chainabuse docs: ``GET /v0/reports?address=`` with HTTP
    Basic auth using the API key as user *and* password):
      1. with a key  -> v0 (Basic) then v1 (Bearer) fallback
      2. without key -> v0 unauthenticated (expected to 401)
    A 401/403 without a key surfaces as a clear "key required" error rather
    than a raw HTTP code. The key is read straight from the environment (the
    same source pydantic-settings uses for ``CHAINABUSE_API_KEY``) so this
    module stays bounded and importable without the full app settings stack.
    """
    api_key = os.environ.get("CHAINABUSE_API_KEY", "")
    query_url = f"{CHAINABUSE_REPORTS_URL}?address={address}"
    attempts: list[tuple[str, dict[str, str], tuple[str, str] | None]] = []
    if api_key:
        attempts.append((query_url, {"Accept": "application/json"}, (api_key, api_key)))
        attempts.append((CHAINABUSE_V1_URL.format(address=address), {"Authorization": f"Bearer {api_key}"}, None))
    else:
        attempts.append((query_url, {"Accept": "application/json"}, None))

    payload: dict[str, Any] | None = None
    last_error = ""
    for url, headers, auth in attempts:
        try:
            resp = httpx.get(url, headers=headers, auth=auth, timeout=timeout)
        except (httpx.HTTPError, OSError) as exc:
            last_error = str(exc)
            continue
        if resp.status_code == 200:
            try:
                payload = resp.json()
            except ValueError as exc:
                last_error = f"invalid JSON: {exc}"
                continue
            break
        last_error = f"HTTP {resp.status_code}"
    else:
        if not api_key and last_error in ("HTTP 401", "HTTP 403"):
            report.errors.append("chainabuse: API requires an API key (CHAINABUSE_API_KEY unset)")
        elif api_key and last_error in ("HTTP 401", "HTTP 403"):
            report.errors.append(f"chainabuse: API key rejected ({last_error})")
        else:
            report.errors.append(f"chainabuse: {last_error or 'no response'}")
        return

    if payload is None:  # unreachable: for/else above returns first
        report.errors.append("chainabuse: no payload")
        return
    findings = _parse_chainabuse_reports(payload)
    report.findings.extend(findings)
    if not findings:
        _add_finding(
            report,
            source="chainabuse",
            category="scam_report",
            summary="no Chainabuse reports",
            confidence="medium",
            address=address,
        )


def _load_sanctions() -> dict[str, Any] | None:
    """Lazily load the OFAC crypto-sanctions-list.

    Supports both the ``crypto-sanctions-list`` format (``{"addresses": …}``)
    and the repo's flat ``ofac_sanctions.json`` list format. Returns ``None``
    when no usable file is present.
    """
    global _sanctions_cache
    if _sanctions_cache is not None:
        return _sanctions_cache or None
    for path in SANCTIONS_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            logger.warning("failed to load sanctions list %s: %s", path, exc)
            continue
        index: dict[str, Any] = {}
        if isinstance(data, dict) and isinstance(data.get("addresses"), dict):
            index = cast("dict[str, Any]", data["addresses"])
        elif isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict) and entry.get("address"):
                    index[str(entry["address"]).lower()] = cast("dict[str, Any]", entry)
        if index:
            _sanctions_cache = index
            logger.info("loaded sanctions list from %s (%d addresses)", path, len(index))
            return index
    _sanctions_cache = {}
    return None


def _sanctions(report: OsintReport, key: str) -> None:
    """OFAC sanctions match from the local crypto-sanctions-list."""
    sanctions = _load_sanctions()
    if sanctions is None:
        report.errors.append("sanctions: no local OFAC list found")
        return
    entry = sanctions.get(key)
    if entry is None:
        _add_finding(
            report,
            source="ofac_sanctions",
            category="sanction",
            summary="no sanctions match",
            confidence="medium",
            address=key,
        )
        return

    entry_dict = cast("dict[str, Any]", entry) if isinstance(entry, dict) else {}
    entries = entry_dict.get("entries")
    if not isinstance(entries, list) or not entries:
        _add_finding(
            report,
            source="ofac_sanctions",
            category="sanction",
            summary="address listed in OFAC sanctions list",
            confidence="high",
            address=key,
            type=entry_dict.get("type") or "",
        )
        return
    for item in entries:
        if not isinstance(item, dict):
            continue
        item = cast("dict[str, Any]", item)
        entity = str(item.get("entity") or "unknown")
        program = str(item.get("program") or "")
        summary = f"OFAC sanctioned: {entity}" + (f" [{program}]" if program else "")
        _add_finding(
            report,
            source="ofac_sanctions",
            category="sanction",
            summary=summary,
            confidence="high",
            address=key,
            entity=entity,
            program=program,
            uid=str(item.get("uid") or ""),
        )


def lookup_wallet(address: str, *, chain: str = "auto", timeout: int = 60) -> OsintReport:
    """Attribute a crypto address: chain, labels, exchange, scams, sanctions.

    Args:
        address: EVM ``0x…`` or Solana/base58 address.
        chain: ``"auto"`` (default), ``"evm"``/``"ethereum"`` or
            ``"solana"``/``"sol"`` — validates the override against the
            detected format but never aborts the lookup.
        timeout: per-HTTP-request timeout in seconds for external sources.

    Returns:
        Aggregated :class:`OsintReport`; per-source failures land in
        ``report.errors``.
    """
    report = OsintReport(target=address, target_type="wallet")

    display, detected_chain, err = _normalize(address, chain)
    if err:
        report.errors.append(f"normalization: {err}")
        if detected_chain is None:
            # Unrecognizable format — nothing to look up.
            return report
        # Recognized chain but conflicting override — warn and continue.

    key = display.lower()
    _add_finding(
        report,
        source="address",
        category="chain",
        summary=f"{detected_chain} address",
        confidence="high",
        address=display,
        chain=detected_chain,
        requested_chain=chain,
    )

    _entity_labels(report, key)
    _redis_label(report, key)
    _chainabuse_reports(report, display, timeout)
    _sanctions(report, key)
    return report


__all__ = ["lookup_wallet"]

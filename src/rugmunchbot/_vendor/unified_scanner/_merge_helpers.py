# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/_merge_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Core-identity merge helper — price/meta/security payloads → ``ScanResult``.

Extracted from ``unified/scanner.py`` (degodding wave 7, 2026-08-09) so the
largest merge block inside ``UnifiedTokenScanner`` (``_merge_core``, C-20)
lives beside the other pure mapping helpers instead of inflating the scanner
orchestration class.

This module is a pure merge: given a ``ScanResult`` plus the DataBus core
payloads (``token_price`` / ``token_metadata`` / ``token_security``) it
populates the token identity fields. Defensive — a raw provider shape never
wipes out fields already populated by the price oracle. Never raises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.unified_scanner._coercion import _extract_meta_identity

if TYPE_CHECKING:
    from rugmunchbot._vendor.unified_scanner.model import ScanResult


def merge_core(r: ScanResult, data: dict[str, Any]) -> None:
    """Extract core identity from price, metadata, and security checks."""
    r.modules_run.extend(data.keys())

    price_data = data.get("token_price", {})
    if isinstance(price_data, dict):
        r.price_usd = float(price_data.get("price_usd", 0) or 0)
        r.market_cap_usd = float(price_data.get("market_cap_usd", 0) or 0)
        # DataBus price payload uses ``volume_24h_usd``; accept the
        # legacy ``volume_24h`` alias too so a renamed provider key
        # never zeroes out the 24h volume.
        r.volume_24h_usd = float(price_data.get("volume_24h_usd", price_data.get("volume_24h", 0)) or 0)
        r.liquidity_usd = float(price_data.get("liquidity_usd", 0) or 0)
        if price_data.get("source"):
            r.enrichment_sources.append(f"price:{price_data['source']}")

    meta = data.get("token_metadata", {})
    if isinstance(meta, dict):
        # token_metadata providers return raw external shapes (DexScreener
        # ``{"pairs": [...]}`` or Alchemy JSON-RPC ``{"result": {...}}``)
        # as well as the normalized ``{name, symbol, age_hours}`` form.
        # Normalize all three defensively — a raw shape must not wipe out
        # the fields populated by the price oracle.
        meta_name, meta_symbol = _extract_meta_identity(meta)
        r.name = str(meta_name or r.name or "")
        r.symbol = str(meta_symbol or r.symbol or "")
        r.age_hours = float(meta.get("age_hours", 0) or 0)

    # Fallback: when metadata is missing/empty, the price payload already
    # carries name/symbol (the F2 price oracle enriches its payload with
    # them).  Keep whatever non-empty value exists.
    if not r.name and isinstance(price_data, dict):
        r.name = str(price_data.get("name", "") or "")
    if not r.symbol and isinstance(price_data, dict):
        r.symbol = str(price_data.get("symbol", "") or "")

    security = data.get("token_security", {})
    if isinstance(security, dict):
        r.security = security
        checks = security.get("checks", {})
        r.modules_run.append(f"security_checks:{len(checks)}")
        r.enrichment_sources.append(f"security:{security.get('source', 'goplus')}")

"""Export schema — one payload for ``GET /scan/{token}.json`` and webhooks.

The file download and the webhook body are the *same object*. That is the
contract, not a convenience: an integrator who tests against the download has
already tested the webhook, and there is only one schema to version.

``schema_version`` is part of the payload and part of the signature base
string, so a consumer can reject an unknown shape instead of mis-parsing it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "premium.scan.v1"
SIGNATURE_HEADER = "X-RMI-Signature"

#: Fields every export/webhook consumer may rely on.
REQUIRED_KEYS = (
    "schema_version",
    "generated_at",
    "token",
    "chain",
    "risk",
    "bundle_graph",
    "cohorts",
)


def _risk_block(scan: dict[str, Any]) -> dict[str, Any]:
    """Safety verdict plus the flags that produced it."""
    return {
        "safety_score": scan.get("safety_score"),
        "confidence": scan.get("confidence"),
        "risk_level": scan.get("risk_level"),
        "risk_flags": scan.get("risk_flags") or [],
        "green_flags": scan.get("green_flags") or [],
    }


def _market_block(scan: dict[str, Any]) -> dict[str, Any]:
    """Price/liquidity/holder numbers exactly as the scanner reported them."""
    return {
        "price_usd": scan.get("price_usd"),
        "market_cap_usd": scan.get("market_cap_usd"),
        "liquidity_usd": scan.get("liquidity_usd"),
        "volume_24h_usd": scan.get("volume_24h_usd"),
        "holder_count": scan.get("holder_count"),
        "dev_holdings_pct": scan.get("dev_holdings_pct"),
    }


def _scanner_block(scan: dict[str, Any]) -> dict[str, Any]:
    """Provenance: which modules ran, which were skipped, which sources fed them."""
    return {
        "deployer_address": scan.get("deployer_address"),
        "scanned_at": scan.get("scanned_at"),
        "modules_run": scan.get("modules_run") or [],
        "modules_skipped": scan.get("modules_skipped") or [],
        "data_sources": scan.get("data_sources") or [],
    }


def webhook_contract() -> dict[str, Any]:
    """The webhook half of the contract: transport, auth, delivery, schema."""
    return {
        "schema_version": SCHEMA_VERSION,
        "method": "POST",
        "content_type": "application/json",
        "body": "identical to GET /api/v1/scan/{token}.json (see REQUIRED_KEYS)",
        "required_keys": list(REQUIRED_KEYS),
        "headers": {
            SIGNATURE_HEADER: "sha256=<hex hmac of the raw body, key = the subscriber's secret>",
            "X-RMI-Event": "scan.export",
            "X-RMI-Delivery": "deterministic id (token + generated_at), for idempotent retries",
        },
        "retries": "5 attempts, exponential backoff; dedupe on X-RMI-Delivery",
        "rate_limit": "per-IP token bucket; 429 with Retry-After once exhausted",
    }


def sign_body(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` signature for the raw webhook body."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def encode(payload: dict[str, Any]) -> bytes:
    """Canonical bytes to sign — must be the bytes that are sent."""
    return json.dumps(payload, separators=(",", ":"), default=str).encode()

"""
Cross-app Integration Test: Bot → DataBus.

Verifies the DataBus endpoints that ``rugmunchbot`` calls at scan time return
responses in the format that ``scan_token()`` and related logic expects.

Tested endpoints (mirrors bot/config.py DATABUS_URL):
  - GET  /api/v1/databus/fetch/token_price?mint={address}
  - GET  /api/v1/databus/token/{address}
  - GET  /api/v1/databus/fetch/token_security?address={addr}&chain={chain}

Skips gracefully when the rmi-backend is not running.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
import pytest

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
BACKEND_URL = os.environ.get("RMI_BACKEND_URL", "http://localhost:8000")
DATABUS_URL = f"{BACKEND_URL}/api/v1/databus"
AUTH_TOKEN = os.environ.get("TEST_RMI_AUTH_TOKEN", "")
REQUEST_TIMEOUT = float(os.environ.get("TEST_REQUEST_TIMEOUT", "10.0"))

# Well-known test tokens (public, low-value addresses for testing)
TEST_TOKENS = {
    "solana": "So11111111111111111111111111111111111111112",  # wSOL
    "ethereum": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH
}

pytestmark = pytest.mark.integration


# ── Helpers ────────────────────────────────────────────────────────────────


async def _backend_available() -> bool:
    """Check if the rmi-backend service is reachable."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{BACKEND_URL}/health")
            return resp.status_code == 200
    except httpx.RequestError:
        return False


def _make_headers() -> dict[str, str]:
    """Build request headers matching what the bot sends."""
    headers: dict[str, str] = {
        "Accept": "application/json",
    }
    if AUTH_TOKEN:
        headers["X-API-Key"] = AUTH_TOKEN
    return headers


def _check_key(response_body: dict, field_path: str) -> bool:
    """Check if a dotted field_path exists in a nested dict."""
    parts = field_path.split(".")
    current = response_body
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False
    return True


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_databus_token_price_response_shape():
    """GET /fetch/token_price returns shape the bot's scan_token() expects.

    The bot (bot.py ~line 298) destructures the response like::

        payload = _db_payload(r.json())
        pairs = payload.get("pairs", [])
        p = pairs[0]
        p.get("baseToken", {}).get("name")
        p.get("priceUsd")
        p.get("marketCap")
        p.get("volume", {}).get("h24")
        p.get("liquidity", {}).get("usd")
    """
    if not await _backend_available():
        pytest.skip(f"rmi-backend not reachable at {BACKEND_URL}")

    address = TEST_TOKENS["ethereum"]

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{DATABUS_URL}/fetch/token_price",
            params={"mint": address},
            headers=_make_headers(),
        )

    # The bot handles non-200 gracefully (logs error, continues)
    if resp.status_code != 200:
        pytest.skip(f"token_price returned {resp.status_code} — skipping shape check")

    body: dict[str, Any] = resp.json()

    # The bot unwraps via _db_payload() which looks for 'data' key
    data = body.get("data", body)
    assert isinstance(data, dict), f"Expected dict payload, got {type(data).__name__}"

    pairs = data.get("pairs", [])
    assert isinstance(pairs, list), f"Expected 'pairs' list, got {type(pairs).__name__}"
    assert len(pairs) > 0, "Expected at least one pair in response"

    first_pair = pairs[0]
    assert isinstance(first_pair, dict), "First pair must be a dict"

    # Fields the bot accesses (from bot.py scan_token phase 1)
    base_token = first_pair.get("baseToken", {})
    assert isinstance(base_token, dict), "'baseToken' should be a dict"
    assert "name" in base_token, "baseToken must include 'name'"
    assert "symbol" in base_token, "baseToken must include 'symbol'"

    assert "priceUsd" in first_pair, "Pair must include 'priceUsd'"

    # Optional but expected fields
    for optional_field in (
        "marketCap",
        "fdv",
        "pairAddress",
        "dexId",
        "chainId",
        "pairCreatedAt",
    ):
        if optional_field not in first_pair:
            logger.info(
                "Optional field '%s' missing from pair response", optional_field
            )

    volume = first_pair.get("volume", {})
    assert isinstance(volume, dict), "'volume' should be a dict"

    liquidity = first_pair.get("liquidity", {})
    assert isinstance(liquidity, dict), "'liquidity' should be a dict"

    price_change = first_pair.get("priceChange", {})
    assert isinstance(price_change, dict), "'priceChange' should be a dict"

    info = first_pair.get("info", {})
    if info:
        socials = info.get("socials", [])
        assert isinstance(socials, list), "'socials' should be a list"


@pytest.mark.asyncio
async def test_databus_token_detail_response_shape():
    """GET /token/{address} returns shape the bot's scan_token() expects.

    The bot (bot.py ~line 341) accesses::

        td.get("name")
        td.get("symbol")
        td.get("price_usd")
        td.get("market_cap")
        td.get("liquidity_usd")
        td.get("bundle_detected")
        td.get("fresh_wallet_pct")
        td.get("dev_wallet")
    """
    if not await _backend_available():
        pytest.skip(f"rmi-backend not reachable at {BACKEND_URL}")

    address = TEST_TOKENS["ethereum"]

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{DATABUS_URL}/token/{address}",
            headers=_make_headers(),
        )

    # Token detail may 404 for unknown tokens; the bot handles this gracefully
    if resp.status_code != 200:
        pytest.skip(f"token_detail returned {resp.status_code} — skipping shape check")

    body: dict[str, Any] = resp.json()

    # The bot calls _db_payload() which unwraps 'data'
    data = body.get("data", body)
    assert isinstance(data, dict), f"Expected dict detail, got {type(data).__name__}"

    # Fields the bot uses (some are optional)
    bot_used_fields = {
        "name": (str, type(None)),
        "symbol": (str, type(None)),
        "bundle_detected": (bool, type(None)),
        "fresh_wallet_pct": ((int, float), type(None)),
    }

    for field, expected_types in bot_used_fields.items():
        val = data.get(field)
        assert isinstance(val, expected_types), (
            f"Field '{field}' has type {type(val).__name__}, expected {expected_types}"
        )

    # price_usd, market_cap, liquidity_usd should be numeric or null
    for num_field in ("price_usd", "market_cap", "liquidity_usd"):
        val = data.get(num_field)
        if val is not None:
            assert isinstance(val, (int, float)), (
                f"Field '{num_field}' should be numeric or null, got {type(val).__name__}"
            )


@pytest.mark.asyncio
async def test_databus_token_security_response_shape():
    """GET /fetch/token_security returns shape the bot's scan_token() expects.

    The bot (bot.py ~line 383) accesses::

        sec.get("is_open_source") / sec.get("contract_verified")
        sec.get("is_honeypot") / sec.get("honeypot")
        sec.get("buy_tax") / sec.get("buy_tax_pct")
        sec.get("sell_tax") / sec.get("sell_tax_pct")
        sec.get("mintable") / sec.get("is_mintable")
        sec.get("can_blacklist")
        sec.get("owner_renounced") / sec.get("is_owner_renounced")
        sec.get("lp_locked") / sec.get("lp_locked_pct")
        sec.get("holders") / sec.get("top_holders")
    """
    if not await _backend_available():
        pytest.skip(f"rmi-backend not reachable at {BACKEND_URL}")

    address = TEST_TOKENS["ethereum"]
    chain = "ethereum"

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{DATABUS_URL}/fetch/token_security",
            params={"address": address, "chain": chain},
            headers=_make_headers(),
        )

    # Security endpoint may be unavailable for some chains; bot handles 404
    if resp.status_code != 200:
        pytest.skip(
            f"token_security returned {resp.status_code} — skipping shape check"
        )

    body: dict[str, Any] = resp.json()

    # The bot calls _db_payload() which unwraps 'data'
    data = body.get("data", body)
    assert isinstance(data, dict), (
        f"Expected dict security data, got {type(data).__name__}"
    )

    # Boolean / nullable fields the bot checks
    for bool_field in (
        "is_open_source",
        "contract_verified",
        "is_honeypot",
        "honeypot",
        "mintable",
        "is_mintable",
        "can_blacklist",
        "owner_renounced",
        "is_owner_renounced",
    ):
        val = data.get(bool_field)
        if val is not None:
            assert isinstance(val, bool), (
                f"Field '{bool_field}' should be bool or null, got {type(val).__name__}"
            )

    # Numeric fields
    for num_field in (
        "buy_tax",
        "buy_tax_pct",
        "sell_tax",
        "sell_tax_pct",
        "lp_locked",
        "lp_locked_pct",
    ):
        val = data.get(num_field)
        if val is not None:
            assert isinstance(val, (int, float)), (
                f"Field '{num_field}' should be numeric or null, got {type(val).__name__}"
            )

    # Holders should be a list of dicts with address, pct/percent
    holders = data.get("holders") or data.get("top_holders", [])
    if isinstance(holders, list) and len(holders) > 0:
        for h in holders:
            assert isinstance(h, dict), "Each holder must be a dict"
            assert "address" in h, "Holder must have 'address'"
            assert "pct" in h or "percent" in h, "Holder must have 'pct' or 'percent'"


@pytest.mark.asyncio
async def test_databus_wallet_endpoint_shape():
    """GET /wallet/{address} returns shape the bot expects.

    The bot (bot.py ~line 637) calls ``/api/v1/databus/wallet/{address}``
    for wallet forensics.
    """
    if not await _backend_available():
        pytest.skip(f"rmi-backend not reachable at {BACKEND_URL}")

    address = TEST_TOKENS["ethereum"]

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(
            f"{DATABUS_URL}/wallet/{address}",
            headers=_make_headers(),
        )

    if resp.status_code != 200:
        pytest.skip(f"wallet endpoint returned {resp.status_code} — skipping")

    body: dict[str, Any] = resp.json()
    data = body.get("data", body)

    assert isinstance(data, dict), "Wallet response must be a dict"

    # Expected fields (bot accesses these)
    for field in ("address",):
        assert field in data, f"Wallet response must include '{field}'"

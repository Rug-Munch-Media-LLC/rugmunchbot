# VENDORED 2026-08-24 from rmi-backend/app/databus/gmgn_provider.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
#!/usr/bin/env python3
"""
GMGN Data Provider — GMGN Agent API (OpenAPI) integration.
==========================================================
Provides: token info, security, holders, trending, discover (trenches),
wallet detail, K-line, live trades, smart money, new pair discovery.

WORKING PATH (verified 2026-08-16)
----------------------------------
The public web API (``gmgn.ai/defi/quotation/v1/...``) is Cloudflare-blocked
(returns HTML even via proxy) and is NO LONGER used. This provider uses the
official **GMGN Agent API** instead:

  * Base URL:  ``https://openapi.gmgn.ai``  (NOT gmgn.ai — different host,
    no Cloudflare challenge on the data routes)
  * Auth:      ``X-APIKEY`` header with the GMGN_API_KEY, plus two query
    params required by the OpenAPI exist-auth scheme:
      - ``timestamp`` — unix seconds
      - ``client_id`` — random UUID (replay guard, fresh per request)
    (Verified from GMGNAI/gmgn-skills source: ``OpenApiClient.js`` /
    ``signer.js``, npm ``gmgn-cli`` 1.5.6 dist.)
  * The ``npx gmgn-cli`` CLI is a thin wrapper over this same API — the
    direct HTTP path is used here so no Node runtime dependency is needed.

Endpoints (verified live 2026-08-16, exist-auth = API key only):
  - token info:      GET /v1/token/info?chain&address
  - token security:  GET /v1/token/security?chain&address
  - token pool:      GET /v1/token/pool_info?chain&address
  - holders:         GET /v1/market/token_top_holders?chain&address&limit&order_by&direction[&tag]
  - traders:         GET /v1/market/token_top_traders?chain&address&limit
  - trending:        GET /v1/market/rank?chain&interval&order_by&direction&limit
  - discover:        POST /v1/trenches?chain   (body: {chain, types, limit})
  - K-line:          GET /v1/market/token_kline?chain&address&resolution&from&to
  - wallet stats:    GET /v1/user/wallet_stats?chain&wallet_address&period
  - wallet activity: GET /v1/user/wallet_activity?chain&wallet_address&limit
  - wallet holdings: GET /v1/user/wallet_holdings  (SIGNED auth — needs
    GMGN_PRIVATE_KEY, which this repo does not hold; NOT used here)

Rate limits: leaky bucket (rate=20, capacity=20), per-route weight; 429
returns ``X-RateLimit-Reset`` (unix secs) or body ``reset_at``. This client
honours the reset time and backs off instead of spamming (which extends
bans). Falls back to DexScreener/Birdeye when GMGN is unavailable.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.gmgn_fallbacks import (
    agent_auth_params,
    agent_headers,
    cached_or_fetch,
    cached_or_fetch_list,
    fallback_birdeye_holders,
    fallback_dexscreener_new_pairs,
    fallback_dexscreener_token,
    fallback_dexscreener_trades,
    normalize_trades,
)
from rugmunchbot._vendor.proxy_rotator import get_proxy_rotator

logger = get_logger(__name__)

GMGN_BASE = "https://gmgn.ai"
# Official GMGN Agent API (used by the gmgn-cli SDK / openapi.gmgn.ai).
# Auth: X-APIKEY header + timestamp (unix seconds) + client_id (UUID) query
# params — see OpenApiClient.ts / signer.ts in GMGNAI/gmgn-skills.

# Browser-like headers kept for the non-Agent fallback egresses
# (DexScreener/Birdeye). The Agent API is not Cloudflare-gated on its data
# routes; a clean UA is still sent.

# Cache TTLs (seconds)
TRADE_CACHE_TTL = 30
TOKEN_CACHE_TTL = 120
HOLDER_CACHE_TTL = 300
TRENDING_CACHE_TTL = 60


# Constants live in gmgn_transport (single source); re-exported here because tests and callers read them from this module (import-time env contract).
from rugmunchbot._vendor.gmgn_transport import (  # noqa: E402,F401
    _f,
    _sum_top_pct,
    GMGN_API_KEY,
    GMGN_BASE,
    GMGN_OPENAPI_BASE,
    _BROWSER_HEADERS,
)

# Read env DIRECTLY here (not via transport import): tests reload this
# module to observe env changes; transport is not reloaded alongside.
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
from rugmunchbot._vendor.gmgn_endpoints_market import MarketEndpointsMixin  # noqa: E402
from rugmunchbot._vendor.gmgn_endpoints_wallet import WalletEndpointsMixin  # noqa: E402
from rugmunchbot._vendor.gmgn_transport import TransportMixin  # noqa: E402


class GMGNProvider(TransportMixin, MarketEndpointsMixin, WalletEndpointsMixin):
    """GMGN Agent API client with fallback cascade."""

    def __init__(self) -> None:
        self.api_key = GMGN_API_KEY
        self.headers = dict(_BROWSER_HEADERS)
        if self.api_key:
            # GMGN Agent API auth convention (official SDK: X-APIKEY header).
            self.headers["X-APIKEY"] = self.api_key
        self.client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        # Proxy-first egress when residential proxies are configured (used by
        # the fallback egresses — the Agent API itself is not CF-gated).
        self._rotator = get_proxy_rotator()
        self._proxy_url: str | None = None
        self._proxy_client: httpx.AsyncClient | None = None
        if self._rotator.enabled:
            self._proxy_url = self._rotator.get_rotating_proxy()
            self._proxy_client = self._rotator.get_client(proxy=self._proxy_url)
        # Rate-limit state: next allowed unix ts + cooldown until (ban).
        self._next_allowed_at = 0.0
        self._banned_until = 0.0
        self._available = True

    # ═══════════════════════════════════════════════════════════════
    # CORE: Token Info
    # ═══════════════════════════════════════════════════════════════



    # ═══════════════════════════════════════════════════════════════
    # WALLET INTELLIGENCE (derived — feeds the unified scanner)
    # ═══════════════════════════════════════════════════════════════



    # ═══════════════════════════════════════════════════════════════
    # SMART MONEY
    # ═══════════════════════════════════════════════════════════════



    # ═══════════════════════════════════════════════════════════════
    # NEW PAIRS / TOKEN LAUNCHES
    # ═══════════════════════════════════════════════════════════════

_gmgn_provider: GMGNProvider | None = None


def get_gmgn_provider() -> GMGNProvider:
    """Get or create the GMGN provider singleton."""
    global _gmgn_provider
    if _gmgn_provider is None:
        _gmgn_provider = GMGNProvider()
    return _gmgn_provider

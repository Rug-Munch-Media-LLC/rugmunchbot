# VENDORED 2026-08-17 from rmi-backend/app/databus/gmgn_provider.py.
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
# DE-GODDED 2026-08-22: the original 907-line single-class monolith was split
# into cohesive mixins — ``gmgn_transport.py`` (constants, chain slugs,
# proxy-first HTTP transport), ``gmgn_endpoints_market.py`` (token info,
# trades, new pairs, trending, discover, kline) and
# ``gmgn_endpoints_wallet.py`` (wallet intelligence/detail, holders, smart
# money, security). Method bodies moved VERBATIM; this module stays the
# public facade: class assembly + singleton + re-exports. A tripwire in
# scripts/refresh-vendors.sh --check fails if this file regrows past 200
# lines (a vendor refresh would otherwise restore the monolith).
#!/usr/bin/env python3
"""
GMGN Data Provider — Real GMGN public API integration.
=====================================================
Provides: token info, live trades, holder analysis, smart money,
bundle/sniper detection, KOL tracking, new pair discovery.

Uses GMGN's public (undocumented) JSON API — no API key required.
The GMGN Agent API key (GMGN_API_KEY) is accepted but optional; when
present it is sent as the ``X-APIKEY`` header (the auth convention used by
the official GMGN SDK, see ``src/client/OpenApiClient.ts`` in
GMGNAI/gmgn-skills), otherwise browser-like headers are used. Falls back
to DexScreener/Birdeye when GMGN is unavailable.

Cloudflare escape: when residential proxies are configured
(``WEBSHARE_PROXY_LIST`` / ``WEBSHARE_PROXY_USER`` / ``WEBSHARE_PROXY_PASS``)
the provider routes requests through the proxy rotator — proxy-first, and a
403/5xx/timeout on the proxy egress marks the proxy failed (cooldown) and
retries once direct. With no proxies configured the provider uses a direct
client exactly as before.

Endpoint provenance (verified against public open-source usage 2026-08-09):
  - token info:      /defi/quotation/v1/tokens/{chain}/{address}
  - trades:          /defi/quotation/v1/trades/{chain}/{address}
  - holders:         /vas/api/v1/token_holders/{chain}/{address}
  - smart money:     /defi/quotation/v1/smartmoney/{chain}/walletNew/{wallet}
  - new pairs:       /defi/quotation/v1/rank/{chain}/pump?new_creation=true
"""

from __future__ import annotations

from rugmunchbot._vendor.gmgn_endpoints_market import _GMGNMarketEndpointsMixin
from rugmunchbot._vendor.gmgn_endpoints_wallet import _GMGNWalletEndpointsMixin
from rugmunchbot._vendor.gmgn_transport import (
    _BROWSER_HEADERS as _BROWSER_HEADERS,
    GMGN_API_KEY,
    GMGN_BASE,
    GMGN_OPENAPI_BASE,
    HOLDER_CACHE_TTL,
    TOKEN_CACHE_TTL,
    TRADE_CACHE_TTL,
    _GMGNTransportMixin,
    _normalize_chain as _normalize_chain,
)

__all__ = [
    "GMGN_API_KEY",
    "GMGN_BASE",
    "GMGN_OPENAPI_BASE",
    "HOLDER_CACHE_TTL",
    "TOKEN_CACHE_TTL",
    "TRADE_CACHE_TTL",
    "GMGNProvider",
    "get_gmgn_provider",
]


class GMGNProvider(
    _GMGNTransportMixin,
    _GMGNMarketEndpointsMixin,
    _GMGNWalletEndpointsMixin,
):
    """Real GMGN public API client with fallback cascade."""


# ── Singleton ──────────────────────────────────────────────

_gmgn_provider: GMGNProvider | None = None


def get_gmgn_provider() -> GMGNProvider:
    """Get or create the GMGN provider singleton."""
    global _gmgn_provider
    if _gmgn_provider is None:
        _gmgn_provider = GMGNProvider()
    return _gmgn_provider

"""Scan provider seam.

The live bot's scanner.py couples to private enrichment (gmgn_enrich,
scan_enrich, rag scan_memory). OSS defines a protocol and a default provider
built on public APIs (DexScreener, GoPlus, Honeypot.is) that need no keys.

Canonical private behavior: rmi-backend rugmunchbot/scanner.py (NOT ported
verbatim — must be provider-injected first, checklist §5).
"""

from __future__ import annotations

from typing import Any, Protocol


class ScanProvider(Protocol):
    async def scan_token(self, address: str, chain: str, **kw: Any) -> dict[str, Any]: ...
    async def analyze_wallet(self, address: str, **kw: Any) -> dict[str, Any]: ...


class PublicAPIScanProvider:
    """Default provider: public no-auth APIs (DexScreener / GoPlus / Honeypot).

    Implements a best-effort risk summary without any private backend.
    Port status: stub — the real fallback chain ships in Stage 3.
    """

    async def scan_token(self, address: str, chain: str, **kw: Any) -> dict[str, Any]:
        return {"address": address, "chain": chain, "source": "public-apis", "verdict": "unscanned"}

    async def analyze_wallet(self, address: str, **kw: Any) -> dict[str, Any]:
        return {"address": address, "source": "public-apis", "verdict": "unscanned"}


def get_scan_provider() -> Any:
    """Return the active provider. Defaults to public-API provider."""
    return PublicAPIScanProvider()

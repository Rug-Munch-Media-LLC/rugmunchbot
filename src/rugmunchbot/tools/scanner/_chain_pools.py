"""Bot-owned RPC endpoint-pool extensions + Lite Chain Indexer fallback.

The vendored ``rpc_router`` is frozen (refresh-vendors.sh would clobber
edits), so this module extends its endpoint pools AT RUNTIME instead:

* ``install_pool_extensions()`` wraps ``RPCRouter._get_endpoints`` and
  appends curated extra free public endpoints per chain (dedup-safe).
  Thin chains gain depth; no vendor file is touched.
* ``lci_deployer_history()`` queries the Lite Chain Indexer's ClickHouse
  store (via the vendored ``lite_chain_indexer`` module) so deployer
  reputation survives even when every live RPC endpoint is down — the
  indexer keeps historical deployer_events indexed from its own 62-endpoint
  free-RPC pool.

Call :func:`install_pool_extensions` once at import time of the resolver
module; it is idempotent and fail-open (a router upgrade that renames the
method simply disables the extension, never breaks scanning).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.scanner.chain_pools")

# Curated ADDITIONAL free public endpoints (no key required). These extend
# the frozen router pools at runtime; duplicates are filtered out, and any
# endpoint that proves unreliable is handled by the router's own health
# tracking (consecutive failures → cooldown → skipped).
EXTRA_ENDPOINTS: dict[str, tuple[str, ...]] = {
    # Solana: OnFinality's public gateway adds depth beyond the router's six.
    "solana": ("https://solana.api.onfinality.io/public",),
    # Ethereum: Cloudflare classic gateway + Merkle + MEV Blocker public.
    "ethereum": (
        "https://cloudflare-eth.com",
        "https://eth.merkle.io",
        "https://rpc.mevblocker.io",
    ),
    # BSC: Binance's official dataseed cluster (three independent hosts).
    "bsc": (
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-dataseed1.ninicoin.io",
    ),
    "polygon": (
        "https://polygon-bor-rpc.publicnode.com",
        "https://1rpc.io/matic",
    ),
    "arbitrum": (
        "https://arb1.arbitrum.io/rpc",  # Offchain Labs official
        "https://1rpc.io/arb",
    ),
    "base": (
        "https://mainnet.base.org",  # official
        "https://1rpc.io/base",
    ),
    "optimism": (
        "https://mainnet.optimism.io",  # official
        "https://1rpc.io/op",
    ),
    "avalanche": (
        "https://api.avax.network/ext/bc/C/rpc",  # official
    ),
    # Thin pools in the frozen router (≤8 endpoints) — add official gates.
    "fantom": ("https://1rpc.io/ftm",),
    "linea": (
        "https://rpc.linea.build",  # Consensys official
    ),
    "zksync": (
        "https://mainnet.era.zksync.io",  # Matter Labs official
    ),
    "scroll": (
        "https://rpc.scroll.io",  # official
    ),
    "gnosis": (
        "https://rpc.gnosischain.com",  # official
        "https://1rpc.io/gnosis",
    ),
    "mantle": (
        "https://rpc.mantle.xyz",  # official
    ),
}

_installed = False


def install_pool_extensions() -> bool:
    """Append EXTRA_ENDPOINTS to the frozen router's pools (idempotent).

    Wraps ``RPCRouter._get_endpoints`` — the router keeps its health
    tracking, cooldowns and rotation semantics; it simply sees more URLs.
    Returns True when the extension is active.
    """
    global _installed
    if _installed:
        return True
    try:
        from rugmunchbot._vendor.scanner_engines.rpc_router import RPCRouter
    except ImportError:
        logger.debug("rpc_router unavailable; pool extension not installed")
        return False

    original = RPCRouter._get_endpoints
    if getattr(original, "_pool_extended", False):
        _installed = True
        return True

    def extended(self: Any, chain: str) -> list[str]:
        urls = list(original(self, chain))
        extras = EXTRA_ENDPOINTS.get(chain)
        if extras:
            for raw in extras:
                url = self._resolve_url(raw)
                if url not in urls:
                    urls.append(url)
        return urls

    extended._pool_extended = True  # type: ignore[attr-defined]
    RPCRouter._get_endpoints = extended  # type: ignore[method-assign]
    _installed = True
    total = sum(len(v) for v in EXTRA_ENDPOINTS.values())
    logger.info(
        "rpc_pool_extension_installed", extra_chains=len(EXTRA_ENDPOINTS), extra_endpoints=total
    )
    return True


async def lci_deployer_history(address: str, chain: str) -> dict[str, Any] | None:
    """Deployer history from the Lite Chain Indexer's ClickHouse store.

    Data-fallback layer: LCI continuously indexes deployer_events from its
    own free-RPC pool, so this works even when every live endpoint the bot
    can reach is down/rate-limited. Fail-open — returns None when ClickHouse
    or the vendored module is unavailable.
    """
    if not address:
        return None
    try:
        from rugmunchbot._vendor.lite_chain_indexer import (
            _lite_chain_indexer_deployer_history,
        )

        result = await _lite_chain_indexer_deployer_history(address=address, chain=chain, limit=25)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        logger.debug("lci_deployer_history failed: %s", exc)
        return None


def lci_deployer_summary(history: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce raw LCI deployer events to the fields the report needs."""
    if not isinstance(history, dict):
        return None
    events = history.get("events") or history.get("deployments") or []
    if not isinstance(events, list):
        events = []
    tokens = len(events)
    chains = sorted({str(e.get("chain")) for e in events if isinstance(e, dict) and e.get("chain")})
    if not tokens:
        return None
    return {
        "tokens_deployed": tokens,
        "chains": chains[:5],
        "source": "lite_chain_indexer",
    }


__all__ = [
    "EXTRA_ENDPOINTS",
    "install_pool_extensions",
    "lci_deployer_history",
    "lci_deployer_summary",
]

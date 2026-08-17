# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/rpc_router.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Unified RPC Router — Multi-Endpoint Fallback with Health Checks
===============================================================
Every RPC call routes through this module. No file hardcodes its own URLs.
If one endpoint is down/rate-limited/slow, we skip to the next automatically.

Architecture:
  rpc_router.py          ← YOU ARE HERE — unified endpoint registry + fallback
      └─ chain_registry.py  ← canonical chain configs reference RPC lists
      └─ data_fallback.py   ← fallback chain engine, references router health
      └─ _core.py           ← scanner helpers, delegates RPC calls here
      └─ consensus_rpc.py   ← multi-provider consensus voting, references here

Pattern for callers:
    router = get_rpc_router()
    result = await router.call("ethereum", "eth_blockNumber", [])
    # → tries 10+ endpoints, returns first success, or None
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════
# COMPREHENSIVE PUBLIC RPC ENDPOINT REGISTRY
# ══════════════════════════════════════════════════════════════════════
# Each chain has 8-15 endpoints. Order: fastest/reliable first,
# then more experimental/community endpoints. Health-checked at runtime.
#
# Sources:
#   • https://chainlist.org  — canonical chain + RPC list
#   • https://llamarpc.com   — llama nodes (fast, free)
#   • https://drpc.org       — distributed RPC (free tier)
#   • https://publicnode.com — free, no-auth public RPCs
#   • https://1rpc.io         — free tier, many chains
#   • https://meowrpc.com    — free tier
#   • https://blockpi.io     — BlockPI network free tier
#   • Proprietary (ankr, blastapi, cloudflare)
# ══════════════════════════════════════════════════════════════════════

# ─── EVM Chains ─────────────────────────────────────────────────────

EVM_RPC_ENDPOINTS: dict[str, list[str]] = {
    "ethereum": [
        "https://eth.llamarpc.com",  # LlamaNodes — fast, free, no key
        "https://rpc.ankr.com/eth",  # Ankr — free tier, reliable
        "https://eth.drpc.org",  # dRPC — distributed, free tier
        "https://ethereum-rpc.publicnode.com",  # PublicNode — free, no auth, 8 req/s
        "https://1rpc.io/eth",  # 1RPC — free tier, privacy-first
        "https://eth.meowrpc.com",  # MeowRPC — free tier
        "https://rpc.flashbots.net",  # Flashbots — private tx pool, public RPC
        "https://ethereum.blockpi.network/v1/rpc/public",  # BlockPI — free public tier
        "https://cloudflare-eth.com",  # Cloudflare — free, reliable
        "https://eth-mainnet.public.blastapi.io",  # BlastAPI — free public
        "https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy — free tier (key)
        "https://mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura — free tier (key)
        "https://virginia.rpc.blxrbdn.com",  # BloXroute — free tier
        "https://uk.rpc.blxrbdn.com",  # BloXroute — EU
        "https://singapore.rpc.blxrbdn.com",  # BloXroute — Asia
    ],
    "bsc": [
        "https://binance.llamarpc.com",  # LlamaNodes BSC
        "https://rpc.ankr.com/bsc",  # Ankr BSC
        "https://bsc.drpc.org",  # dRPC BSC
        "https://bsc-rpc.publicnode.com",  # PublicNode BSC
        "https://1rpc.io/bnb",  # 1RPC BSC
        "https://bsc.meowrpc.com",  # MeowRPC BSC
        "https://bsc.blockpi.network/v1/rpc/public",  # BlockPI BSC
        "https://bsc-dataseed.binance.org",  # Binance official dataseed
        "https://bsc-dataseed1.binance.org",  # Binance dataseed 1
        "https://bsc-dataseed2.binance.org",  # Binance dataseed 2
        "https://bsc-dataseed3.binance.org",  # Binance dataseed 3
        "https://bsc-dataseed1.defibit.io",  # Defibit dataseed
        "https://bsc-dataseed2.defibit.io",  # Defibit dataseed 2
        "https://bsc-dataseed1.ninicoin.io",  # Ninicoin dataseed
        "https://bsc-dataseed2.ninicoin.io",  # Ninicoin dataseed 2
        "https://bsc-mainnet.public.blastapi.io",  # BlastAPI BSC
    ],
    "polygon": [
        "https://polygon.llamarpc.com",  # LlamaNodes Polygon
        "https://rpc.ankr.com/polygon",  # Ankr Polygon
        "https://polygon.drpc.org",  # dRPC Polygon
        "https://polygon-rpc.com",  # Polygon official public
        "https://1rpc.io/matic",  # 1RPC Polygon
        "https://polygon.meowrpc.com",  # MeowRPC Polygon
        "https://polygon.blockpi.network/v1/rpc/public",  # BlockPI Polygon
        "https://polygon-bor-rpc.publicnode.com",  # PublicNode Polygon
        "https://polygon-mainnet.public.blastapi.io",  # BlastAPI Polygon
        "https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Polygon (key)
    ],
    "arbitrum": [
        "https://arbitrum.llamarpc.com",  # LlamaNodes Arbitrum
        "https://rpc.ankr.com/arbitrum",  # Ankr Arbitrum
        "https://arbitrum.drpc.org",  # dRPC Arbitrum
        "https://arbitrum-one-rpc.publicnode.com",  # PublicNode Arbitrum
        "https://1rpc.io/arb",  # 1RPC Arbitrum
        "https://arbitrum.meowrpc.com",  # MeowRPC Arbitrum
        "https://arbitrum.blockpi.network/v1/rpc/public",  # BlockPI Arbitrum
        "https://arb1.arbitrum.io/rpc",  # Offchain Labs official
        "https://arb-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Arbitrum (key)
    ],
    "base": [
        "https://base.llamarpc.com",  # LlamaNodes Base
        "https://rpc.ankr.com/base",  # Ankr Base
        "https://base.drpc.org",  # dRPC Base
        "https://base-rpc.publicnode.com",  # PublicNode Base
        "https://1rpc.io/base",  # 1RPC Base
        "https://base.meowrpc.com",  # MeowRPC Base
        "https://base.blockpi.network/v1/rpc/public",  # BlockPI Base
        "https://mainnet.base.org",  # Base official public
        "https://base-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Base (key)
    ],
    "optimism": [
        "https://optimism.llamarpc.com",  # LlamaNodes Optimism
        "https://rpc.ankr.com/optimism",  # Ankr Optimism
        "https://optimism.drpc.org",  # dRPC Optimism
        "https://optimism-rpc.publicnode.com",  # PublicNode Optimism
        "https://1rpc.io/op",  # 1RPC Optimism
        "https://optimism.meowrpc.com",  # MeowRPC Optimism
        "https://optimism.blockpi.network/v1/rpc/public",  # BlockPI Optimism
        "https://mainnet.optimism.io",  # Optimism official
        "https://opt-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Optimism (key)
    ],
    "avalanche": [
        "https://avalanche.llamarpc.com",  # LlamaNodes Avalanche
        "https://rpc.ankr.com/avalanche",  # Ankr Avalanche
        "https://avalanche.drpc.org",  # dRPC Avalanche
        "https://avalanche-c-chain-rpc.publicnode.com",  # PublicNode Avalanche
        "https://1rpc.io/avax/c",  # 1RPC Avalanche C-Chain
        "https://avalanche.meowrpc.com",  # MeowRPC Avalanche
        "https://avalanche.blockpi.network/v1/rpc/public",  # BlockPI Avalanche
        "https://api.avax.network/ext/bc/C/rpc",  # Avalanche official
        "https://avalanche-mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura (key)
    ],
    "fantom": [
        "https://rpc.ankr.com/fantom",  # Ankr Fantom
        "https://fantom.drpc.org",  # dRPC Fantom
        "https://fantom-rpc.publicnode.com",  # PublicNode Fantom
        "https://1rpc.io/ftm",  # 1RPC Fantom
        "https://fantom.meowrpc.com",  # MeowRPC Fantom
        "https://rpc.fantom.network",  # Fantom official public
        "https://fantom-mainnet.public.blastapi.io",  # BlastAPI Fantom
        "https://fantom.blockpi.network/v1/rpc/public",  # BlockPI Fantom
    ],
    "linea": [
        "https://rpc.linea.build",  # Linea official
        "https://linea-rpc.publicnode.com",  # PublicNode Linea
        "https://linea.drpc.org",  # dRPC Linea
        "https://1rpc.io/linea",  # 1RPC Linea
        "https://linea.blockpi.network/v1/rpc/public",  # BlockPI Linea
        "https://linea.meowrpc.com",  # MeowRPC Linea
        "https://linea-mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura (key)
    ],
    "zksync": [
        "https://mainnet.era.zksync.io",  # zkSync official
        "https://zksync-era.blockpi.network/v1/rpc/public",  # BlockPI zkSync
        "https://zksync.drpc.org",  # dRPC zkSync
        "https://1rpc.io/zksync-era",  # 1RPC zkSync Era
        "https://zksync.meowrpc.com",  # MeowRPC zkSync
        "https://zksync-era.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy zkSync (key)
    ],
    "scroll": [
        "https://rpc.scroll.io",  # Scroll official
        "https://scroll-rpc.publicnode.com",  # PublicNode Scroll
        "https://scroll.drpc.org",  # dRPC Scroll
        "https://1rpc.io/scroll",  # 1RPC Scroll
        "https://scroll.blockpi.network/v1/rpc/public",  # BlockPI Scroll
        "https://scroll-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Scroll (key)
    ],
    "mantle": [
        "https://rpc.mantle.xyz",  # Mantle official
        "https://mantle-rpc.publicnode.com",  # PublicNode Mantle
        "https://mantle.drpc.org",  # dRPC Mantle
        "https://1rpc.io/mantle",  # 1RPC Mantle
        "https://mantle.blockpi.network/v1/rpc/public",  # BlockPI Mantle
        "https://mantle-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Mantle (key)
    ],
    "gnosis": [
        "https://gnosis.drpc.org",  # dRPC Gnosis
        "https://rpc.gnosischain.com",  # Gnosis official
        "https://1rpc.io/gnosis",  # 1RPC Gnosis
        "https://gnosis-rpc.publicnode.com",  # PublicNode Gnosis
    ],
}

# ─── Solana ─────────────────────────────────────────────────────────

SOLANA_RPC_ENDPOINTS: list[str] = [
    "https://api.mainnet-beta.solana.com",  # Solana official public
    "https://solana-rpc.publicnode.com",  # PublicNode Solana
    "https://solana.drpc.org",  # dRPC Solana
    "https://rpc.ankr.com/solana",  # Ankr Solana
    "https://solana.meowrpc.com",  # MeowRPC Solana
    "https://solana.blockpi.network/v1/rpc/public",  # BlockPI Solana
    "https://solana-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Solana (key)
    "https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}",  # Helius (key, paid fallback)
]


# ══════════════════════════════════════════════════════════════════════
# ENDPOINT HEALTH TRACKING
# ══════════════════════════════════════════════════════════════════════


@dataclass
class EndpointHealth:
    """Health tracking for a single RPC endpoint."""

    url: str
    chain: str
    consecutive_failures: int = 0
    total_calls: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    last_check: float = 0.0
    is_dead: bool = False  # Marked dead after N consecutive failures
    dead_until: float = 0.0  # Unix timestamp when we can try again

    COOLDOWN_SECONDS: int = 60  # Try dead endpoints again after 60s
    MAX_CONSECUTIVE_FAILURES: int = 3  # Mark dead after 3 consecutive failures

    @property
    def alive(self) -> bool:
        """Is this endpoint worth trying right now?"""
        if not self.is_dead:
            return True
        return time.time() > self.dead_until

    def record_success(self, latency_ms: float) -> None:
        self.consecutive_failures = 0
        self.total_calls += 1
        self.avg_latency_ms = (self.avg_latency_ms * (self.total_calls - 1) + latency_ms) / self.total_calls
        self.last_check = time.time()
        self.is_dead = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.total_calls += 1
        self.total_failures += 1
        self.last_check = time.time()
        if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES and not self.is_dead:
            self.is_dead = True
            self.dead_until = time.time() + self.COOLDOWN_SECONDS
            logger.warning(
                "rpc_endpoint_dead",
                url=self.url,
                consecutive_failures=self.consecutive_failures,
                cooldown_s=self.COOLDOWN_SECONDS,
            )

    def reset(self) -> None:
        """Reset health (e.g. after endpoint recovers)."""
        self.consecutive_failures = 0
        self.total_calls = 0
        self.total_failures = 0
        self.avg_latency_ms = 0.0
        self.is_dead = False
        self.dead_until = 0.0


# ══════════════════════════════════════════════════════════════════════
# RPC ROUTER
# ══════════════════════════════════════════════════════════════════════


class RpcRouter:
    """
    Unified RPC router with multi-endpoint fallback + health checks.

    Usage:
        router = get_rpc_router()
        # EVM: get latest block
        result = await router.call("ethereum", "eth_blockNumber", [])
        # Solana: get balance
        result = await router.call("solana", "getBalance", ["address..."])

    Health checks run automatically before each call — dead endpoints
    are skipped until their cooldown expires.
    """

    def __init__(self) -> None:
        # Initialize endpoint health trackers
        self._evm_health: dict[str, dict[str, EndpointHealth]] = defaultdict(dict)
        self._solana_health: dict[str, EndpointHealth] = {}

        # Shared httpx client (connection pooling)
        self._client: httpx.AsyncClient | None = None

        # Performance tuning
        self._call_timeout: float = 10.0  # Per-endpoint timeout
        self._health_check_payloads: dict[str, tuple[str, list[Any]]] = {
            # EVM: eth_blockNumber
            "ethereum": ("eth_blockNumber", []),
            "bsc": ("eth_blockNumber", []),
            "polygon": ("eth_blockNumber", []),
            "arbitrum": ("eth_blockNumber", []),
            "base": ("eth_blockNumber", []),
            "optimism": ("eth_blockNumber", []),
            "avalanche": ("eth_blockNumber", []),
            "fantom": ("eth_blockNumber", []),
            "linea": ("eth_blockNumber", []),
            "zksync": ("eth_blockNumber", []),
            "scroll": ("eth_blockNumber", []),
            "mantle": ("eth_blockNumber", []),
            "gnosis": ("eth_blockNumber", []),
            # Solana: getHealth
            "solana": ("getHealth", []),
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=200,
                max_keepalive_connections=50,
                keepalive_expiry=30,
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._call_timeout),
                limits=limits,
            )
        return self._client

    def _resolve_url(self, url: str) -> str:
        """Resolve ${ENV_VAR} references in URLs."""
        import re

        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.getenv(m.group(1), ""),
            url,
        )

    def _get_endpoints(self, chain: str) -> list[str]:
        """Get the list of RPC endpoints for a chain (with env vars resolved)."""
        if chain == "solana":
            return [self._resolve_url(u) for u in SOLANA_RPC_ENDPOINTS]
        urls = EVM_RPC_ENDPOINTS.get(chain, [])
        return [self._resolve_url(u) for u in urls]

    def _get_health(self, chain: str, url: str) -> EndpointHealth:
        """Get or create an EndpointHealth for a URL."""
        if chain == "solana":
            if url not in self._solana_health:
                self._solana_health[url] = EndpointHealth(url=url, chain=chain)
            return self._solana_health[url]
        if url not in self._evm_health[chain]:
            self._evm_health[chain][url] = EndpointHealth(url=url, chain=chain)
        return self._evm_health[chain][url]

    def get_available_endpoints(self, chain: str) -> list[tuple[str, EndpointHealth]]:
        """
        Get all available (non-dead) endpoints for a chain,
        sorted by health score (lowest failures first).
        """
        endpoints = self._get_endpoints(chain)
        available = []
        for url in endpoints:
            health = self._get_health(chain, url)
            if health.alive:
                available.append((url, health))
        # Sort: healthy endpoints first, then by latency (lower is better)
        available.sort(
            key=lambda x: (
                0 if x[1].consecutive_failures == 0 else 1,
                x[1].avg_latency_ms if x[1].avg_latency_ms > 0 else 999,
            ),
        )
        return available

    # ─── Health Check ──────────────────────────────────────────────

    async def check_endpoint(self, chain: str, url: str, health: EndpointHealth) -> bool:
        """
        Check if an RPC endpoint is alive with a lightweight call.
        Returns True if healthy.
        """
        payload_key = chain if chain in self._health_check_payloads else "ethereum"
        method, params = self._health_check_payloads.get(payload_key, ("eth_blockNumber", []))

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        try:
            client = await self._get_client()
            t0 = time.monotonic()
            resp = await client.post(url, json=body)
            latency = (time.monotonic() - t0) * 1000  # ms

            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, dict) and "result" in data:
                    health.record_success(latency)
                    return True

            # Rate limited: mark with longer cooldown
            if resp.status_code == 429:
                health.is_dead = True
                health.dead_until = time.time() + 120  # 2 min cooldown for rate limits
                health.consecutive_failures += 1
                logger.debug("rpc_rate_limited", url=url, chain=chain)
                return False

            # Other error
            health.record_failure()
            logger.debug(
                "rpc_health_check_failed",
                url=url,
                chain=chain,
                status=resp.status_code,
            )
            return False

        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            health.record_failure()
            logger.debug(
                "rpc_health_check_error",
                url=url,
                chain=chain,
                error=str(exc)[:60],
            )
            return False

    async def ensure_healthy_endpoints(self, chain: str) -> list[tuple[str, EndpointHealth]]:
        """
        Get and verify healthy endpoints for a chain.
        Lazily checks endpoints that haven't been checked recently.
        Returns list of (url, health) tuples that are confirmed healthy.
        """
        all_endpoints = self._get_endpoints(chain)
        if not all_endpoints:
            logger.warning("no_rpc_endpoints_configured", chain=chain)
            return []

        # Quick check: return already-healthy endpoints first
        available = self.get_available_endpoints(chain)
        if available:
            # Verify the first one hasn't gone stale (check if >60s since last check)
            _, health = available[0]
            if time.time() - health.last_check < 30:
                return available

        # Need to validate: check all endpoints with expired health
        healthy = []
        check_tasks = []
        urls_to_check = []

        for url in all_endpoints:
            health = self._get_health(chain, url)
            # Only check endpoints that haven't been verified recently, or are dead
            if (time.time() - health.last_check > 30) or health.is_dead:
                check_tasks.append(self.check_endpoint(chain, url, health))
                urls_to_check.append(url)
            elif health.alive:
                healthy.append((url, health))

        if check_tasks:
            results = await asyncio.gather(*check_tasks, return_exceptions=True)
            for url, is_healthy in zip(urls_to_check, results, strict=False):
                if isinstance(is_healthy, Exception):
                    logger.debug("rpc_check_exception", url=url, error=str(is_healthy))
                    health = self._get_health(chain, url)
                    health.record_failure()
                elif is_healthy:
                    healthy.append((url, self._get_health(chain, url)))

        # Sort healthy: by latency then failures
        healthy.sort(key=lambda x: (x[1].consecutive_failures, x[1].avg_latency_ms if x[1].avg_latency_ms > 0 else 999))
        return healthy

    # ─── Primary Call Method ───────────────────────────────────────

    async def call(
        self,
        chain: str,
        method: str,
        params: list[Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Make an RPC call with automatic fallback across all endpoints.

        Args:
            chain: Chain name (e.g. "ethereum", "solana", "base")
            method: JSON-RPC method (e.g. "eth_call", "getBalance")
            params: JSON-RPC params list
            timeout: Optional per-endpoint timeout override (default: 10s)

        Returns:
            RPC result dict (the "result" field), or None if all endpoints failed.
        """
        if params is None:
            params = []

        endpoints = await self.ensure_healthy_endpoints(chain)
        if not endpoints:
            logger.error("no_healthy_rpc_endpoints", chain=chain)
            return None

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        call_timeout = timeout or self._call_timeout
        last_error: str | None = None

        client = await self._get_client()

        for url, health in endpoints:
            try:
                t0 = time.monotonic()
                resp = await client.post(
                    url,
                    json=body,
                    timeout=call_timeout,
                )
                latency = (time.monotonic() - t0) * 1000

                if resp.status_code == 200:
                    data = resp.json()
                    if data and isinstance(data, dict) and "result" in data:
                        health.record_success(latency)
                        # Check for null result (e.g. empty account)
                        if data["result"] is not None:
                            return data
                        return data  # Return anyway, caller handles null
                    elif data and "error" in data:
                        error_msg = data["error"].get("message", str(data["error"]))
                        health.record_failure()
                        last_error = error_msg
                        logger.debug(
                            "rpc_json_error",
                            url=url,
                            method=method,
                            error=error_msg[:80],
                        )
                        # JSON-RPC errors are often permanent — don't retry for these
                        if data["error"].get("code") in (-32000, -32001, -32004, -32602, -32601):
                            return None
                        continue
                    else:
                        health.record_failure()
                        last_error = "empty_response"
                        continue

                elif resp.status_code == 429:
                    # Rate limited: mark dead with longer cooldown
                    health.is_dead = True
                    health.dead_until = time.time() + 120
                    health.consecutive_failures += 1
                    last_error = f"rate_limited_{resp.status_code}"
                    logger.debug("rpc_call_rate_limited", url=url, chain=chain)
                    continue

                else:
                    health.record_failure()
                    last_error = f"http_{resp.status_code}"
                    logger.debug(
                        "rpc_call_http_error",
                        url=url,
                        status=resp.status_code,
                        method=method,
                    )
                    continue

            except httpx.TimeoutException:
                health.record_failure()
                last_error = "timeout"
                logger.debug("rpc_call_timeout", url=url, method=method)
                continue

            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
                health.record_failure()
                last_error = str(exc)[:60]
                logger.debug("rpc_call_connection_error", url=url, error=last_error)
                continue

            except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
                health.record_failure()
                last_error = str(exc)[:80]
                logger.debug("rpc_call_unexpected_error", url=url, error=last_error)
                continue

        logger.warning(
            "rpc_all_endpoints_failed",
            chain=chain,
            method=method,
            endpoints_tried=len(endpoints),
            last_error=last_error,
        )
        return None

    # ─── Convenience: eth_call for EVM ─────────────────────────────

    async def eth_call(
        self,
        chain: str,
        to_address: str,
        data: str,
        block: str = "latest",
        timeout: float | None = None,
    ) -> str | None:
        """
        Convenience: make an eth_call on an EVM chain.

        Returns the hex result string, or None on failure.
        """
        params = [
            {"to": to_address, "data": data},
            block,
        ]
        result = await self.call(chain, "eth_call", params, timeout=timeout)
        if result and isinstance(result, dict):
            return result.get("result")
        return None

    async def eth_get_logs(
        self,
        chain: str,
        address: str,
        from_block: str,
        to_block: str,
        topics: list[str] | None = None,
        timeout: float | None = None,
    ) -> list[dict[str, Any]] | None:
        """
        Convenience: eth_getLogs on an EVM chain.

        Returns the list of logs, or None on failure.
        """
        filter_params: dict[str, Any] = {
            "address": address,
            "fromBlock": from_block,
            "toBlock": to_block,
        }
        if topics:
            filter_params["topics"] = topics

        result = await self.call(
            chain,
            "eth_getLogs",
            [filter_params],
            timeout=timeout,
        )
        if result and isinstance(result, dict):
            return result.get("result")
        return None

    async def get_balance(
        self,
        chain: str,
        address: str,
        timeout: float | None = None,
    ) -> int | None:
        """
        Get native token balance for an address.
        For EVM: eth_getBalance. For Solana: getBalance.
        Returns balance in smallest unit (wei/lamports), or None.
        """
        if chain == "solana":
            result = await self.call(chain, "getBalance", [address], timeout=timeout)
            if result and isinstance(result, dict):
                return result.get("result", {}).get("value")
            return None

        result = await self.call(chain, "eth_getBalance", [address, "latest"], timeout=timeout)
        if result and isinstance(result, dict):
            val = result.get("result")
            if val and val.startswith("0x"):
                return int(val, 16)
        return None

    # ─── Stats & Health Report ─────────────────────────────────────

    def get_health_report(self) -> dict[str, Any]:
        """Get a health report for all tracked RPC endpoints."""
        report: dict[str, Any] = {
            "evm_chains": {},
            "solana": {},
            "summary": {
                "total_endpoints": 0,
                "alive": 0,
                "dead": 0,
                "total_calls": 0,
                "total_failures": 0,
            },
        }

        for chain, endpoints in self._evm_health.items():
            chain_report = []
            for url, health in endpoints.items():
                chain_report.append(
                    {
                        "url": url,
                        "alive": health.alive,
                        "consecutive_failures": health.consecutive_failures,
                        "total_calls": health.total_calls,
                        "total_failures": health.total_failures,
                        "avg_latency_ms": round(health.avg_latency_ms, 1),
                        "is_dead": health.is_dead,
                    }
                )
                report["summary"]["total_endpoints"] += 1
                if health.alive:
                    report["summary"]["alive"] += 1
                else:
                    report["summary"]["dead"] += 1
                report["summary"]["total_calls"] += health.total_calls
                report["summary"]["total_failures"] += health.total_failures
            report["evm_chains"][chain] = chain_report

        for url, health in self._solana_health.items():
            report["solana"][url] = {
                "alive": health.alive,
                "consecutive_failures": health.consecutive_failures,
                "total_calls": health.total_calls,
                "total_failures": health.total_failures,
                "avg_latency_ms": round(health.avg_latency_ms, 1),
                "is_dead": health.is_dead,
            }
            report["summary"]["total_endpoints"] += 1
            if health.alive:
                report["summary"]["alive"] += 1
            else:
                report["summary"]["dead"] += 1
            report["summary"]["total_calls"] += health.total_calls
            report["summary"]["total_failures"] += health.total_failures

        return report


# ══════════════════════════════════════════════════════════════════════
# GLOBAL SINGLETON
# ══════════════════════════════════════════════════════════════════════

_rpc_router: RpcRouter | None = None


def get_rpc_router() -> RpcRouter:
    """Get or create the global RpcRouter singleton."""
    global _rpc_router
    if _rpc_router is None:
        _rpc_router = RpcRouter()
        logger.info(
            "RpcRouter initialized: %d EVM chains, Solana, ~%d total endpoints",
            len(EVM_RPC_ENDPOINTS),
            sum(len(v) for v in EVM_RPC_ENDPOINTS.values()) + len(SOLANA_RPC_ENDPOINTS),
        )
    return _rpc_router


async def rpc_call(
    chain: str,
    method: str,
    params: list[Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any] | None:
    """
    Quick one-shot RPC call with full fallback.

    For one-off calls when you don't need the router instance.
    """
    router = get_rpc_router()
    return await router.call(chain, method, params, timeout=timeout)


__all__ = [
    "EVM_RPC_ENDPOINTS",
    "SOLANA_RPC_ENDPOINTS",
    "RpcRouter",
    "get_rpc_router",
    "rpc_call",
]

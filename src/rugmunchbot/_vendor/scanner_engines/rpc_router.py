# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/rpc_router.py (frozen copy; do not edit).
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
import re
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
        "https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        "https://mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura — free tier (key)
        "https://virginia.rpc.blxrbdn.com",  # BloXroute — free tier
        "https://uk.rpc.blxrbdn.com",  # BloXroute — EU
        "https://singapore.rpc.blxrbdn.com",  # BloXroute — Asia
        "https://rpc.mevblocker.io",  # MevBlocker — free, MEV-protecting
        "https://gateway.tenderly.co/public/mainnet",  # Tenderly public gateway
        # ── Freemium (keyed) — used ONLY when the free pool is exhausted ──
        "https://lb.drpc.org/ogrpc?network=ethereum&dkey=${DRPC_API_KEY}",  # dRPC load balancer
        "https://rpc.ankr.com/eth/${ANKR_API_KEY}",  # Ankr keyed (higher rate)
        "${QUICKNODE_ETH_HTTP}",  # QuickNode dedicated endpoint (full URL via env)
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
        "https://bsc-dataseed1.bnbchain.org",  # BNB Chain official (renamed domain)
        "https://bsc-dataseed2.bnbchain.org",  # BNB Chain official 2
        "https://bsc-mainnet.public.blastapi.io",  # probe-verified 2026-08-23 (fallback-only for getLogs; fine for call/balance)
        "https://rpc-bsc.48.club",  # 48 Club — free public
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=bsc&dkey=${DRPC_API_KEY}",
        "https://rpc.ankr.com/bsc/${ANKR_API_KEY}",
        "${QUICKNODE_BSC_HTTP}",
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
        "https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=polygon&dkey=${DRPC_API_KEY}",
        "https://rpc.ankr.com/polygon/${ANKR_API_KEY}",
        "${QUICKNODE_POLYGON_HTTP}",
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
        "https://arb-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=arbitrum&dkey=${DRPC_API_KEY}",
        "https://rpc.ankr.com/arbitrum/${ANKR_API_KEY}",
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
        "https://base-mainnet.public.blastapi.io",  # probe-verified 2026-08-23 (fallback-only for getLogs; fine for call/balance)
        "https://base-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Base (key)
        "https://base-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=base&dkey=${DRPC_API_KEY}",
        "https://rpc.ankr.com/base/${ANKR_API_KEY}",
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
        "https://opt-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=optimism&dkey=${DRPC_API_KEY}",
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
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=avalanche&dkey=${DRPC_API_KEY}",
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
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=fantom&dkey=${DRPC_API_KEY}",
    ],
    "linea": [
        "https://rpc.linea.build",  # Linea official
        "https://linea-rpc.publicnode.com",  # PublicNode Linea
        "https://linea.drpc.org",  # dRPC Linea
        "https://1rpc.io/linea",  # 1RPC Linea
        "https://linea.blockpi.network/v1/rpc/public",  # BlockPI Linea
        "https://linea.meowrpc.com",  # MeowRPC Linea
        "https://linea-mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura (key)
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=linea&dkey=${DRPC_API_KEY}",
    ],
    "zksync": [
        "https://mainnet.era.zksync.io",  # zkSync official
        "https://zksync-era.blockpi.network/v1/rpc/public",  # BlockPI zkSync
        "https://zksync.drpc.org",  # dRPC zkSync
        "https://1rpc.io/zksync-era",  # 1RPC zkSync Era
        "https://zksync.meowrpc.com",  # MeowRPC zkSync
        "https://zksync-era.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy zkSync (key)
        "https://zksync-era.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=zksync&dkey=${DRPC_API_KEY}",
    ],
    "scroll": [
        "https://rpc.scroll.io",  # Scroll official
        "https://scroll-rpc.publicnode.com",  # PublicNode Scroll
        "https://scroll.drpc.org",  # dRPC Scroll
        "https://1rpc.io/scroll",  # 1RPC Scroll
        "https://scroll.blockpi.network/v1/rpc/public",  # BlockPI Scroll
        "https://scroll-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Scroll (key)
        "https://scroll-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=scroll&dkey=${DRPC_API_KEY}",
    ],
    "mantle": [
        "https://rpc.mantle.xyz",  # Mantle official
        "https://mantle-rpc.publicnode.com",  # PublicNode Mantle
        "https://mantle.drpc.org",  # dRPC Mantle
        "https://1rpc.io/mantle",  # 1RPC Mantle
        "https://mantle.blockpi.network/v1/rpc/public",  # BlockPI Mantle
        "https://mantle-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Mantle (key)
        "https://mantle-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=mantle&dkey=${DRPC_API_KEY}",
    ],
    "gnosis": [
        "https://gnosis.drpc.org",  # dRPC Gnosis
        "https://rpc.gnosischain.com",  # Gnosis official
        "https://gnosis-rpc.publicnode.com",  # probe-verified 2026-08-23 (fallback-only for getLogs; fine for call/balance)
        "https://1rpc.io/gnosis",  # 1RPC Gnosis
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=gnosis&dkey=${DRPC_API_KEY}",
    ],
    # ── Expanded free-tier chains (Ankr Freemium probed live 2026-08-23) ──
    "flare": [
        "https://flare-rpc.publicnode.com",
        "https://rpc.ankr.com/flare/${ANKR_API_KEY}",
    ],
    "taiko": [
        "https://rpc.mainnet.taiko.xyz",
        "https://rpc.ankr.com/taiko/${ANKR_API_KEY}",
    ],
    "core": [
        "https://rpc.coredao.org",
        "https://rpc.ankr.com/core/${ANKR_API_KEY}",
    ],
    "filecoin": [
        "https://api.node.glif.io",
        "https://rpc.ankr.com/filecoin/${ANKR_API_KEY}",
    ],
    "iotex": [
        "https://api.iotex.one",
        "https://rpc.ankr.com/iotex/${ANKR_API_KEY}",
    ],
    "chiliz": [
        "https://rpc.ankr.com/chiliz/${ANKR_API_KEY}",
    ],
    "gravity": [
        "https://rpc.gravitychain.io",
        "https://rpc.ankr.com/gravity/${ANKR_API_KEY}",
    ],
    "syscoin": [
        "https://rpc.syscoin.org",
        "https://rpc.ankr.com/syscoin/${ANKR_API_KEY}",
    ],
    "xdc": [
        "https://rpc.xinfin.network",
        "https://rpc.ankr.com/xdc/${ANKR_API_KEY}",
    ],
    "celo": [
        "https://forno.celo.org",
        "https://rpc.ankr.com/celo/${ANKR_API_KEY}",
    ],
    "klaytn": [
        "https://public-en-kairos.node.kaia.io",
        "https://rpc.ankr.com/klaytn/${ANKR_API_KEY}",
    ],
    "harmony": [
        "https://api.harmony.one",
        "https://rpc.ankr.com/harmony/${ANKR_API_KEY}",
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
    "https://solana-api.projectserum.com",  # Project Serum — long-standing free
    "https://solana-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Solana (key)
    "https://solana-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
    "https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}",  # Helius (key, paid fallback)
    # ── Freemium (keyed) ──
    "https://lb.drpc.org/ogrpc?network=solana&dkey=${DRPC_API_KEY}",
    "https://rpc.ankr.com/solana/${ANKR_API_KEY}",
    "${QUICKNODE_SOLANA_HTTP}",
]


# ══════════════════════════════════════════════════════════════════════
# ENDPOINT HEALTH TRACKING
# ══════════════════════════════════════════════════════════════════════


@dataclass
class EndpointHealth:
    """Health tracking for a single RPC endpoint."""

    url: str
    chain: str
    tier: str = "free"  # "free" (keyless public) | "freemium" (keyed free-tier)
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

        # ── Tier / rotation / quota state ──────────────────────────
        # Round-robin offset per chain: spreads consecutive calls across
        # the top healthy endpoints instead of hammering the same winner.
        self._rr_offset: dict[str, int] = defaultdict(int)
        # Freemium quota ledger: provider host -> recent call timestamps
        # (1h sliding window). Escalation picks the LEAST-used provider.
        self._quota: dict[str, list[float]] = defaultdict(list)
        # Escalation telemetry: how often the free tier failed us.
        self.freemium_escalations: int = 0
        self._last_escalation_log: dict[str, float] = {}

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

    # ─── Tier helpers ──────────────────────────────────────────────

    @staticmethod
    def _tier_of_template(url: str) -> str:
        """Classify a raw (unresolved) endpoint template.

        Any URL embedding a ``${ENV_VAR}`` key placeholder is *freemium* —
        a keyed free-tier provider whose quota we protect. Everything else
        is *free*. Freemium endpoints are used ONLY when the free pool for
        the chain is exhausted (all dead / rate-limited).
        """
        return "freemium" if "${" in url else "free"

    def _resolve_url(self, url: str) -> str | None:
        """Resolve ``${ENV_VAR}`` references in URLs.

        Returns ``None`` when a referenced key env var is unset/empty —
        keyed endpoints without credentials are excluded entirely so we
        never health-check or call a broken half-resolved URL.
        """

        class _MissingKey(Exception):
            pass

        def _sub(m: re.Match[str]) -> str:
            val = os.getenv(m.group(1), "")
            if not val:
                raise _MissingKey(m.group(1))
            return val

        try:
            return re.sub(r"\$\{(\w+)\}", _sub, url)
        except _MissingKey:
            return None

    def _raw_endpoints(self, chain: str) -> list[str]:
        """Raw endpoint templates for a chain (before key resolution)."""
        if chain == "solana":
            return list(SOLANA_RPC_ENDPOINTS)
        return list(EVM_RPC_ENDPOINTS.get(chain, []))

    def _split_tiers(self, chain: str) -> tuple[list[str], list[str]]:
        """Split a chain's endpoints into resolved (free, freemium) lists.

        Endpoints whose API-key env var is missing are dropped entirely.
        """
        free: list[str] = []
        freemium: list[str] = []
        for raw in self._raw_endpoints(chain):
            resolved = self._resolve_url(raw)
            if resolved is None:
                continue
            if self._tier_of_template(raw) == "freemium":
                freemium.append(resolved)
            else:
                free.append(resolved)
        return free, freemium

    def _get_endpoints(self, chain: str) -> list[str]:
        """All usable (resolved) endpoints for a chain, free tier first."""
        free, freemium = self._split_tiers(chain)
        return free + freemium

    def _get_health(self, chain: str, url: str, tier: str = "free") -> EndpointHealth:
        """Get or create an EndpointHealth for a URL."""
        if chain == "solana":
            if url not in self._solana_health:
                self._solana_health[url] = EndpointHealth(url=url, chain=chain, tier=tier)
            return self._solana_health[url]
        if url not in self._evm_health[chain]:
            self._evm_health[chain][url] = EndpointHealth(url=url, chain=chain, tier=tier)
        return self._evm_health[chain][url]

    # ─── Quota ledger (freemium providers) ─────────────────────────

    def _record_quota(self, url: str) -> None:
        """Record a call against the provider host's hourly quota window."""
        from urllib.parse import urlparse

        now = time.time()
        q = self._quota[urlparse(url).netloc]
        q[:] = [t for t in q if now - t < 3600]
        q.append(now)

    def _quota_usage(self, url: str) -> int:
        """Calls made to this provider host in the last hour."""
        from urllib.parse import urlparse

        now = time.time()
        return sum(1 for t in self._quota.get(urlparse(url).netloc, []) if now - t < 3600)

    def _record_escalation(self, chain: str) -> None:
        """Count + throttled-log a free→freemium escalation for a chain."""
        self.freemium_escalations += 1
        now = time.time()
        if now - self._last_escalation_log.get(chain, 0.0) > 300:
            logger.warning(
                "rpc_freemium_escalation",
                chain=chain,
                note="free tier exhausted; falling back to keyed providers",
            )
            self._last_escalation_log[chain] = now

    # ─── Availability ──────────────────────────────────────────────

    def _rank(self, pairs: list[tuple[str, EndpointHealth]]) -> list[tuple[str, EndpointHealth]]:
        """Sort by health: clean first, then lowest latency."""
        return sorted(
            pairs,
            key=lambda x: (
                0 if x[1].consecutive_failures == 0 else 1,
                x[1].avg_latency_ms if x[1].avg_latency_ms > 0 else 999,
            ),
        )

    def _spread(self, ranked: list[tuple[str, EndpointHealth]], chain: str) -> list[tuple[str, EndpointHealth]]:
        """Round-robin the top-3 healthy endpoints across consecutive calls.

        Keeps quality ordering (best-first within the rotation window) but
        distributes load so one low-latency winner doesn't absorb every
        request and trip its rate limit.
        """
        head, tail = ranked[:3], ranked[3:]
        if len(head) > 1:
            k = self._rr_offset[chain] % len(head)
            self._rr_offset[chain] += 1
            head = head[k:] + head[:k]
        return head + tail

    def get_available_endpoints(self, chain: str) -> list[tuple[str, EndpointHealth]]:
        """
        Get callable endpoints for a chain, tier-aware:

        1. Free tier alive → round-robin spread over its healthiest.
        2. Free tier exhausted (all dead/rate-limited) → ESCALATE to the
           freemium keyed pool, least-quota-used provider first.
        3. Nothing alive → empty list.
        """
        free_urls, freemium_urls = self._split_tiers(chain)

        free_alive = [(u, self._get_health(chain, u)) for u in free_urls if self._get_health(chain, u).alive]
        if free_alive:
            return self._spread(self._rank(free_alive), chain)

        # Free pool exhausted → escalate into keyed providers.
        fremium_alive = [
            (u, self._get_health(chain, u, tier="freemium"))
            for u in freemium_urls
            if self._get_health(chain, u, tier="freemium").alive
        ]
        if fremium_alive:
            self._record_escalation(chain)
            fremium_alive.sort(
                key=lambda x: (
                    self._quota_usage(x[0]),  # least-used provider first
                    x[1].consecutive_failures,
                    x[1].avg_latency_ms if x[1].avg_latency_ms > 0 else 999,
                )
            )
            return fremium_alive

        return []

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

        Tier-aware: only the ACTIVE tier (free while it has living
        endpoints, freemium after escalation) is health-checked — we never
        burn keyed-provider quota on probes while the free pool is fine.
        """
        free_urls, fremium_urls = self._split_tiers(chain)
        if not free_urls and not fremium_urls:
            logger.warning("no_rpc_endpoints_configured", chain=chain)
            return []

        # Quick check: tier-aware availability without network I/O
        available = self.get_available_endpoints(chain)
        if available:
            # Verify the first one hasn't gone stale (check if >30s since last check)
            _, health = available[0]
            if time.time() - health.last_check < 30:
                return available

        # Validate: check stale/dead endpoints within the ACTIVE tier only.
        active_tier = available[0][1].tier if available else "free"
        active_urls = free_urls if active_tier == "free" else fremium_urls

        healthy = []
        check_tasks = []
        urls_to_check = []

        for url in active_urls:
            health = self._get_health(chain, url, tier=active_tier)
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
                    health = self._get_health(chain, url, tier=active_tier)
                    health.record_failure()
                elif is_healthy:
                    healthy.append((url, self._get_health(chain, url, tier=active_tier)))

        # Sort healthy: by latency then failures; spread load across top-3
        healthy = self._spread(self._rank(healthy), chain)
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
            if health.tier == "freemium":
                self._record_quota(url)  # keyed provider: count against hourly window
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
                "freemium_escalations": self.freemium_escalations,
                "freemium_calls_last_hour": sum(
                    sum(1 for t in q if time.time() - t < 3600) for q in self._quota.values()
                ),
            },
        }

        def _entry(health: EndpointHealth) -> dict[str, Any]:
            return {
                "url": health.url,
                "tier": health.tier,
                "alive": health.alive,
                "consecutive_failures": health.consecutive_failures,
                "total_calls": health.total_calls,
                "total_failures": health.total_failures,
                "avg_latency_ms": round(health.avg_latency_ms, 1),
                "is_dead": health.is_dead,
            }

        for chain, endpoints in self._evm_health.items():
            chain_report = []
            for _url, health in endpoints.items():
                chain_report.append(_entry(health))
                report["summary"]["total_endpoints"] += 1
                if health.alive:
                    report["summary"]["alive"] += 1
                else:
                    report["summary"]["dead"] += 1
                report["summary"]["total_calls"] += health.total_calls
                report["summary"]["total_failures"] += health.total_failures
            report["evm_chains"][chain] = chain_report

        for url, health in self._solana_health.items():
            report["solana"][url] = _entry(health)
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

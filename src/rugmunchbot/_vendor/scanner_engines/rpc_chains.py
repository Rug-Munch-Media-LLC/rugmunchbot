"""RPC chain endpoint configuration — data only, no logic.

Split from rpc_router.py (2026-08-23 production audit): the endpoint chain
map is pure data; the router (health tracking, tiering, fallback) imports
it. Edit endpoints here; never add behavior.
"""

from __future__ import annotations

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
        "https://virginia.rpc.blxrbdn.com",  # BloXroute — free tier
        "https://uk.rpc.blxrbdn.com",  # BloXroute — EU
        "https://singapore.rpc.blxrbdn.com",  # BloXroute — Asia
        "https://rpc.mevblocker.io",  # MevBlocker — free, MEV-protecting
        "https://gateway.tenderly.co/public/mainnet",  # Tenderly public gateway
        "https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy — free tier (key)
        "https://mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura — free tier (key)
        "https://eth.nownodes.io/${NOWNODES_API_KEY}",  # NOWNodes (verified 2026-08-23)
        "https://lb.drpc.org/ogrpc?network=ethereum&dkey=${DRPC_API_KEY}",  # dRPC load balancer
        "https://rpc.ankr.com/eth/${ANKR_API_KEY}",  # Ankr keyed (higher rate)
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
        "https://lb.drpc.org/ogrpc?network=bsc&dkey=${DRPC_API_KEY}",
        "https://rpc.ankr.com/bsc/${ANKR_API_KEY}",
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
        "https://lb.drpc.org/ogrpc?network=polygon&dkey=${DRPC_API_KEY}",
        "https://rpc.ankr.com/polygon/${ANKR_API_KEY}",
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
        "https://lb.drpc.org/ogrpc?network=linea&dkey=${DRPC_API_KEY}",
    ],
    "zksync": [
        "https://mainnet.era.zksync.io",  # zkSync official
        "https://zksync-era.blockpi.network/v1/rpc/public",  # BlockPI zkSync
        "https://zksync.drpc.org",  # dRPC zkSync
        "https://1rpc.io/zksync-era",  # 1RPC zkSync Era
        "https://zksync.meowrpc.com",  # MeowRPC zkSync
        "https://zksync-era.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy zkSync (key)
        "https://lb.drpc.org/ogrpc?network=zksync&dkey=${DRPC_API_KEY}",
    ],
    "scroll": [
        "https://rpc.scroll.io",  # Scroll official
        "https://scroll-rpc.publicnode.com",  # PublicNode Scroll
        "https://scroll.drpc.org",  # dRPC Scroll
        "https://1rpc.io/scroll",  # 1RPC Scroll
        "https://scroll.blockpi.network/v1/rpc/public",  # BlockPI Scroll
        "https://scroll-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Scroll (key)
        "https://lb.drpc.org/ogrpc?network=scroll&dkey=${DRPC_API_KEY}",
    ],
    "mantle": [
        "https://rpc.mantle.xyz",  # Mantle official
        "https://mantle-rpc.publicnode.com",  # PublicNode Mantle
        "https://mantle.drpc.org",  # dRPC Mantle
        "https://1rpc.io/mantle",  # 1RPC Mantle
        "https://mantle.blockpi.network/v1/rpc/public",  # BlockPI Mantle
        "https://mantle-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Mantle (key)
        "https://lb.drpc.org/ogrpc?network=mantle&dkey=${DRPC_API_KEY}",
    ],
    "gnosis": [
        "https://gnosis.drpc.org",  # dRPC Gnosis
        "https://rpc.gnosischain.com",  # Gnosis official
        "https://gnosis-rpc.publicnode.com",  # probe-verified 2026-08-23 (fallback-only for getLogs; fine for call/balance)
        "https://1rpc.io/gnosis",  # 1RPC Gnosis
        "https://lb.drpc.org/ogrpc?network=gnosis&dkey=${DRPC_API_KEY}",
    ],
}


SOLANA_RPC_ENDPOINTS: list[str] = [
    "https://api.mainnet-beta.solana.com",  # Solana official public
    "https://solana-rpc.publicnode.com",  # PublicNode Solana
    "https://solana.drpc.org",  # dRPC Solana
    "https://rpc.ankr.com/solana",  # Ankr Solana
    "https://solana.meowrpc.com",  # MeowRPC Solana
    "https://solana.blockpi.network/v1/rpc/public",  # BlockPI Solana
    "https://solana-api.projectserum.com",  # Project Serum — long-standing free
    "https://solana-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",  # Alchemy Solana (key)
    "https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}",  # Helius (key, paid fallback)
    # ── Freemium (keyed) ──
    "https://lb.drpc.org/ogrpc?network=solana&dkey=${DRPC_API_KEY}",
    "https://rpc.ankr.com/solana/${ANKR_API_KEY}",
    "${QUICKNODE_SOLANA_HTTP}",
    "${CHAINSTACK_SOLANA_URL}",  # Chainstack shared node (verified 2026-08-23)
    "${GETBLOCK_SOLANA_URL}",  # GetBlock shared (verified 2026-08-23)
    "https://sol.nownodes.io/${NOWNODES_API_KEY}",  # NOWNodes (verified 2026-08-23)
]

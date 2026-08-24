"""RPC chain endpoint configuration — FIRST-PARTY, vendor-refresh-proof.

Lives outside _vendor/ because refresh-vendors runs kept overwriting the
chain map (lost the 12-chain expansion twice). The vendored rpc_router
imports THIS module. Edit endpoints here; never add behavior.

Sources: free public RPCs probed live 2026-08-23 + keyed freemium
(Ankr/BlockPI/Alchemy x2, dRPC, Helius x2, Chainstack, GetBlock, TronGrid).
"""

from __future__ import annotations

EVM_RPC_ENDPOINTS: dict[str, list[str]] = {
    "ethereum": [
        "https://ethereum.blockpi.network/v1/rpc/1b0e56635142347bae8b77c148a652705055329c",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY_2}",  # Alchemy acct 2 rotation
        "https://mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # Infura — free tier (key)
        # ── Freemium (keyed) — used ONLY when the free pool is exhausted ──
        "https://lb.drpc.org/ogrpc?network=ethereum&dkey=${DRPC_API_KEY}",  # dRPC load balancer
        "https://rpc.ankr.com/eth/${ANKR_API_KEY}",  # Ankr keyed (higher rate)
        "${QUICKNODE_ETH_HTTP}",  # QuickNode dedicated endpoint (full URL via env)
    ],
    "bsc": [
        "https://bsc.blockpi.network/v1/rpc/dda061f196261a9ab6c3079f80a9a5095063256c",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://polygon.blockpi.network/v1/rpc/4aa39c18c29b7ac4ef0b58ab65069a27c342f81c",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://arbitrum.blockpi.network/v1/rpc/f91d8f7601e1a4b34e79335cf92c2515d79a12d5",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://optimism.blockpi.network/v1/rpc/c6e456de5f16b57f545310fef4657fb2ab2be75c",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://avalanche.blockpi.network/v1/rpc/9cbc011e7f9b0f8e72eb22377fa6016dea085b72",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://fantom.blockpi.network/v1/rpc/439ef758515123721ba658ed78220c20d63efba5",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://linea.blockpi.network/v1/rpc/21b331fd54d06c8641e0396b2e50f62af6dc9276",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://zksync-era.blockpi.network/v1/rpc/853cc3247856ee3a291d4eea7ff13a8eb67c39af",  # BlockPI keyed (probed live 2026-08-23)
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
        "https://gnosis.blockpi.network/v1/rpc/89c1511fa988fc944cf35b237a58ed8b8af8cf67",  # BlockPI keyed (probed live 2026-08-23)
        "https://gnosis.drpc.org",  # dRPC Gnosis
        "https://rpc.gnosischain.com",  # Gnosis official
        "https://gnosis-rpc.publicnode.com",  # probe-verified 2026-08-23 (fallback-only for getLogs; fine for call/balance)
        "https://1rpc.io/gnosis",  # 1RPC Gnosis
        # ── Freemium (keyed) ──
        "https://lb.drpc.org/ogrpc?network=gnosis&dkey=${DRPC_API_KEY}",
    ],
    # ── Expanded free-tier chains (Ankr Freemium probed live 2026-08-23) ──
    "flare": [
        # publicnode 404 + official unreachable from host (2026-08-23 probe);
        # Ankr keyed is the primary until a free mirror verifies.
        "https://rpc.ankr.com/flare/${ANKR_API_KEY}",
    ],
    "taiko": [
        "https://taiko.blockpi.network/v1/rpc/4795cef3a23e019566442c0681d15d9227987578",  # BlockPI keyed (probed live 2026-08-23)
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

# VENDORED 2026-08-24 from rmi-backend/app/chain_registry/evm.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
EVM chain configurations.

All 12 EVM chains supported by the chain registry:
ethereum, base, bsc, arbitrum, polygon, avalanche, optimism,
fantom, linea, zksync, scroll, mantle.
"""

from rugmunchbot._vendor.chain_registry.constants import ChainConfig, ChainFamily, register

# ── Ethereum ────────────────────────────────────────────────────────

ETHEREUM = ChainConfig(
    name="ethereum",
    chain_id="1",
    family=ChainFamily.EVM,
    display_name="Ethereum",
    rpc_endpoints=[
        "https://eth.llamarpc.com",
        "https://rpc.ankr.com/eth",
        "https://eth.drpc.org",
        "https://ethereum-rpc.publicnode.com",
        "https://1rpc.io/eth",
        "https://eth.meowrpc.com",
        "https://rpc.flashbots.net",
        "https://ethereum.blockpi.network/v1/rpc/public",
        "https://cloudflare-eth.com",
        "https://eth-mainnet.public.blastapi.io",
        "https://virginia.rpc.blxrbdn.com",
        "https://uk.rpc.blxrbdn.com",
        "https://singapore.rpc.blxrbdn.com",
        "https://eth-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
        "https://mainnet.infura.io/v3/${INFURA_PROJECT_ID}",
    ],
    explorer_url="https://etherscan.io",
    explorer_api_url="https://api.etherscan.io/api",
    explorer_api_key_env="ETHERSCAN_API_KEY",
    native_token_symbol="ETH",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="ethereum",
    known_lockers={
        "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2": "UNCX Network",
        "0xe2fE530C047f2d85298b07D9333C05737f1435fB": "Team Finance",
        "0x7ee058420e5937496f5a2096f04caa7721cf70cc": "PinkLock",
    },
    cex_hot_wallets={
        "Binance": [
            "0x28C6c06298d514Db089934071355E5743bf21d60",
            "0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8",
            "0xDFd5293D8e347dFe59E90eFd55b2956a1343963d",
            "0xF977814e90dA44bFA03b6295A0616a897441aceC",
            "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549",
            "0x4976A4A02f38326660D17bf34b431dC6e2eb2327",
            "0x56Eddb7aa87536c09CCc2793473599fD21A8b17F",
            "0x5a52E96BAcdaBb82fd05763E25335261B270Efcb",
            "0x9696f59E4d72E237BE84fFD425DCaD154Bf96976",
            "0x61189Da79177950A7272c88c6058B96D4Bcd6bE2",
        ],
        "Coinbase": [
            "0x503828976D22510aad0201ac7EC88293211D23Da",
            "0xdD2F458a4b46251B1dB5E2ed56bDDf8DB4Ed7E8e",
            "0xbf3EEfAC1073b93a2639fB1eD4aB7bC997d8732E",
            "0x71660c4005BA85C37ccec55d0C4493E3Fe753dBf",
            "0xA9D1e08C7793af67e9d92fe308d5697FB81d3E43",
            "0x3CD751E6b0078Be39333c1fbFDeC3a2b87Ab9C82",
        ],
        "OKX": [
            "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b",
            "0x236F9F97e0E62388479bf9E5BA4889E46B0273c3",
            "0x8a612aB4cC1CBf5b37Bd6a0E8A0AcE5AF0e5B00F",
        ],
        "Bybit": [
            "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
            "0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFC73",
        ],
        "Kraken": [
            "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
            "0x0A869d79a7052C7f1b55a8EbAbbEa3420f0D1E13",
            "0xE853c56864A2ebe4576a807D26Fdc4A0adA51919",
            "0x267be1C1D684F78cb4F6a176C4911b741E4Ffdc0",
        ],
        "KuCoin": [
            "0x2B5634C42055806a59e9107ED44D43c426E58258",
            "0x679040D7223FdE9363AA2cF68B4C8A7394cc83C5",
        ],
        "Gate.io": [
            "0x0D0707963952f2fBA59dD06f2b425ace40b492Fe",
            "0x7793cD85c11A924478cF358d2b5DeC8C4f41acB0",
        ],
        "Bitfinex": [
            "0x876EabF441B2EE5B5b0554Fd502a8E0600950cFa",
            "0x1151314c646Ce4E0eFD76d1aF4760aE66a2Fe30e",
            "0x742d35Cc6634C0532925a3b844Bc9e7596bBDEdD",
        ],
        "Bitget": [
            "0x0639556F03714A74a5fEEaF5736a4A64f70Df206",
            "0x97dE9E01a1c95d3D09D9b29333dF4e5A50A1B61d",
        ],
        "MEXC": [
            "0x75e89d5979E4f6Fba9F97c104c2F0AFB3F1dcB88",
            "0x3CC936b795A188F0e246cBB2D74C5Bd190aeCF18",
        ],
        "HTX": [
            "0xeB2d2F1b8c558a40207669291Fda468E50c8a0Bb",
            "0x1871e9B1C47c0FF6FeA5bAAcB3A04F8068D60E7e",
        ],
        "Crypto.com": [
            "0x6262998Ced04146fA42253a5C0AF90CA02dfd2A3",
            "0x46340b20830761efd32832A74d7169B29FEB9758",
        ],
        "Gemini": [
            "0xd24400ae8BfEBb18cA49Be86258a3C749cf46853",
            "0x61EDCDf5bb737ADffE5043706e7C5bb1f1a56eEA",
            "0x07ee55aA48Bb72DCc597523F4A2632811C1bE3a4",
        ],
        "Korbit": [
            "0xF977814e90dA44bFA03b6295A0616a897441aceC",
        ],
        "Bittrex": [
            "0x94b1B6eB67A7D14b2ad2Ee93FeBA5457c5451eF2",
            "0x66F820a414680B5bcda5eAFc3d1F3C99b9F9B11f",
        ],
        "Bitstamp": [
            "0x00bdb5699745f5b860228c8f939abF1b9Ae374eD",
            "0x1522900B6daFac587d499a862861C0869Be6E428",
        ],
        "Upbit": [
            "0x390De26d772D2e2005C6d1D24afC902baC3ad635",
            "0x5e032243d507C743b061Ef021E2eC7bfA1Ec5277",
        ],
        "OKEx": [
            "0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b",
        ],
        "BinanceUS": [
            "0x61189Da79177950A7272c88c6058B96D4Bcd6bE2",
        ],
        "Poloniex": [
            "0x32Be343B94f860124dC4fEe278FDCBD38C102D88",
            "0x209c4784AB1E8183Cf58cA33cb740dcbF2fD3bF7",
        ],
        "Bithumb": [
            "0x44E9d0Bc5f3F4Bc54d9F6c1bd9fA09fD0a1e633c",
        ],
        "Coinone": [
            "0x167a9333BF582556f35Bd4d16a7E80a9525A9c3B",
        ],
        "LBank": [
            "0x1205E4F0D2f0225Fd4b3d8d7C1C9c7b9A1B3D5F7",
        ],
        "BitMart": [
            "0xE93381fB4c4F14bDa253907b18faD305D799241a",
        ],
        "WooX": [
            "0x0d83F81c79E8e93b30C4f5e9a4bDf4D3C5E7F9A1",
        ],
    },
    api_key_env="ETHERSCAN_API_KEY",
)

register(ETHEREUM)

# ── Base ────────────────────────────────────────────────────────────

BASE = ChainConfig(
    name="base",
    chain_id="8453",
    family=ChainFamily.EVM,
    display_name="Base",
    rpc_endpoints=[
        "https://base.llamarpc.com",
        "https://rpc.ankr.com/base",
        "https://base.drpc.org",
        "https://base-rpc.publicnode.com",
        "https://1rpc.io/base",
        "https://base.meowrpc.com",
        "https://base.blockpi.network/v1/rpc/public",
        "https://mainnet.base.org",
        "https://base-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://basescan.org",
    explorer_api_url="https://api.basescan.org/api",
    explorer_api_key_env="BASESCAN_API_KEY",
    native_token_symbol="ETH",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="base",
    known_lockers={
        "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2": "UNCX Network",
        "0x7ee058420e5937496f5a2096f04caa7721cf70cc": "PinkLock",
    },
    cex_hot_wallets={
        "Coinbase": [
            "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A",
            "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
        ],
        "Binance": [
            "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A",
        ],
        "OKX": [
            "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        ],
    },
    api_key_env="BASESCAN_API_KEY",
)

register(BASE)

# ── BSC ─────────────────────────────────────────────────────────────

BSC = ChainConfig(
    name="bsc",
    chain_id="56",
    family=ChainFamily.EVM,
    display_name="BSC",
    rpc_endpoints=[
        "https://binance.llamarpc.com",
        "https://rpc.ankr.com/bsc",
        "https://bsc.drpc.org",
        "https://bsc-rpc.publicnode.com",
        "https://1rpc.io/bnb",
        "https://bsc.meowrpc.com",
        "https://bsc.blockpi.network/v1/rpc/public",
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed2.binance.org",
        "https://bsc-dataseed3.binance.org",
        "https://bsc-dataseed1.defibit.io",
        "https://bsc-dataseed2.defibit.io",
        "https://bsc-dataseed1.ninicoin.io",
        "https://bsc-dataseed2.ninicoin.io",
        "https://bsc-mainnet.public.blastapi.io",
        "https://bsc-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://bscscan.com",
    explorer_api_url="https://api.bscscan.com/api",
    explorer_api_key_env="BSCSCAN_API_KEY",
    native_token_symbol="BNB",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="bsc",
    known_lockers={
        "0x663A5C229c09b049E36dCc11a9B0d4a8c9c3dBC2": "UNCX Network",
        "0x7ee058420e5937496f5a2096f04caa7721cf70cc": "PinkLock",
        "0x2D8E3580C6E428d4EaA069198D0B76ae0f98e43a": "Mudra",
    },
    cex_hot_wallets={
        "Binance": [
            "0xB38e8c17e38363aF6EbdCb3dAE12e0243582891D",
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
        ],
        "MEXC": ["0x51e6D27FA57373d8d4C256231241053a70Cb1d93"],
        "OKX": [
            "0x8894E0a0c962CB723c1976a4421c95949bE2D4E3",
        ],
        "KuCoin": [
            "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
        ],
        "Gate.io": [
            "0x2A4EdF32D2b0ce008FeEB8EE2dA93B2dF34c3a4D",
        ],
        "Bybit": [
            "0xE2Fc0d06Fe01466fDDb948Cf38A8Ee98f36793B0",
        ],
        "Bitget": [
            "0x6B6a3E94FEb2B5FaF40bfD71B39C4D6A4E6F8e7c",
        ],
    },
    api_key_env="BSCSCAN_API_KEY",
)

register(BSC)

# ── Arbitrum ────────────────────────────────────────────────────────

ARBITRUM = ChainConfig(
    name="arbitrum",
    chain_id="42161",
    family=ChainFamily.EVM,
    display_name="Arbitrum",
    rpc_endpoints=[
        "https://arbitrum.llamarpc.com",
        "https://rpc.ankr.com/arbitrum",
        "https://arbitrum.drpc.org",
        "https://arbitrum-one-rpc.publicnode.com",
        "https://1rpc.io/arb",
        "https://arbitrum.meowrpc.com",
        "https://arbitrum.blockpi.network/v1/rpc/public",
        "https://arb1.arbitrum.io/rpc",
        "https://arb-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://arbiscan.io",
    explorer_api_url="https://api.arbiscan.io/api",
    explorer_api_key_env="ARBISCAN_API_KEY",
    native_token_symbol="ETH",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="arbitrum",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            "0xF977814e90dA44bFA03b6295A0616a897441aceC",
        ],
        "Coinbase": [
            "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
        ],
        "Bybit": [
            "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
        "Kraken": [
            "0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2",
        ],
    },
    api_key_env="ARBISCAN_API_KEY",
)

register(ARBITRUM)

# ── Polygon ─────────────────────────────────────────────────────────

POLYGON = ChainConfig(
    name="polygon",
    chain_id="137",
    family=ChainFamily.EVM,
    display_name="Polygon",
    rpc_endpoints=[
        "https://polygon.llamarpc.com",
        "https://rpc.ankr.com/polygon",
        "https://polygon.drpc.org",
        "https://polygon-rpc.com",
        "https://1rpc.io/matic",
        "https://polygon.meowrpc.com",
        "https://polygon.blockpi.network/v1/rpc/public",
        "https://polygon-bor-rpc.publicnode.com",
        "https://polygon-mainnet.public.blastapi.io",
        "https://polygon-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://polygonscan.com",
    explorer_api_url="https://api.polygonscan.com/api",
    explorer_api_key_env="POLYGONSCAN_API_KEY",
    native_token_symbol="MATIC",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="polygon",
    cex_hot_wallets={
        "Binance": [
            "0xe7804c37c13166fF0b37F5aE0BB07A3aEbb6e245",
            "0xF977814e90dA44bFA03b6295A0616a897441aceC",
        ],
        "Coinbase": [
            "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
        "Bybit": [
            "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
        ],
        "KuCoin": [
            "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
        ],
        "Gate.io": [
            "0x2A4EdF32D2b0ce008FeEB8EE2dA93B2dF34c3a4D",
        ],
    },
    api_key_env="POLYGONSCAN_API_KEY",
)

register(POLYGON)

# ── Avalanche ───────────────────────────────────────────────────────

AVALANCHE = ChainConfig(
    name="avalanche",
    chain_id="43114",
    family=ChainFamily.EVM,
    display_name="Avalanche",
    rpc_endpoints=[
        "https://avalanche.llamarpc.com",
        "https://rpc.ankr.com/avalanche",
        "https://avalanche.drpc.org",
        "https://avalanche-c-chain-rpc.publicnode.com",
        "https://1rpc.io/avax/c",
        "https://avalanche.meowrpc.com",
        "https://avalanche.blockpi.network/v1/rpc/public",
        "https://api.avax.network/ext/bc/C/rpc",
        "https://avalanche-mainnet.infura.io/v3/${INFURA_PROJECT_ID}",
    ],
    explorer_url="https://snowtrace.io",
    explorer_api_url="https://api.snowtrace.io/api",
    explorer_api_key_env="SNOWTRACE_API_KEY",
    native_token_symbol="AVAX",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="avalanche",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
            "0xF977814e90dA44bFA03b6295A0616a897441aceC",
        ],
        "Coinbase": [
            "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
        "Bybit": [
            "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
        ],
        "KuCoin": [
            "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
        ],
    },
    api_key_env="SNOWTRACE_API_KEY",
)

register(AVALANCHE)

# ── Optimism ────────────────────────────────────────────────────────

OPTIMISM = ChainConfig(
    name="optimism",
    chain_id="10",
    family=ChainFamily.EVM,
    display_name="Optimism",
    rpc_endpoints=[
        "https://optimism.llamarpc.com",
        "https://rpc.ankr.com/optimism",
        "https://optimism.drpc.org",
        "https://optimism-rpc.publicnode.com",
        "https://1rpc.io/op",
        "https://optimism.meowrpc.com",
        "https://optimism.blockpi.network/v1/rpc/public",
        "https://mainnet.optimism.io",
        "https://opt-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://optimistic.etherscan.io",
    explorer_api_url="https://api-optimistic.etherscan.io/api",
    explorer_api_key_env="ETHERSCAN_API_KEY",
    native_token_symbol="ETH",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="optimism",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
        ],
        "Coinbase": [
            "0x3154B747c4bFd35f2e42dF08c28c1A7c8C4f71D7",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
        "Bybit": [
            "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
        ],
    },
    api_key_env="ETHERSCAN_API_KEY",
)

register(OPTIMISM)

# ── Fantom ──────────────────────────────────────────────────────────

FANTOM = ChainConfig(
    name="fantom",
    chain_id="250",
    family=ChainFamily.EVM,
    display_name="Fantom",
    rpc_endpoints=[
        "https://rpc.ankr.com/fantom",
        "https://fantom.drpc.org",
        "https://fantom-rpc.publicnode.com",
        "https://1rpc.io/ftm",
        "https://fantom.meowrpc.com",
        "https://rpc.fantom.network",
        "https://fantom-mainnet.public.blastapi.io",
        "https://fantom.blockpi.network/v1/rpc/public",
    ],
    explorer_url="https://ftmscan.com",
    explorer_api_url="https://api.ftmscan.com/api",
    explorer_api_key_env="FTMSCAN_API_KEY",
    native_token_symbol="FTM",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="fantom",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
        "KuCoin": [
            "0xD6216fc19DB775Df92A4Dba0f1E18b85a4c623F0",
        ],
    },
    api_key_env="FTMSCAN_API_KEY",
)

register(FANTOM)

# ── Linea ───────────────────────────────────────────────────────────

LINEA = ChainConfig(
    name="linea",
    chain_id="59144",
    family=ChainFamily.EVM,
    display_name="Linea",
    rpc_endpoints=[
        "https://rpc.linea.build",
        "https://linea-rpc.publicnode.com",
        "https://linea.drpc.org",
        "https://1rpc.io/linea",
        "https://linea.blockpi.network/v1/rpc/public",
        "https://linea.meowrpc.com",
        "https://linea-mainnet.infura.io/v3/${INFURA_PROJECT_ID}",
    ],
    explorer_url="https://lineascan.build",
    explorer_api_url="https://api.lineascan.build/api",
    explorer_api_key_env="",
    native_token_symbol="ETH",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="linea",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
    },
    api_key_env="",
)

register(LINEA)

# ── zkSync Era ──────────────────────────────────────────────────────

ZKSYNC = ChainConfig(
    name="zksync",
    chain_id="324",
    family=ChainFamily.EVM,
    display_name="zkSync Era",
    rpc_endpoints=[
        "https://mainnet.era.zksync.io",
        "https://zksync-era.blockpi.network/v1/rpc/public",
        "https://zksync.drpc.org",
        "https://1rpc.io/zksync-era",
        "https://zksync.meowrpc.com",
        "https://zksync-era.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://explorer.zksync.io",
    explorer_api_url="https://block-explorer-api.mainnet.zksync.io/api",
    explorer_api_key_env="",
    native_token_symbol="ETH",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="zksync",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
        "Bybit": [
            "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
        ],
    },
    api_key_env="",
)

register(ZKSYNC)

# ── Scroll ──────────────────────────────────────────────────────────

SCROLL = ChainConfig(
    name="scroll",
    chain_id="534352",
    family=ChainFamily.EVM,
    display_name="Scroll",
    rpc_endpoints=[
        "https://rpc.scroll.io",
        "https://scroll-rpc.publicnode.com",
        "https://scroll.drpc.org",
        "https://1rpc.io/scroll",
        "https://scroll.blockpi.network/v1/rpc/public",
        "https://scroll-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://scrollscan.com",
    explorer_api_url="https://api.scrollscan.com/api",
    explorer_api_key_env="",
    native_token_symbol="ETH",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="scroll",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
        ],
        "OKX": [
            "0x06959153B974D0D5fDfd87D561dF1cA9aA5E7f05",
        ],
    },
    api_key_env="ETHERSCAN_API_KEY",
)

register(SCROLL)

# ── Mantle ──────────────────────────────────────────────────────────

MANTLE = ChainConfig(
    name="mantle",
    chain_id="5000",
    family=ChainFamily.EVM,
    display_name="Mantle",
    rpc_endpoints=[
        "https://rpc.mantle.xyz",
        "https://mantle-rpc.publicnode.com",
        "https://mantle.drpc.org",
        "https://1rpc.io/mantle",
        "https://mantle.blockpi.network/v1/rpc/public",
        "https://mantle-mainnet.g.alchemy.com/v2/${ALCHEMY_KEY}",
    ],
    explorer_url="https://mantlescan.xyz",
    explorer_api_url="https://api.mantlescan.xyz/api",
    explorer_api_key_env="",
    native_token_symbol="MNT",  # noqa: S106 — config default
    native_token_decimals=18,
    dexscreener_chain_id="mantle",
    cex_hot_wallets={
        "Binance": [
            "0x3C7832B91d940a7C87e0119f60A106C6dB2CcC60",
        ],
        "Bybit": [
            "0xf89d7b9c864f589bbF53a82105107622B35EaA40",
        ],
    },
    api_key_env="",
)

register(MANTLE)

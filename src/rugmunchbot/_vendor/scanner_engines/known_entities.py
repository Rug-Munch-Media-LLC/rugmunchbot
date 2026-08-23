# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/known_entities.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Known-Entity Wallet Database — Rug Munch Media Scanner
======================================================
Prevents the scanner from mislabeling legitimate protocol wallets.
- NEVER flag Meteora/Jupiter/Raydium/Orca/pump.fun as "bundlers"
- NEVER flag CEX hot/cold wallets as "unknown whale"
- NEVER flag DEX routers as "suspicious contracts"

Sources: eth-labels MCP, Blockscout, Etherscan, protocol docs, chain-indexer
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════════════
# CRITICAL: Meteora DLMM pools use frequent rebalancing transactions
# that LOOK like bot activity but are NORMAL protocol operations.
# NEVER flag Meteora transactions as bundler/suspicious.
# ══════════════════════════════════════════════════════════════════════

# ── Exchange Wallets ──────────────────────────────────────────────────

EXCHANGE_WALLETS = {
    "binance": {
        "name": "Binance",
        "ethereum": [
            ("0x28C6c06298d514Db089934071355E5743bf21d60", "Binance 7", "hot"),
            ("0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549", "Binance 8", "hot"),
            ("0xDFd5293D8e347dFe59E90eFd55b2956a1343963d", "Binance 14", "hot"),
            ("0x56Eddb7aa87536c09CCc2793473599fD21A8b17F", "Binance 15", "hot"),
            ("0xF977814e90dA44bFA03b6295A0616a897441aceC", "Binance 20", "cold"),
            ("0xBE0eB53F46cd790Cd13851d5EFf43D12404d33E8", "Binance 3", "deposit"),
            ("0x3f5CE5FBFe3E9af3971dD833D26bA9b5C936f0bE", "Binance", "hot"),
            ("0xfE9E8709d3215310075d67E3ed32A380CCf451C8", "Binance 1", "cold"),
            ("0x3CbFc97f87F534b42bb58276B7DcaDd6b1E95dEE", "Binance 2", "cold"),
            ("0x4e9ce36e442e55ecd9025b9a6e0d88485d628a67", "Binance 5", "cold"),
        ],
        "solana": [
            ("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9", "Binance Hot", "hot"),
            ("2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S", "Binance Cold", "cold"),
        ],
    },
    "coinbase": {
        "name": "Coinbase",
        "ethereum": [
            ("0x71660c4005BA85C37ccec55d0C4493E66Fe775d3", "Coinbase 1", "hot"),
            ("0x503828976D22510aad0201ac7EC88293211D23Da", "Coinbase 2", "hot"),
            ("0xddfAbCdc4D8fFC41786C8E8E6fF2Cb3A0F8D92A6", "Coinbase 3", "hot"),
            ("0xA090e606E30bD747d4E6245a1517EbE430F0057e", "Coinbase", "deposit"),
        ],
    },
    "kraken": {
        "name": "Kraken",
        "ethereum": [
            ("0x2910543Af39abA0Cd09dBb2D50200b3E800A63D2", "Kraken 3", "hot"),
            ("0x0a869d79a7052C7f1b55a8EbAbbEa3420F0D1E13", "Kraken 4", "hot"),
            ("0xE853c56864A2ebe4576a807D26Fdc4A0adA51919", "Kraken 5", "hot"),
            ("0x267be1C1D684F78cb4F6a176C4911b741E4Ffdc0", "Kraken 2", "hot"),
        ],
    },
    "okx": {
        "name": "OKX",
        "ethereum": [
            ("0x6cC5F688a315f3dC28A7781717a9A798a59fDA7b", "OKX Hot", "hot"),
            ("0xD6216fC19DB775Df9774a6E33526131dA7D19a2c", "OKX", "deposit"),
        ],
    },
    "bybit": {
        "name": "Bybit",
        "ethereum": [
            ("0xf89d7b9c864f589bbF53a82105107622B35EaA40", "Bybit Hot", "hot"),
            ("0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFCF4", "Bybit Cold", "cold"),
        ],
    },
    "kucoin": {
        "name": "KuCoin",
        "ethereum": [
            ("0x2B5634C42055806a59e9107ED44D43c426E58258", "KuCoin 1", "hot"),
            ("0x689C56AEf474Df92D44A1B70850f808488F9769C", "KuCoin 2", "hot"),
        ],
    },
    "gate_io": {
        "name": "Gate.io",
        "ethereum": [
            ("0x0D0707963952f2fBA59dD06f2b425ace40b492Fe", "Gate.io 1", "hot"),
        ],
    },
    "mexc": {
        "name": "MEXC",
        "ethereum": [
            ("0x75e89d5979E4f6Fba9F97c104c2F0AFB3F1dcB88", "MEXC 1", "hot"),
        ],
    },
    "bitget": {
        "name": "Bitget",
        "ethereum": [
            ("0x0639556F03714A74a5fEEaF5736a4A64fF70D206", "Bitget Hot", "hot"),
        ],
    },
    "htx": {
        "name": "HTX (Huobi)",
        "ethereum": [
            ("0xe4818f8fDe0C977A01DA4Fa467365B8B22B725C0", "HTX 1", "hot"),
            ("0x6748F50f686bfbcA6Fe8ad62b22228b87F31ff2b", "HTX 2", "hot"),
        ],
    },
    "gemini": {
        "name": "Gemini",
        "ethereum": [
            ("0xd24400ae8BfEBb18cA49Be86258a3C749cf46853", "Gemini 1", "hot"),
            ("0x5f65f7b609678448494De4C87521CdF6cEf1e932", "Gemini 4", "cold"),
        ],
    },
    "crypto_com": {
        "name": "Crypto.com",
        "ethereum": [
            ("0x6262998Ced04146fA42253a5C0AF90CA02dfd2A3", "Crypto.com 1", "hot"),
            ("0x46340b20830761efd32832A74d7169B29FEB9758", "Crypto.com 2", "hot"),
        ],
    },
}

# ── Solana DEX Programs — NEVER FLAG THESE ───────────────────────────

SOLANA_DEX_PROGRAMS = {
    # Jupiter — the biggest aggregator on Solana
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": ("Jupiter v6", "dex_aggregator"),
    "JUP2jxvXaqu7NQY1GmNF4m1vodw12LVXYxbFL2uJvfo": ("Jupiter v4", "dex_aggregator"),
    # Raydium
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": ("Raydium AMM v4", "dex_amm"),
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK": ("Raydium CLMM", "dex_clmm"),
    "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C": ("Raydium CPMM", "dex_cpmm"),
    # Raydium Authority / Fee Vaults
    "RaydiumAuthority11111111111111111111111111111111": ("Raydium Authority", "dex_authority"),
    "RaydiumFeeVault111111111111111111111111111111111": ("Raydium Fee Vault", "dex_fee_vault"),
    # Orca
    "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": ("Orca Whirlpools", "dex_clmm"),
    "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP": ("Orca v2", "dex_amm"),
    # ⚠️ METEORA — CRITICAL: DLMM pools rebalance frequently. Legitimate protocol activity.
    "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo": ("Meteora DLMM", "dex_clmm"),
    "Eo7WjKq67rjJQSZxS6z3YkapzY3eMj6Xy8X5EQVn5UaB": ("Meteora Pools", "dex_clmm"),
    # Meteora Vaults / Dynamic Vaults
    "MeteoraVault111111111111111111111111111111111111": ("Meteora Vault", "dex_vault"),
    "MeteoraDynamicVault111111111111111111111111111111": ("Meteora Dynamic Vault", "dex_vault"),
    # pump.fun
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": ("pump.fun", "token_launchpad"),
    # pump.fun migration / bonding curve
    "pumpfunMigration111111111111111111111111111111111": ("pump.fun Migration", "token_launchpad"),
    # bonk
    "bonkProgram11111111111111111111111111111111111111": ("BONK Program", "token_launchpad"),
    # bags.fm
    "bagsfmProgram1111111111111111111111111111111111111": ("bags.fm", "token_launchpad"),
    # Jito
    "Jito4APyf642JPZPx3hGc6WWJZnjetVRR3VpVbvzJ4Ud": ("Jito MEV", "mev_infra"),
    # Marinade
    "MarBmsS13i3uR3HFuHo3g4Fh7hMqX9VGNxyRagVtUAVD": ("Marinade", "liquid_staking"),
    # Jupiter DCA
    "DCA265Vj8a9CEuX1eb1LWRnDT7uK6q1xMipnNyatn23M": ("Jupiter DCA", "dca"),
    # Jupiter Limit Order
    "j1o2qRpjcyUwEvwtcfhEQefh773ZgjMVRzG7C1s8zLR": ("Jupiter Limit Order", "limit_order"),
    # Metaplex
    "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s": ("Metaplex Token Metadata", "nft_infra"),
    # Wormhole
    "worm2ZoG2kUd4vFXhvjh93UUH596ayRfgQ2MgjNMTth": ("Wormhole Core", "bridge"),
    "wormDTUJ6AWPNvk59vGQbDvGJmqbDTdgWgAqcLBCgUb": ("Wormhole Token Bridge", "bridge"),
    # Solana System Programs
    "11111111111111111111111111111111": ("System Program", "system"),
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA": ("SPL Token Program", "token_program"),
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL": ("Associated Token Account", "token_program"),
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb": ("Token-2022 Program", "token_program"),
}

# ── EVM DEX & DeFi Contracts — NEVER FLAG THESE ───────────────────────

EVM_KNOWN_CONTRACTS = {
    "ethereum": {
        # Uniswap
        "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D": ("Uniswap V2 Router", "dex_amm"),
        "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f": ("Uniswap V2 Factory", "dex_amm"),
        "0xE592427A0AEce92De3Edee1F18E0157C05861564": ("Uniswap V3 Router", "dex_clmm"),
        "0x1F98431c8aD98523631AE4a59f267346ea31F984": ("Uniswap V3 Factory", "dex_clmm"),
        "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD": (
            "Uniswap Universal Router",
            "dex_aggregator",
        ),
        "0x000000000004444c5dc75cB358380D2e3dE08A90": ("Uniswap V4 Pool Manager", "dex_clmm"),
        # 1inch
        "0x1111111254EEB25477B68fb85Ed929f73A960582": ("1inch V5 Router", "dex_aggregator"),
        "0x111111125421cA6dc452d289314280a0f8842A65": ("1inch V6 Router", "dex_aggregator"),
        # Curve
        "0x0000000022D53366457F9d5E68Ec105046FC4383": ("Curve Address Provider", "dex_stableswap"),
        # Balancer
        "0xBA12222222228d8Ba445958a75a0704d566BF2C8": ("Balancer V2 Vault", "dex_amm"),
        # Aave
        "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2": ("Aave V3 Pool", "lending"),
        # Lido
        "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84": ("Lido stETH", "liquid_staking"),
        "0xae78736Cd615f374D3085123A210448E74Fc6393": ("Rocket Pool rETH", "liquid_staking"),
        # Maker
        "0x5ef30b9986345249bc32d8928B7ee64DE9435E39": ("Maker CDP Manager", "lending"),
        # Wormhole
        "0x98f3c9e6E3fAce36bAAd05FE09d375Ef1464288B": ("Wormhole Core", "bridge"),
        "0x3ee18B2214AFF97000D974cf647E7C347E8fa585": ("Wormhole Token Bridge", "bridge"),
        # LayerZero
        "0x1a44076050125825900e736c501f859c50fE728c": ("LayerZero Endpoint V2", "bridge"),
        # EigenLayer
        "0x858646372CC42E1A627fcE94aa7A7033e7CF075A": ("EigenLayer Strategy Manager", "restaking"),
        # Tornado Cash (flag for compliance, not "suspicious" — it IS a mixer)
        "0xA160cdAB225685dA1d56aa342Ad8841c3b53f291": ("Tornado Cash Router", "mixer"),
        "0xd90e2f925DA726b50C4Ed8D0Fb90Ad053324F31b": ("Tornado Cash 0.1 ETH", "mixer"),
        "0x47CE0C6eD5B0Ce3d3A51fdb1C52DC66a7c3c2936": ("Tornado Cash 1 ETH", "mixer"),
        "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF": ("Tornado Cash 10 ETH", "mixer"),
    },
    "bsc": {
        "0x10ED43C718714eb63d5aA57B78B54704E256024E": ("PancakeSwap V2 Router", "dex_amm"),
        "0x13f4EA83D0bd40E75C8222255bc855a974568Dd4": ("PancakeSwap V3 Router", "dex_clmm"),
    },
    "arbitrum": {
        "0xaBBc5F99639c9B6bCb58544ddf04EFA6802F4064": ("GMX Router", "dex_perpetuals"),
        "0x489ee077994B6658eAfA855C308275EAd8097C4A": ("GMX Vault", "dex_perpetuals"),
    },
    "base": {
        "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43": ("Aerodrome Router", "dex_clmm"),
    },
    "polygon": {
        "0xa5E0829CaCEd8fFDD4De3c43696c57F7D7A678ff": ("QuickSwap Router", "dex_amm"),
    },
}

# ── Market Makers & VC Funds ──────────────────────────────────────────

KNOWN_MARKET_MAKERS = {
    "wintermute": {
        "name": "Wintermute",
        "ethereum": ["0xdbf5E9C5206d0dB70a90108bf936DA60221dC080"],
    },
    "jump_trading": {
        "name": "Jump Trading",
        "ethereum": ["0xf584F8728B874a6a5c7A8d4d387C9aae9172D621"],
    },
    "gsr": {
        "name": "GSR Markets",
        "ethereum": ["0xAea627b93B233Bd3A92bFbf5487948dfC3A0B314"],
    },
    "dwf_labs": {
        "name": "DWF Labs",
        "ethereum": ["0xD9c36fD0efa21103CCbAaaB580F82DaaDb1eE9Bc"],
    },
}

# ── Lookup Cache ──────────────────────────────────────────────────────

_lookup: dict[str, dict[str, Any]] = {}
_solana_programs: dict[str, tuple[str, str]] = {}
_initialized = False


def _init() -> None:
    global _initialized, _lookup, _solana_programs
    if _initialized:
        return

    # Exchange wallets
    for ex_id, ex in EXCHANGE_WALLETS.items():
        for chain in ("ethereum", "solana"):
            for addr, label, wtype in ex.get(chain, []):
                _lookup[f"{chain}:{addr.lower()}"] = {
                    "name": f"{ex['name']} ({label})",
                    "type": f"cex_{wtype}",
                    "confidence": "HIGH",
                    "entity": ex_id,
                }

    # Solana DEX programs
    for prog_id, (name, ptype) in SOLANA_DEX_PROGRAMS.items():
        _solana_programs[prog_id] = (name, ptype)

    # EVM contracts
    for chain, contracts in EVM_KNOWN_CONTRACTS.items():
        for addr, (name, ctype) in contracts.items():
            _lookup[f"{chain}:{addr.lower()}"] = {
                "name": name,
                "type": ctype,
                "confidence": "HIGH",
            }

    # Market makers
    for _mm_id, mm in KNOWN_MARKET_MAKERS.items():
        for chain, addrs in mm.items():
            if chain == "name":
                continue
            for addr in addrs:
                _lookup[f"{chain}:{addr.lower()}"] = {
                    "name": mm["name"],
                    "type": "market_maker",
                    "confidence": "HIGH",
                }

    _initialized = True
    logger.info(f"known_entities: {len(_lookup)} labels, {len(_solana_programs)} Solana programs")


def label_wallet(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Label a wallet. Returns {"name":..., "type":..., "confidence":..., "entity":...}"""
    _init()
    key = f"{chain}:{address.lower()}"
    result = _lookup.get(key)
    if result:
        return result
    # Check Solana program IDs (stored separately from EVM lookup)
    if chain == "solana" and address in _solana_programs:
        name, ptype = _solana_programs[address]
        return {"name": name, "type": ptype, "confidence": "HIGH", "entity": None}
    return {"name": "unknown", "type": "unknown", "confidence": "NONE", "entity": None}


def is_known_protocol(address: str, chain: str = "ethereum") -> bool:
    """True if this is a known legitimate protocol — NEVER flag as suspicious."""
    result = label_wallet(address, chain)
    if result["confidence"] == "HIGH":
        return True
    if chain == "solana":
        _init()
        return address in _solana_programs
    return False


def is_cex_wallet(address: str, chain: str = "ethereum") -> bool:
    """True if this is a known CEX wallet."""
    return bool(label_wallet(address, chain)["type"].startswith("cex_"))


def is_dex_contract(address: str, chain: str = "ethereum") -> bool:
    """True if this is a known DEX router/factory/pool."""
    t = label_wallet(address, chain)["type"]
    return bool(t.startswith("dex_") or t in ("token_launchpad",))


def is_bridge(address: str, chain: str = "ethereum") -> bool:
    """True if this is a known bridge contract."""
    return bool(label_wallet(address, chain)["type"] == "bridge")


def is_lending_protocol(address: str, chain: str = "ethereum") -> bool:
    """True if this is a known lending protocol."""
    return bool(label_wallet(address, chain)["type"] in ("lending", "liquid_staking"))


def is_mixer(address: str, chain: str = "ethereum") -> bool:
    """True if this is a known mixer (Tornado Cash, etc)."""
    return bool(label_wallet(address, chain)["type"] == "mixer")


def is_market_maker(address: str, chain: str = "ethereum") -> bool:
    """True if this is a known market maker."""
    return bool(label_wallet(address, chain)["type"] == "market_maker")


def get_exchange_name(address: str, chain: str = "ethereum") -> str | None:
    """Get CEX name if this is an exchange wallet."""
    result = label_wallet(address, chain)
    if result["type"].startswith("cex_"):
        return str(result.get("entity") or result["name"])
    return None


def get_all_known_for_chain(chain: str) -> dict[str, dict[str, Any]]:
    """Get all known labels for a specific chain."""
    _init()
    return {addr.split(":", 1)[1]: label for addr, label in _lookup.items() if addr.startswith(f"{chain}:")}


_init()

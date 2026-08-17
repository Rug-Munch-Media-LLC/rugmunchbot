# VENDORED 2026-08-17 from rmi-backend/app/chain_registry/constants.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
SENTINEL - Multi-Chain Registry & Configuration (Constants)
============================================================
Shared types — ChainFamily enum, ChainConfig dataclass, and
convenience functions used across all chain-specific modules.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum


class ChainFamily(Enum):
    """Blockchain family classification."""

    SOLANA = "solana"  # Solana SPL tokens
    EVM = "evm"  # Ethereum Virtual Machine chains
    COSMOS = "cosmos"  # Not yet supported
    TRON = "tron"  # TRON blockchain


@dataclass
class ChainConfig:
    """Full configuration for a supported blockchain."""

    name: str  # Short name (e.g. "ethereum", "base")
    chain_id: str  # Chain ID ("1", "8453", "solana")
    family: ChainFamily = ChainFamily.EVM
    display_name: str = ""  # Human name ("Ethereum", "Base")

    # RPC endpoints (primary, fallback, public)
    rpc_endpoints: list[str] = field(default_factory=list)

    # Block explorer API
    explorer_url: str = ""
    explorer_api_url: str = ""
    explorer_api_key_env: str = ""  # Env var for API key

    # Native token
    native_token_symbol: str = "ETH"  # noqa: S105 — config default
    native_token_decimals: int = 18

    # DEX data sources (DexScreener supports all)
    dexscreener_chain_id: str = ""

    # Known lockers / factory addresses
    known_lockers: dict[str, str] = field(default_factory=dict)

    # CEX hot wallets for this chain
    cex_hot_wallets: dict[str, list[str]] = field(default_factory=dict)

    # Block explorer API key env var
    api_key_env: str = ""

    def get_explorer_api_key(self) -> str:
        """Get the block explorer API key from environment."""
        return os.getenv(self.api_key_env, "")

    @property
    def is_solana(self) -> bool:
        return self.family == ChainFamily.SOLANA

    @property
    def is_evm(self) -> bool:
        return self.family == ChainFamily.EVM


# ─── Chain Registry (aggregated from chain-specific modules) ───────

CHAINS: dict[str, ChainConfig] = {}


def register(config: ChainConfig) -> None:
    """Register a chain config in the global CHAINS dict."""
    CHAINS[config.name] = config


# ─── Convenience functions ─────────────────────────────────────────


def get_chain(chain_name: str) -> ChainConfig | None:
    """Get chain config by name (case-insensitive)."""
    return CHAINS.get(chain_name.lower())


def get_chain_family(chain_name: str) -> ChainFamily:
    """Determine if a chain is Solana, EVM, or other."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.family if cfg else ChainFamily.EVM


def is_solana(chain_name: str) -> bool:
    return get_chain_family(chain_name) == ChainFamily.SOLANA


def is_evm(chain_name: str) -> bool:
    return get_chain_family(chain_name) == ChainFamily.EVM


def resolve_chain_id(chain_name: str) -> str:
    """Resolve chain name to numeric or canonical ID."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.chain_id if cfg else chain_name


def list_chains() -> list[str]:
    """List all supported chain names."""
    return list(CHAINS.keys())


def get_chain_display(chain_name: str) -> str:
    """Get human-readable chain display name."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.display_name if cfg else chain_name.title()


def get_explorer_url(chain_name: str, address: str, tx: str = "") -> str:
    """Build explorer URL for an address or transaction."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return ""
    if tx:
        return f"{cfg.explorer_url}/tx/{tx}"
    if cfg.is_solana:
        return f"{cfg.explorer_url}/account/{address}"
    return f"{cfg.explorer_url}/address/{address}"


def get_rpc_urls(chain_name: str) -> list[str]:
    """Get list of RPC URLs for a chain (with env vars resolved)."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return []
    urls = []
    for url in cfg.rpc_endpoints:
        # Resolve ${VAR} references from environment
        resolved = re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(str(m.group(1)), str(m.group(1))), url)
        urls.append(resolved)
    return urls


def get_explorer_api_config(chain_name: str) -> tuple[str, str]:
    """Get (api_url, api_key) for a chain's block explorer."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return ("", "")
    api_key = ""
    if cfg.api_key_env:
        api_key = os.getenv(cfg.api_key_env, "")
    return (cfg.explorer_api_url, api_key)


def get_cex_wallets(chain_name: str) -> dict[str, list[str]]:
    """Get known CEX hot wallets for a chain."""
    cfg = CHAINS.get(chain_name.lower())
    return cfg.cex_hot_wallets if cfg else {}


def get_known_lockers(chain_name: str) -> dict[str, str]:
    """Get known liquidity locker contracts for a chain (keys are lowercase-normalized)."""
    cfg = CHAINS.get(chain_name.lower())
    if not cfg:
        return {}
    return {k.lower(): v for k, v in cfg.known_lockers.items()}

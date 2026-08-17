# VENDORED 2026-08-17 from rmi-backend/app/chain_registry/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
SENTINEL - Multi-Chain Registry & Configuration
================================================
Centralized chain definitions, RPC endpoints, explorer APIs,
and data source configurations for all supported chains.

This package was split from the original chain_registry.py monolith.
See the individual modules for chain-specific configs.
"""

# Import constants and types first (no circular deps)
# Import chain-specific modules to trigger register() calls
from rugmunchbot._vendor.chain_registry import (
    evm,
    solana,
    tron,
)
from rugmunchbot._vendor.chain_registry.constants import (
    CHAINS,
    ChainConfig,
    ChainFamily,
    get_cex_wallets,
    get_chain,
    get_chain_display,
    get_chain_family,
    get_explorer_api_config,
    get_explorer_url,
    get_known_lockers,
    get_rpc_urls,
    is_evm,
    is_solana,
    list_chains,
    resolve_chain_id,
)

__all__ = [
    "CHAINS",
    "ChainConfig",
    "ChainFamily",
    "evm",
    "get_cex_wallets",
    "get_chain",
    "get_chain_display",
    "get_chain_family",
    "get_explorer_api_config",
    "get_explorer_url",
    "get_known_lockers",
    "get_rpc_urls",
    "is_evm",
    "is_solana",
    "list_chains",
    "resolve_chain_id",
    "solana",
    "tron",
]

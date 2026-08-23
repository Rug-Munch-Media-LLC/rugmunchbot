# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/launchpad_registry.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Launchpad Registry — detect token launch source (pump.fun, bags.fun, etc.)
and enrich deployer data with launchpad context.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger("rmi.launchpad")

# ── Known launchpad factory/program addresses ──
LAUNCHPAD_REGISTRY = {
    "solana": {
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": {
            "name": "pump.fun",
            "type": "bonding_curve",
            "url": "https://pump.fun",
        },
        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMefXA": {
            "name": "pump.fun AMM",
            "type": "amm",
            "url": "https://pump.fun",
        },
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8": {
            "name": "Raydium AMM",
            "type": "amm",
            "url": "https://raydium.io",
        },
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc": {
            "name": "Orca",
            "type": "dex",
            "url": "https://orca.so",
        },
        "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4": {
            "name": "Jupiter",
            "type": "aggregator",
            "url": "https://jup.ag",
        },
    },
    "ethereum": {
        "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984": {
            "name": "Uniswap",
            "type": "dex",
            "url": "https://uniswap.org",
        },
    },
    "bsc": {
        "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73": {
            "name": "PancakeSwap",
            "type": "dex",
            "url": "https://pancakeswap.finance",
        },
    },
}


def detect_launchpad(deployer_address: str, chain: str) -> dict[str, Any] | None:
    """Check if a deployer address is a known launchpad/factory contract."""
    if not deployer_address:
        return None
    chain_registry = LAUNCHPAD_REGISTRY.get(chain, {})
    addr_lower = deployer_address.lower()
    for factory_addr, info in chain_registry.items():
        if factory_addr.lower() == addr_lower:
            return info
    return None


def get_launchpad_name(deployer_address: str, chain: str) -> str | None:
    """Get the launchpad name for a deployer address, or None."""
    info = detect_launchpad(deployer_address, chain)
    return info.get("name") if info else None


def is_launchpad(deployer_address: str, chain: str) -> bool:
    """Check if a deployer address is a known launchpad/factory."""
    return detect_launchpad(deployer_address, chain) is not None

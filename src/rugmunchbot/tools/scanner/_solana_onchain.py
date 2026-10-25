"""_solana_onchain.py - import surface preserved; implementation split into submodules."""
from __future__ import annotations
from .solanaonchain_p1 import logger, SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM, _T22_EXTENSION_NAMES, _CORE_BUDGET_S, _BUNDLE_BUDGET_S, _BUNDLE_MAX_TXS, _BUNDLE_TX_CONCURRENCY, _BUNDLE_MIN_WALLETS_PER_SLOT, _router, _rpc, _rpc_rotating, _encode_pubkey, _parse_mint_layout, _CACHE_TTL_S, _cache, _cache_get, _cache_set, resolve_mint_facts  # noqa: F401
from .solanaonchain_p2 import _birdeye_holder_count, resolve_holder_stats, resolve_deployer, _buyers_from_tx  # noqa: F401
from .solanaonchain_p3 import detect_same_slot_bundle  # noqa: F401
from .solanaonchain_p4 import enrich_scan_solana  # noqa: F401

__all__ = [
    "detect_same_slot_bundle",
    "enrich_scan_solana",
    "resolve_deployer",
    "resolve_holder_stats",
    "resolve_mint_facts",
]

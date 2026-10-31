"""_solana_onchain.py - import surface preserved; implementation split into submodules."""

from __future__ import annotations

from .solanaonchain_p1 import (  # noqa: F401
    _BUNDLE_BUDGET_S,
    _BUNDLE_MAX_TXS,
    _BUNDLE_MIN_WALLETS_PER_SLOT,
    _BUNDLE_TX_CONCURRENCY,
    _CORE_BUDGET_S,
    _T22_EXTENSION_NAMES,
    SPL_TOKEN_PROGRAM,
    TOKEN_2022_PROGRAM,
    _encode_pubkey,
    _parse_mint_layout,
    _router,
    _rpc,
    _rpc_rotating,
    logger,
    resolve_mint_facts,
)
from .solanaonchain_p2 import (  # noqa: F401
    _birdeye_holder_count,
    _buyers_from_tx,
    resolve_deployer,
    resolve_holder_stats,
)
from .solanaonchain_p3 import detect_same_slot_bundle
from .solanaonchain_p4 import enrich_scan_solana
from .solanaonchain_p5 import _CACHE_TTL_S, _cache, _cache_get, _cache_set  # noqa: F401

__all__ = [
    "detect_same_slot_bundle",
    "enrich_scan_solana",
    "resolve_deployer",
    "resolve_holder_stats",
    "resolve_mint_facts",
]

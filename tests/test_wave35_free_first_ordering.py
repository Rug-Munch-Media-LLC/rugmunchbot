"""Wave 35 — free-first RPC ordering invariant (canonical router).

'Free data first always': within every chain pool, keyless publics must
precede keyed (freemium) templates. Position feeds the router's scoring
penalty (+i), so ordering IS policy here, not cosmetics.
"""

from __future__ import annotations

from rugmunchbot._vendor.scanner_engines.rpc_router import (
    EVM_RPC_ENDPOINTS,
    SOLANA_RPC_ENDPOINTS,
)


def _assert_free_first(urls: list[str], label: str) -> None:
    seen_keyed = False
    for url in urls:
        if "${" in url:
            seen_keyed = True
        else:
            msg = f"{label}: free-after-keyed ordering violation: {url}"
            assert not seen_keyed, msg


def test_evm_pools_free_first() -> None:
    for chain, urls in EVM_RPC_ENDPOINTS.items():
        _assert_free_first(urls, chain)


def test_solana_pool_free_first() -> None:
    _assert_free_first(SOLANA_RPC_ENDPOINTS, "solana")


def test_tier_split_respects_ordering() -> None:
    """The tier machinery must see the same story: with no keys set the
    available pool is exactly the free prefix of each registry."""
    from rugmunchbot._vendor.scanner_engines.rpc_router import RpcRouter

    r = RpcRouter()
    for chain in ("ethereum", "bsc", "polygon"):
        free, _fremium = r._split_tiers(chain)
        assert free, f"{chain}: free pool empty"
        assert all("${" not in u for u in free), f"{chain}: placeholder leaked"
        # every resolved free URL appears in the registry BEFORE any keyed one
        src_urls = EVM_RPC_ENDPOINTS[chain]
        first_keyed_idx = next((i for i, u in enumerate(src_urls) if "${" in u), len(src_urls))
        for u in free:
            matches = [i for i, s in enumerate(src_urls) if u in s]
            msg = f"{chain}: {u} resolves after the keyed block"
            assert matches, msg
            assert matches[0] < first_keyed_idx, msg

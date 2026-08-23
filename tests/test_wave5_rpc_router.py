"""Test wave 5 — RPC router endpoint inventory + health model.

Offline contracts only: no network calls. Selection/health behavior is
exercised through the router's reporting surfaces.
"""

from __future__ import annotations

from rugmunchbot._vendor.scanner_engines.rpc_router import (
    EVM_RPC_ENDPOINTS,
    SOLANA_RPC_ENDPOINTS,
    EndpointHealth,
    get_rpc_router,
)


def test_get_rpc_router_is_singleton() -> None:
    assert get_rpc_router() is get_rpc_router()


def test_endpoint_inventory_nonempty() -> None:
    assert len(SOLANA_RPC_ENDPOINTS) >= 1
    # Entries are either full URLs or ${ENV_VAR} templates (resolved lazily;
    # templates whose key is unset are dropped before any network I/O).
    assert all(
        isinstance(u, str) and (u.startswith("http") or "${" in u) for u in SOLANA_RPC_ENDPOINTS
    )
    assert len(EVM_RPC_ENDPOINTS) >= 1


def test_endpoint_health_defaults() -> None:
    h = EndpointHealth(url="https://rpc.test", chain="solana")
    assert h.consecutive_failures == 0
    assert h.is_dead is False
    assert h.total_calls == 0
    assert h.COOLDOWN_SECONDS == 60
    assert h.MAX_CONSECUTIVE_FAILURES == 3


def test_get_available_endpoints_solana() -> None:
    router = get_rpc_router()
    available = router.get_available_endpoints("solana")
    assert isinstance(available, list)
    # Tier semantics: without API keys set, keyed (freemium) endpoints are
    # excluded entirely — availability == the free pool, never a broken
    # half-resolved URL.
    for url, health in available:
        assert url.startswith("http")
        assert "${" not in url  # no unresolved key placeholders leak through
        assert health.url == url
        assert health.chain == "solana"
        assert health.tier == "free"
        assert health.is_dead is False


def test_get_health_report_reflects_inventory() -> None:
    router = get_rpc_router()
    report = router.get_health_report()
    assert isinstance(report, dict)
    assert len(report) >= 1

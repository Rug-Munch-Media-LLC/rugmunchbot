"""Test wave 31 — RPC router freemium tier: escalation, quota, rotation.

Offline contracts only (no network). Verifies the tier machinery added
2026-08-23:

  * keyed endpoints without credentials are excluded (never half-resolved)
  * free tier is served exclusively while it has living endpoints
  * free pool exhausted → escalation into the freemium keyed pool,
    least-quota-used provider first
  * freemium calls are counted against a per-host hourly quota ledger
  * round-robin spread rotates the top-3 healthy endpoints across calls
"""

from __future__ import annotations

import time

from rugmunchbot._vendor.scanner_engines.rpc_router import (
    EVM_RPC_ENDPOINTS,
    SOLANA_RPC_ENDPOINTS,
    EndpointHealth,
    RpcRouter,
)


def _fresh_router(monkeypatch=None) -> RpcRouter:
    """Isolated router (not the global singleton) for hermetic tests.

    When ``monkeypatch`` is given, all keyed-endpoint env vars are cleared
    so tier-split assertions never depend on the ambient environment
    (operator keys ARE set in some shells since 2026-08-23).
    """
    if monkeypatch is not None:
        for var in (
            "DRPC_API_KEY",
            "ANKR_API_KEY",
            "ALCHEMY_KEY",
            "HELIUS_API_KEY",
            "QUICKNODE_ETH_HTTP",
            "QUICKNODE_BSC_HTTP",
            "QUICKNODE_POLYGON_HTTP",
            "QUICKNODE_SOLANA_HTTP",
            "NOWNODES_API_KEY",
            "CHAINSTACK_SOLANA_URL",
            "GETBLOCK_SOLANA_URL",
            "GETBLOCK_BSC_URL",
        ):
            monkeypatch.delenv(var, raising=False)
    return RpcRouter()


def _kill_free_tier(router: RpcRouter, chain: str) -> None:
    """Mark every free-tier endpoint dead so escalation must kick in."""
    free, _freemium = router._split_tiers(chain)
    assert free, f"chain {chain} has no free tier to kill"
    for url in free:
        health = router._get_health(chain, url, tier="free")
        health.consecutive_failures = health.MAX_CONSECUTIVE_FAILURES
        health.is_dead = True
        health.dead_until = time.time() + 9999


# ── Registry integrity ────────────────────────────────────────────────


def test_registry_has_freemium_depth() -> None:
    """Every major chain carries keyed fallbacks for exhaustion scenarios."""
    for chain in ("ethereum", "bsc", "polygon", "arbitrum", "base"):
        templates = EVM_RPC_ENDPOINTS[chain]
        keyed = [u for u in templates if "${" in u]
        assert keyed, f"{chain} has no freemium (keyed) endpoints"
    assert any("${" in u for u in SOLANA_RPC_ENDPOINTS), "solana lacks freemium depth"


def test_missing_key_excludes_endpoint_entirely(monkeypatch) -> None:
    """Unset key env vars drop the endpoint — no broken URLs ever surface."""
    router = _fresh_router(monkeypatch)
    free, fremium = router._split_tiers("ethereum")
    joined = free + fremium
    assert all("${" not in u for u in joined), "unresolved placeholder leaked"
    # DRPC_API_KEY is not set in the test env → its lb.drpc.org entry is gone.
    assert not any("dkey=" in u for u in joined)
    assert not any(
        u.endswith("/v2/") or (u.rstrip("/").endswith("eth") and "ankr.com/eth/" in u)
        for u in joined
    )


def test_resolved_key_unlocks_freemium(monkeypatch) -> None:
    monkeypatch.setenv("DRPC_API_KEY", "test-key-123")
    router = _fresh_router()
    _free, fremium = router._split_tiers("ethereum")
    assert any("dkey=test-key-123" in u for u in fremium)


# ── Tier selection ────────────────────────────────────────────────────


def test_free_tier_served_exclusively_while_alive(monkeypatch) -> None:
    router = _fresh_router(monkeypatch)
    monkey_keys = False  # no keys → only free exists anyway; still assert tiers
    available = router.get_available_endpoints("ethereum")
    assert available, "ethereum free pool must never be empty"
    assert all(h.tier == "free" for _, h in available)
    assert monkey_keys is False


def test_escalation_only_when_free_exhausted(monkeypatch) -> None:
    # Hermetic: strip ambient provider keys so escalation has nothing to
    # escalate into (the dev box legitimately exports real keys now).
    for var in (
        "ALCHEMY_KEY",
        "ALCHEMY_KEY_2",
        "ANKR_API_KEY",
        "DRPC_API_KEY",
        "INFURA_PROJECT_ID",
        "QUICKNODE_ETH_HTTP",
    ):
        monkeypatch.delenv(var, raising=False)
    router = _fresh_router(monkeypatch)
    _kill_free_tier(router, "ethereum")
    # No keys set → nothing to escalate into → empty, no crash.
    assert router.get_available_endpoints("ethereum") == []
    assert router.freemium_escalations == 0


def test_escalation_picks_freemium_and_counts(monkeypatch) -> None:
    monkeypatch.setenv("DRPC_API_KEY", "esc-key")
    router = _fresh_router()
    _kill_free_tier(router, "ethereum")
    available = router.get_available_endpoints("ethereum")
    assert available, "escalation must surface keyed providers"
    assert all(h.tier == "freemium" for _, h in available)
    assert any("esc-key" in u for u, _ in available)
    assert router.freemium_escalations >= 1


def test_escalation_log_is_throttled() -> None:
    router = _fresh_router()
    for _ in range(10):
        router._record_escalation("ethereum")
    assert router.freemium_escalations == 10  # counter always climbs…
    # …but the log timestamp gate keeps spam down (single chain window).
    assert len(router._last_escalation_log) == 1


# ── Quota ledger ──────────────────────────────────────────────────────


def test_quota_ledger_records_and_prunes() -> None:
    router = _fresh_router()
    url = "https://lb.drpc.org/ogrpc?network=ethereum&dkey=k"
    router._record_quota(url)
    router._record_quota(url)
    assert router._quota_usage(url) == 2
    # Age out the window → usage decays to zero.
    router._quota["lb.drpc.org"] = [time.time() - 7200]
    assert router._quota_usage(url) == 0


def test_escalation_orders_by_least_quota_used(monkeypatch) -> None:
    monkeypatch.setenv("DRPC_API_KEY", "q-key")
    monkeypatch.setenv("ANKR_API_KEY", "q-ankr")
    router = _fresh_router()
    _kill_free_tier(router, "ethereum")
    # Pre-burn the dRPC host's quota so Ankr must sort first.
    drpc_url = next(u for u in router._split_tiers("ethereum")[1] if "drpc" in u)
    for _ in range(5):
        router._record_quota(drpc_url)
    available = router.get_available_endpoints("ethereum")
    assert available[0][0] != drpc_url or router._quota_usage(
        available[0][0]
    ) <= router._quota_usage(available[-1][0])


# ── Round-robin spread ────────────────────────────────────────────────


def test_round_robin_spreads_top3() -> None:
    router = _fresh_router()
    ranked = [
        (f"https://e{i}.example/{i}", EndpointHealth(url=f"https://e{i}.example/{i}", chain="test"))
        for i in range(5)
    ]
    firsts = []
    for _ in range(6):
        spread = router._spread(list(ranked), "unittest")
        firsts.append(spread[0][0])
    # With 5 healthy endpoints the top-3 rotate; tail order is stable.
    assert len(set(firsts)) == 3, f"expected 3 rotating heads, got {set(firsts)}"
    # Tail (ranks 4-5) always trails the rotation window.
    spread = router._spread(list(ranked), "unittest")
    assert [u for u, _ in spread[3:]] == [ranked[3][0], ranked[4][0]]


def test_health_report_carries_tier_and_escalation_counters() -> None:
    router = _fresh_router()
    router.freemium_escalations = 7
    report = router.get_health_report()
    assert report["summary"]["freemium_escalations"] == 7
    assert "freemium_calls_last_hour" in report["summary"]

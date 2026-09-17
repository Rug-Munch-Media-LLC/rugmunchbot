"""Smoke + unit tests for tools/scanner/_local_v2 (import bridge + pure helpers).

The module is the local unified-scanner execution path: it stubs backend-only
modules in ``sys.modules`` so the vendored scanner imports in-process. These
tests cover the bridge + the pure pair/plumbing helpers — no network.
"""

import sys

from rugmunchbot.tools.scanner import _local_v2 as v2


def test_install_import_bridge_is_idempotent():
    v2.install_import_bridge()
    v2.install_import_bridge()  # second call is a no-op
    assert v2._INSTALLED is True


def test_stub_module_registers_and_reuses():
    name = "rugmunchbot_test_stub_module_xyz"
    mod = v2._stub_module(name)
    assert mod is v2._stub_module(name)
    assert sys.modules[name] is mod


def test_best_pair_picks_highest_liquidity():
    pairs = [
        {"liquidity": {"usd": 1000}, "pairAddress": "a"},
        {"liquidity": {"usd": 50000}, "pairAddress": "b"},
        {"liquidity": {"usd": 200}, "pairAddress": "c"},
    ]
    best = v2._best_pair(pairs)
    assert best is not None
    assert best["pairAddress"] == "b"


def test_best_pair_empty_and_degrades_to_first():
    assert v2._best_pair([]) is None
    # Non-numeric liquidity can't be compared — the first pair is the fallback.
    odd = [{"liquidity": {"usd": "abc"}, "pairAddress": "x"}]
    assert v2._best_pair(odd) is odd[0]


def test_normalize_dex_pair_swaps_quote_side():
    token = "0xToken"
    pair = {
        "baseToken": {"address": "0xOther"},
        "quoteToken": {"address": "0xToken"},
        "priceUsd": "10",
        "priceNative": "2",
    }
    out = v2._normalize_dex_pair(pair, token)
    assert out["baseToken"]["address"] == "0xToken"
    assert out["quoteToken"]["address"] == "0xOther"
    assert out["priceUsd"] == 5.0
    assert out["priceNative"] == 0.5


def test_normalize_dex_pair_noop_when_base_matches():
    pair = {"baseToken": {"address": "0xT"}, "quoteToken": {"address": "0xQ"}}
    assert v2._normalize_dex_pair(pair, "0xT") is pair


def test_normalize_dex_pair_survives_bad_types():
    pair = {"baseToken": "nope", "quoteToken": None}
    assert v2._normalize_dex_pair(pair, "0xT") is pair

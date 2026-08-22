"""Test wave 23 — fetcher lanes over stubbed transports.

Contracts read from source (2026-08-22). Transports are stubbed at the
module boundary (backend_json_get / http_get_json / resolve_with_fallback);
the cascade logic under test (own-data-first, fail-open degradation,
normalization) runs for real.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from rugmunchbot.tools._client import BackendApiError
from rugmunchbot.tools.trend_algo import _candidates as cand

if TYPE_CHECKING:
    import pytest

# ── trend_algo candidate collectors ──────────────────────────────────────────


def test_scanner_candidates_normalizes_and_skips_addressless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(path: str, **kw: Any) -> list[dict[str, Any]]:
        assert path == "/api/v1/scanner/recent"
        return [
            {"token_address": "0xAAA", "chain": "SOL", "token_symbol": "tok", "risk_score": 88},
            {"chain": "eth"},  # no address -> dropped
            {"token_address": "0xBBB", "is_honeypot": True},
        ]

    monkeypatch.setattr(cand, "backend_json_get_any", fake_get)
    out = asyncio.run(cand._scanner_candidates())
    assert out[0] == {
        "address": "0xAAA",
        "chain": "solana",
        "symbol": "tok",
        "risk_score": 88.0,
        "is_honeypot": False,
        "is_rug_pull": False,
        "scanned_at": None,
        "source": "own:scanner/recent",
    }
    assert out[1]["address"] == "0xBBB"
    assert out[1]["is_honeypot"] is True


def test_scanner_candidates_degrades_on_backend_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(path: str, **kw: Any) -> None:
        raise BackendApiError("dead", status_code=502)

    monkeypatch.setattr(cand, "backend_json_get_any", boom)
    assert asyncio.run(cand._scanner_candidates()) == []


def test_trending_candidates_filters_all_chain_and_addressless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        assert kw["params"] == {"limit": 50}
        return {
            "items": [
                {"address": "0x1", "chain": "Base", "symbol": "A"},
                {"address": "0x2", "chain": "all"},  # cross-chain row -> skipped
                {"symbol": "noaddr"},  # no address -> skipped
            ]
        }

    monkeypatch.setattr(cand, "backend_json_get", fake_get)
    out = asyncio.run(cand._trending_candidates())
    assert out == [
        {"address": "0x1", "chain": "base", "symbol": "A", "source": "own:market/trending"}
    ]


def test_launch_candidates_keep_own_clickhouse_rows_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get(path: str, **kw: Any) -> dict[str, Any]:
        return {
            "items": [
                {"token_address": "0xOK", "source": "own_clickhouse", "launch_timestamp": 123},
                {"token_address": "0xDX", "source": "dexscreener_boost"},  # filtered
            ]
        }

    monkeypatch.setattr(cand, "backend_json_get", fake_get)
    out = asyncio.run(cand._launch_candidates())
    assert out == [
        {
            "address": "0xOK",
            "chain": "all",  # _normalize_chain("") -> "all"
            "symbol": "",
            "launch_timestamp": 123,
            "source": "own:clickhouse:deployer_events",
        }
    ]


def test_dex_profile_candidates_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(url: str, **kw: Any) -> list[dict[str, str]]:
        return [{"tokenAddress": "0xP", "chainId": "ethereum"}, "junk"]

    monkeypatch.setattr(cand, "http_get_any", fake_get)
    out = asyncio.run(cand._dex_profile_candidates())
    assert out == [
        {
            "address": "0xP",
            "chain": "ethereum",
            "symbol": "",
            "source": "dexscreener:token-profiles",
        }
    ]

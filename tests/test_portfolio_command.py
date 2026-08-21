"""Portfolio command — honest backend contract (audit 2026-08-21 §F).

The backend answers genuine no-data with 200+``{"data": null,
"reason": "no_data"}`` and real failures with non-200. The command must:

* render the no-data contract as a GRACEFUL empty state (not an error),
* surface non-200 / network failures LOUDLY (never "$0.00 / no holdings"),
* parse the enriched shape (``balance_usd`` / ``top_tokens``) AND generic
  ``{"tokens": [...]}`` / ``{"balances": [...]}`` shapes,
* report raw provider passthroughs (Alchemy ``result.tokenBalances`` —
  no USD prices) as unvalued holdings instead of a fabricated $0.00.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.commands import portfolio as pf
from rugmunchbot.tools._client import BackendApiError

EVM_ADDR = "0x28C6c06298d514Db089934071355E5743bf21d60"


# ── Parse layer: every backend shape is handled honestly ─────────────────


def test_parse_no_data_contract_is_graceful_empty_state():
    body = {
        "data": None,
        "reason": "no_data",
        "sources_tried": ["alchemy_balances", "moralis_tokens"],
        "address": EVM_ADDR,
        "data_type": "wallet_tokens",
    }
    result = pf._parse_portfolio_body(body)
    assert result["no_data"] is True
    assert result["error"] is None
    assert result["balance_usd"] == 0.0
    assert result["top_tokens"] == []


def test_parse_enriched_shape_reads_balance_and_tokens():
    body = {
        "balance_usd": 1234.5,
        "tx_count": 42,
        "top_tokens": [
            {"symbol": "USDC", "balance": 1000.0, "value_usd": 1000.0, "mint": "0xa"},
            {"symbol": "PEPE", "balance": 5.0, "value_usd": 234.5, "mint": "0xb"},
        ],
    }
    result = pf._parse_portfolio_body(body)
    assert result["no_data"] is False
    assert result["balance_usd"] == pytest.approx(1234.5)
    assert result["tx_count"] == 42
    assert [t["symbol"] for t in result["top_tokens"]] == ["USDC", "PEPE"]


def test_parse_tokens_list_shape_normalizes_and_sums_values():
    body = {
        "tokens": [
            {"symbol": "WETH", "amount": 2.0, "value_usd": 6000.0, "address": "0xc"},
            {"symbol": "DAI", "amount": 500.0, "value_usd": 500.0, "address": "0xd"},
        ]
    }
    result = pf._parse_portfolio_body(body)
    assert len(result["top_tokens"]) == 2
    assert result["balance_usd"] == pytest.approx(6500.0)
    assert result["unvalued_tokens"] == 0


def test_parse_balances_list_shape_without_values_is_unvalued_not_zero():
    # A holdings list WITHOUT usd values must not fabricate $0.00 totals.
    body = {"balances": [{"symbol": "FOO", "amount": 1.0}, {"symbol": "BAR", "amount": 2.0}]}
    result = pf._parse_portfolio_body(body)
    assert len(result["top_tokens"]) == 2
    assert result["unvalued_tokens"] == 2
    assert result["balance_usd"] == 0.0


def test_parse_raw_alchemy_jsonrpc_shape_counts_unvalued():
    # LIVE shape of /api/v1/databus/wallet/{addr}/tokens (probed 2026-08-21):
    # raw Alchemy passthrough — hex balances, no symbols, no USD prices.
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "address": EVM_ADDR.lower(),
            "tokenBalances": [
                {"contractAddress": "0x1", "tokenBalance": "0x0de0b6b3a7640000"},
                {"contractAddress": "0x2", "tokenBalance": "0x0"},
            ],
        },
    }
    result = pf._parse_portfolio_body(body)
    assert result["unvalued_tokens"] == 2
    assert result["balance_usd"] == 0.0
    assert result["top_tokens"] == []
    assert result["no_data"] is False
    assert result["error"] is None


# ── Fetch layer: loud on real failures, graceful on honest no-data ──────


@pytest.mark.asyncio
async def test_fetch_backend_error_is_loud_not_silent(monkeypatch):
    async def _boom(path: str, **kw: Any) -> dict[str, Any]:
        raise BackendApiError(f"HTTP 502 from {path}", status_code=502, url=path)

    monkeypatch.setattr(pf, "backend_json_get", _boom)
    result = await pf._fetch_portfolio_from_backend(EVM_ADDR)
    assert result["error"] is not None
    assert "502" in result["error"]
    assert result["no_data"] is False


@pytest.mark.asyncio
async def test_fetch_no_data_contract_graceful(monkeypatch):
    async def _ok(path: str, **kw: Any) -> dict[str, Any]:
        return {"data": None, "reason": "no_data"}

    monkeypatch.setattr(pf, "backend_json_get", _ok)
    result = await pf._fetch_portfolio_from_backend(EVM_ADDR)
    assert result["no_data"] is True
    assert result["error"] is None


# ── Command layer: what the user actually sees ───────────────────────────


class FakeChat:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def send_action(self, action: str) -> None:
        self.actions.append(action)


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kw: Any) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, args: list[str]) -> None:
        self.message = FakeMessage()
        self.message.chat = FakeChat()  # type: ignore[attr-defined]
        self._args = args


class FakeCtx:
    def __init__(self, args: list[str]) -> None:
        self.args = args


async def _run_cmd(monkeypatch, body_or_exc: Any) -> list[str]:
    """Run /portfolio with a stubbed backend; return the reply texts."""

    async def _stub(path: str, **kw: Any) -> dict[str, Any]:
        if isinstance(body_or_exc, Exception):
            raise body_or_exc
        return body_or_exc

    monkeypatch.setattr(pf, "backend_json_get", _stub)
    update = FakeUpdate([EVM_ADDR])
    await pf.cmd_portfolio(update, FakeCtx([EVM_ADDR]))
    return update.message.replies  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cmd_portfolio_no_data_shows_graceful_empty_state(monkeypatch):
    replies = await _run_cmd(monkeypatch, {"data": None, "reason": "no_data"})
    assert len(replies) == 1
    assert "No token holdings found" in replies[0]
    # Honest empty state — NOT an error, NOT a fabricated $0.00 line.
    assert "⚠️" not in replies[0]
    assert "$0.0000" not in replies[0]


@pytest.mark.asyncio
async def test_cmd_portfolio_backend_502_is_loud(monkeypatch):
    replies = await _run_cmd(
        monkeypatch, BackendApiError("HTTP 502 from x", status_code=502, url="x")
    )
    assert len(replies) == 1
    assert "502" in replies[0]
    assert "Could not load holdings" in replies[0]
    # The old silent path rendered a $0.00 portfolio card — must not return.
    assert "$0.0000" not in replies[0]
    assert "No token holdings" not in replies[0]


@pytest.mark.asyncio
async def test_cmd_portfolio_raw_alchemy_shape_reports_unvalued(monkeypatch):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "address": EVM_ADDR.lower(),
            "tokenBalances": [{"contractAddress": "0x1", "tokenBalance": "0x1"}],
        },
    }
    replies = await _run_cmd(monkeypatch, body)
    assert len(replies) == 1
    assert "1 token holdings found" in replies[0]
    assert "valuation is unavailable" in replies[0]
    assert "$0.0000" not in replies[0]


@pytest.mark.asyncio
async def test_cmd_portfolio_enriched_shape_renders_table(monkeypatch):
    body = {
        "balance_usd": 1500.0,
        "tx_count": 7,
        "top_tokens": [{"symbol": "USDC", "balance": 1500.0, "value_usd": 1500.0}],
    }
    replies = await _run_cmd(monkeypatch, body)
    assert len(replies) == 1
    assert "$1,500.00" in replies[0]
    assert "USDC" in replies[0]

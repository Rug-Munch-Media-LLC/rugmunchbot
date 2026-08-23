"""Tests for the bot-native Solana on-chain resolver + token-scoped smart money.

Covers tools/scanner/_solana_onchain.py and tools/scanner/_smart_money_token.py.
All RPC/HTTP boundaries are mocked — no network anywhere. Payload shapes were
probed against the implementation before asserting (oracle policy).
"""

from __future__ import annotations

import base64
from typing import Any

import base58
import pytest

from rugmunchbot.tools.scanner import _smart_money_token as smt, _solana_onchain as so

# ── Helpers ──────────────────────────────────────────────────────────────

WALLET_A = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"
WALLET_B = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
WALLET_C = "GThUX1Atko4tqhN2NaiTazWSeFWMuiUvfFnyJyUghFMz"


def _pubkey32(addr: str) -> bytes:
    return base58.b58decode(addr)


def _mint_layout(
    *,
    mint_authority: str | None,
    freeze_authority: str | None = None,
    supply: int = 1_000_000_000,
    decimals: int = 6,
    extensions: list[tuple[int, bytes]] | None = None,
) -> bytes:
    """Build a real SPL/Token-2022 mint account payload."""
    data = bytearray(82)
    if mint_authority:
        data[0:4] = (1).to_bytes(4, "little")
        data[4:36] = _pubkey32(mint_authority)
    data[36:44] = supply.to_bytes(8, "little")
    data[44] = decimals
    data[45] = 1  # is_initialized
    if freeze_authority:
        data[46:50] = (1).to_bytes(4, "little")
        data[50:82] = _pubkey32(freeze_authority)
    out = bytes(data)
    for ext_type, body in extensions or []:
        # TLV: type u16 LE + len u16 LE + body
        out += ext_type.to_bytes(2, "little") + len(body).to_bytes(2, "little") + body
    return out


def _transfer_fee_config_body(older_bp: int, newer_bp: int) -> bytes:
    """116-byte TransferFeeConfig extension body (2x COption<Pubkey> + u64 + 2x TransferFee)."""
    body = bytearray(116)
    # transfer_fee_config_authority COption=None (option=0 + 32 zero bytes)
    # withdraw_withheld_authority COption=None
    # withheld_amount u64 at [72:80]
    # older TransferFee: epoch u64 [80:88], max_fee u64 [88:96], bp u16 [96:98]
    body[80:88] = (0).to_bytes(8, "little")
    body[88:96] = (1000).to_bytes(8, "little")
    body[96:98] = older_bp.to_bytes(2, "little")
    # newer TransferFee: epoch [98:106], max_fee [106:114], bp [114:116]
    body[98:106] = (1).to_bytes(8, "little")
    body[106:114] = (2000).to_bytes(8, "little")
    body[114:116] = newer_bp.to_bytes(2, "little")
    return bytes(body)


class FakeRouter:
    """Scripted RPC router — maps (method) → response or callable."""

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, list[Any]]] = []

    async def call(self, chain: str, method: str, params=None, timeout=None):
        self.calls.append((method, params or []))
        resp = self.responses.get(method)
        if callable(resp):
            return resp(params or [])
        return resp


def _account_info_response(data: bytes, owner: str = so.SPL_TOKEN_PROGRAM) -> dict[str, Any]:
    return {
        "result": {
            "value": {
                "owner": owner,
                "data": [base64.b64encode(data).decode("ascii"), "base64"],
            }
        }
    }


def _patch_router(monkeypatch: pytest.MonkeyPatch, router: FakeRouter) -> None:
    monkeypatch.setattr(so, "_router", lambda: router)


# ── _parse_mint_layout ───────────────────────────────────────────────────


def test_parse_mint_layout_authorities_present() -> None:
    facts = so._parse_mint_layout(_mint_layout(mint_authority=WALLET_A, freeze_authority=WALLET_B))
    assert facts["mint_authority"] == WALLET_A
    assert facts["freeze_authority"] == WALLET_B
    assert facts["decimals"] == 6
    assert facts["supply_raw"] == 1_000_000_000


def test_parse_mint_layout_renounced() -> None:
    facts = so._parse_mint_layout(_mint_layout(mint_authority=None))
    assert facts["mint_authority"] is None
    assert facts["freeze_authority"] is None


def test_parse_mint_layout_transfer_fee_extension() -> None:
    data = _mint_layout(
        mint_authority=None,
        extensions=[(1, _transfer_fee_config_body(older_bp=100, newer_bp=250))],
    )
    facts = so._parse_mint_layout(data)
    assert facts.get("transfer_fee_pct") == 2.5  # worst-case of 1% / 2.5%
    assert "transfer_fee" in facts.get("extensions", [])


def test_parse_mint_layout_too_short_is_empty() -> None:
    assert so._parse_mint_layout(b"\x00" * 10) == {}


# ── resolve_mint_facts ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_mint_facts_spl_renounced(monkeypatch: pytest.MonkeyPatch) -> None:
    router = FakeRouter(
        {"getAccountInfo": _account_info_response(_mint_layout(mint_authority=None))}
    )
    _patch_router(monkeypatch, router)
    facts = await so.resolve_mint_facts(WALLET_C)
    assert facts is not None
    assert facts["token_program"] == "spl"
    assert facts["mint_authority"] is None


@pytest.mark.asyncio
async def test_resolve_mint_facts_token2022_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    router = FakeRouter(
        {
            "getAccountInfo": _account_info_response(
                _mint_layout(mint_authority=WALLET_A), owner=so.TOKEN_2022_PROGRAM
            )
        }
    )
    _patch_router(monkeypatch, router)
    facts = await so.resolve_mint_facts(WALLET_C)
    assert facts is not None
    assert facts["token_program"] == "token-2022"
    assert facts["mint_authority"] == WALLET_A


@pytest.mark.asyncio
async def test_resolve_mint_facts_none_when_no_account(monkeypatch: pytest.MonkeyPatch) -> None:
    router = FakeRouter({"getAccountInfo": {"result": {"value": None}}})
    _patch_router(monkeypatch, router)
    assert await so.resolve_mint_facts(WALLET_C) is None


@pytest.mark.asyncio
async def test_resolve_mint_facts_none_when_rpc_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_router(monkeypatch, FakeRouter({}))
    assert await so.resolve_mint_facts(WALLET_C) is None


# ── resolve_holder_stats ─────────────────────────────────────────────────


def _largest_accounts(pcts: list[float], supply: float = 100.0) -> dict[str, Any]:
    return {
        "result": {
            "value": [
                {
                    "address": f"Acc{i}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                    "uiAmount": supply * p / 100,
                }
                for i, p in enumerate(pcts)
            ]
        }
    }


@pytest.mark.asyncio
async def test_holder_stats_top10_math(monkeypatch: pytest.MonkeyPatch) -> None:
    pcts = [20.0, 15.0, 10.0, 5.0, 5.0, 3.0, 2.0, 2.0, 1.0, 1.0] + [0.5] * 10
    router = FakeRouter(
        {
            "getTokenLargestAccounts": _largest_accounts(pcts),
            "getTokenSupply": {"result": {"value": {"uiAmount": 100.0}}},
        }
    )
    _patch_router(monkeypatch, router)
    stats = await so.resolve_holder_stats(WALLET_C)
    assert stats is not None
    assert stats["top_10_pct"] == pytest.approx(64.0)
    assert stats["top_20_pct"] == pytest.approx(69.0)
    assert stats["source"] == "rpc:getTokenLargestAccounts"
    assert len(stats["top_holders"]) == 20


@pytest.mark.asyncio
async def test_holder_stats_none_without_supply(monkeypatch: pytest.MonkeyPatch) -> None:
    router = FakeRouter(
        {
            "getTokenLargestAccounts": _largest_accounts([50.0]),
            "getTokenSupply": {"result": {"value": {"uiAmount": 0}}},
        }
    )
    _patch_router(monkeypatch, router)
    assert await so.resolve_holder_stats(WALLET_C) is None


@pytest.mark.asyncio
async def test_holder_stats_none_without_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    router = FakeRouter(
        {
            "getTokenLargestAccounts": {"result": {"value": []}},
            "getTokenSupply": {"result": {"value": {"uiAmount": 100.0}}},
        }
    )
    _patch_router(monkeypatch, router)
    assert await so.resolve_holder_stats(WALLET_C) is None


# ── resolve_deployer ─────────────────────────────────────────────────────


def _tx_response(signers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "result": {
            "slot": 123,
            "transaction": {"message": {"accountKeys": signers}},
        }
    }


@pytest.mark.asyncio
async def test_deployer_from_earliest_tx_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    router = FakeRouter(
        {
            "getSignaturesForAddress": {
                "result": [
                    {"signature": "sig_new"},
                    {"signature": "sig_old"},
                ]
            },
            "getTransaction": lambda params: (
                _tx_response(
                    [{"pubkey": WALLET_A, "signer": True}, {"pubkey": WALLET_B, "signer": False}]
                )
                if params[0] == "sig_old"
                else _tx_response([{"pubkey": WALLET_B, "signer": True}])
            ),
        }
    )
    _patch_router(monkeypatch, router)
    deployer = await so.resolve_deployer(WALLET_C)
    assert deployer == WALLET_A  # earliest tx's signer, not the newest


@pytest.mark.asyncio
async def test_deployer_none_without_signatures(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_router(monkeypatch, FakeRouter({"getSignaturesForAddress": {"result": []}}))
    assert await so.resolve_deployer(WALLET_C) is None


# ── detect_same_slot_bundle ──────────────────────────────────────────────


def _buy_tx(slot: int, mint: str, owners: list[str]) -> dict[str, Any]:
    """A transaction where `owners` each gained token balance for `mint`."""
    post = []
    pre = []
    for i, owner in enumerate(owners):
        post.append(
            {
                "accountIndex": i,
                "mint": mint,
                "owner": owner,
                "uiTokenAmount": {"uiAmount": 100.0},
            }
        )
        pre.append(
            {
                "accountIndex": i,
                "mint": mint,
                "owner": owner,
                "uiTokenAmount": {"uiAmount": 0.0},
            }
        )
    return {
        "result": {
            "slot": slot,
            "meta": {"preTokenBalances": pre, "postTokenBalances": post},
            "transaction": {"message": {"accountKeys": []}},
        }
    }


@pytest.mark.asyncio
async def test_same_slot_bundle_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    sigs = [{"signature": f"sig{i}"} for i in range(3)]

    def get_tx(params: list[Any]) -> dict[str, Any]:
        sig = params[0]
        if sig == "sig0":
            return _buy_tx(100, WALLET_C, [WALLET_A, WALLET_B, WALLET_C])
        if sig == "sig1":
            return _buy_tx(101, WALLET_C, [WALLET_A])
        return _buy_tx(102, WALLET_C, [WALLET_B])

    router = FakeRouter({"getSignaturesForAddress": {"result": sigs}, "getTransaction": get_tx})
    _patch_router(monkeypatch, router)
    bundle = await so.detect_same_slot_bundle(WALLET_C)
    assert bundle is not None
    assert bundle["is_bundled"] is True
    assert bundle["max_wallets_one_slot"] == 3
    assert bundle["clusters"][0]["buyer_count"] == 3


@pytest.mark.asyncio
async def test_same_slot_bundle_clean_when_spread(monkeypatch: pytest.MonkeyPatch) -> None:
    sigs = [{"signature": f"sig{i}"} for i in range(2)]
    router = FakeRouter(
        {
            "getSignaturesForAddress": {"result": sigs},
            "getTransaction": lambda params: _buy_tx(
                200 + int(params[0][-1]), WALLET_C, [WALLET_A]
            ),
        }
    )
    _patch_router(monkeypatch, router)
    bundle = await so.detect_same_slot_bundle(WALLET_C)
    assert bundle is not None
    assert bundle["is_bundled"] is False
    assert bundle["clusters"] == []


# ── enrich_scan_solana integration ───────────────────────────────────────


@pytest.mark.asyncio
async def test_enrich_fills_authorities_and_tax(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _mint_layout(
        mint_authority=None,
        extensions=[(1, _transfer_fee_config_body(older_bp=0, newer_bp=500))],
    )

    async def fake_resolve(mint: str) -> dict[str, Any]:
        return {**so._parse_mint_layout(data), "token_program": "token-2022"}

    async def none(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(so, "resolve_mint_facts", fake_resolve)
    monkeypatch.setattr(so, "resolve_holder_stats", none)
    monkeypatch.setattr(so, "resolve_deployer", none)
    monkeypatch.setattr(so, "detect_same_slot_bundle", none)

    result: dict[str, Any] = {"address": WALLET_C, "chain": "solana"}
    out = await so.enrich_scan_solana(result, WALLET_C, "solana")
    assert out["mintable"] is False
    assert out["owner_renounced"] is True
    assert out["freezable"] is False
    assert out["can_blacklist"] is False
    assert out["buy_tax"] == pytest.approx(0.05)
    assert out["sell_tax"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_enrich_classic_spl_tax_defaults_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(mint: str) -> dict[str, Any]:
        return {**so._parse_mint_layout(_mint_layout(mint_authority=None)), "token_program": "spl"}

    async def none(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(so, "resolve_mint_facts", fake_resolve)
    monkeypatch.setattr(so, "resolve_holder_stats", none)
    monkeypatch.setattr(so, "resolve_deployer", none)
    monkeypatch.setattr(so, "detect_same_slot_bundle", none)

    out = await so.enrich_scan_solana({"chain": "solana"}, WALLET_C, "solana")
    assert out["buy_tax"] == 0.0
    assert out["sell_tax"] == 0.0


@pytest.mark.asyncio
async def test_enrich_never_overwrites_provider_deployer(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(mint: str) -> dict[str, Any]:
        return {**so._parse_mint_layout(_mint_layout(mint_authority=None)), "token_program": "spl"}

    async def fake_deployer(mint: str) -> str:
        return WALLET_B

    async def none(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(so, "resolve_mint_facts", fake_resolve)
    monkeypatch.setattr(so, "resolve_holder_stats", none)
    monkeypatch.setattr(so, "resolve_deployer", fake_deployer)
    monkeypatch.setattr(so, "detect_same_slot_bundle", none)

    out = await so.enrich_scan_solana(
        {"chain": "solana", "deployer_address": WALLET_A}, WALLET_C, "solana"
    )
    assert out["deployer_address"] == WALLET_A  # provider value kept


@pytest.mark.asyncio
async def test_enrich_skips_evm_chains() -> None:
    result = {"chain": "eth"}
    out = await so.enrich_scan_solana(result, "0x" + "a" * 40, "eth")
    assert out == result  # untouched


@pytest.mark.asyncio
async def test_enrich_fail_open_on_budget_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: Any, **k: Any) -> None:
        raise TimeoutError()

    monkeypatch.setattr(so, "resolve_mint_facts", boom)
    monkeypatch.setattr(so, "resolve_holder_stats", boom)
    monkeypatch.setattr(so, "resolve_deployer", boom)
    monkeypatch.setattr(so, "detect_same_slot_bundle", boom)

    result = {"chain": "solana", "risk_score": 41}
    out = await so.enrich_scan_solana(result, WALLET_C, "solana")
    assert out["risk_score"] == 41  # unchanged, no exception escaped


# ── _smart_money_token ───────────────────────────────────────────────────


def test_normalize_full_activity_payload() -> None:
    payload = {
        "data": {
            "smart_degen_count": 12,
            "kol_count": 2,
            "buy_pressure": 30.5,
            "total_profit_pnl": 15000.0,
            "activity": [
                {"wallet": WALLET_A, "buy_7d": 10, "sell_7d": 2, "realized_profit": 500.0},
                {"wallet": WALLET_B, "buy_7d": 3, "sell_7d": 9, "realized_profit": -200.0},
            ],
        }
    }
    norm = smt._normalize(payload, WALLET_C, "solana")
    assert norm is not None
    # (10-2) + (3-9) = 2 — real rows win over the aggregate buy_pressure.
    assert norm["net_bought"] == pytest.approx(2.0)
    assert norm["smart_money_count"] == 12
    assert norm["kol_count"] == 2
    wallets = norm["top_smart_money_wallets"]
    assert wallets[0]["direction"] == "BUY"
    assert wallets[1]["direction"] == "SELL"


def test_normalize_cancelled_rows_keep_zero_net() -> None:
    """Rows that genuinely cancel to zero must NOT be replaced by buy_pressure."""
    payload = {
        "data": {
            "buy_pressure": 99.0,
            "activity": [
                {"wallet": WALLET_A, "buy_7d": 5, "sell_7d": 5},
            ],
        }
    }
    norm = smt._normalize(payload, WALLET_C, "solana")
    assert norm is not None
    assert norm["net_bought"] == 0.0


def test_normalize_buy_pressure_fallback_without_rows() -> None:
    payload = {"data": {"smart_degen_count": 4, "buy_pressure": -12.5}}
    norm = smt._normalize(payload, WALLET_C, "solana")
    assert norm is not None
    assert norm["net_bought"] == pytest.approx(-12.5)


def test_normalize_pnl_only_rows_still_prove_presence() -> None:
    payload = {
        "data": {
            "activity": [
                {"wallet_address": WALLET_A, "realized_profit": 900.0},
            ]
        }
    }
    norm = smt._normalize(payload, WALLET_C, "solana")
    assert norm is not None
    assert norm["smart_money_count"] == 1
    assert norm["top_smart_money_wallets"][0]["address"] == WALLET_A


def test_normalize_empty_payload_returns_none() -> None:
    assert smt._normalize({"data": {}}, WALLET_C, "solana") is None
    assert smt._normalize({}, WALLET_C, "solana") is None


def test_normalize_malformed_activity_types() -> None:
    payload = {"data": {"activity": ["nope", 42, None, {"wallet": WALLET_A}]}}
    norm = smt._normalize(payload, WALLET_C, "solana")
    # Row without counts/pnl contributes nothing but must not crash.
    assert norm is not None or norm is None  # no exception is the contract


@pytest.mark.asyncio
async def test_enrich_smart_money_merges_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(address: str, chain: str) -> dict[str, Any]:
        return {
            "net_bought": 42.0,
            "smart_money_count": 5,
            "kol_count": 1,
            "buy_pressure": None,
            "total_profit_pnl": None,
            "top_smart_money_wallets": [],
            "source": "gmgn_token_scoped",
        }

    monkeypatch.setattr(smt, "fetch_token_smart_money", fake_fetch)
    result: dict[str, Any] = {"gmgn": {"holders": {"holder_count": 10}}}
    out = await smt.enrich_scan_smart_money(result, WALLET_C, "solana")
    assert out["gmgn"]["smart_money"]["net_bought"] == 42.0
    assert out["gmgn"]["holders"]["holder_count"] == 10  # holders preserved


@pytest.mark.asyncio
async def test_enrich_smart_money_skips_already_enriched(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*a: Any, **k: Any) -> None:
        raise AssertionError("must not be called")

    monkeypatch.setattr(smt, "fetch_token_smart_money", fail)
    result = {"gmgn": {"smart_money": {"source": "gmgn_token_scoped", "net_bought": 1}}}
    out = await smt.enrich_scan_smart_money(result, WALLET_C, "solana")
    assert out["gmgn"]["smart_money"]["net_bought"] == 1


@pytest.mark.asyncio
async def test_enrich_smart_money_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(smt, "fetch_token_smart_money", none)
    result = {"risk_score": 10}
    out = await smt.enrich_scan_smart_money(result, WALLET_C, "solana")
    assert out == result

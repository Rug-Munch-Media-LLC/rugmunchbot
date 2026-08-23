"""Test wave 29 — scan-family command handlers (_scan_handlers/_extras.py).

Behavior probed via .venv/bin/python before asserting (2026-08-23).
FakeUpdate/FakeCtx pattern from tests/test_portfolio_command.py; transports
and scanner seams monkeypatched at the _extras module namespace (top-level
imports).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from rugmunchbot.handlers._scan_handlers import _extras as ex

# ── Telegram fakes (duck-typed, per portfolio_command pattern) ──


class FakeUser:
    def __init__(self, uid: int = 42) -> None:
        self.id = uid


class FakeSent:
    """Object returned by reply_text(); captures edit_text calls."""

    def __init__(self) -> None:
        self.edits: list[str] = []
        self.edit_kwargs: list[dict[str, Any]] = []

    async def edit_text(self, text: str, **kw: Any) -> None:
        self.edits.append(text)
        self.edit_kwargs.append(kw)


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.reply_kwargs: list[dict[str, Any]] = []
        self.sent: FakeSent | None = None

    async def reply_text(self, text: str, **kw: Any) -> FakeSent:
        self.replies.append(text)
        self.reply_kwargs.append(kw)
        self.sent = FakeSent()
        return self.sent


class FakeUpdate:
    def __init__(self, args: list[str], uid: int | None = 42) -> None:
        self.message = FakeMessage()
        self.effective_user = FakeUser(uid) if uid is not None else None


class FakeCtx:
    def __init__(self, args: list[str]) -> None:
        self.args = args


def _pair(**over: Any) -> dict[str, Any]:
    p = {
        "baseToken": {"address": "0x" + "cd" * 20, "name": "Pepe", "symbol": "PEPE"},
        "chainId": "ethereum",
        "priceUsd": "0.0000123",
        "priceChange": {"h24": 10.0, "h1": 5.0},
        "volume": {"h24": 123456.0},
        "marketCap": 999999.0,
        "liquidity": {"usd": 50000.0},
    }
    p.update(over)
    return p


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(ex, "is_owner", lambda uid: True)
    calls: dict[str, int] = {"n": 0}
    monkeypatch.setattr(
        ex.db, "increment_usage", lambda uid, scan=False: calls.__setitem__("n", calls["n"] + 1)
    )
    return calls


# ── cmd_quick ──


@pytest.mark.asyncio
async def test_cmd_quick_no_args_shows_usage() -> None:
    u = FakeUpdate([])
    await ex.cmd_quick(u, FakeCtx([]))
    assert len(u.message.replies) == 1
    assert "/quick PEPE" in u.message.replies[0]


@pytest.mark.asyncio
async def test_cmd_quick_hit_renders_price_card_with_keyboard(monkeypatch, owner) -> None:
    async def _stub(url: str, timeout: float = 10) -> Any:
        return {"pairs": [_pair()]}

    monkeypatch.setattr(ex, "http_get_json", _stub)
    u = FakeUpdate(["pepe"])
    await ex.cmd_quick(u, FakeCtx(["pepe"]))
    assert len(u.message.replies) == 1
    text = u.message.replies[0]
    assert "<b>Pepe</b> (PEPE)" in text
    assert "MCap:" in text
    assert "Vol:" in text
    kb = u.message.reply_kwargs[0]["reply_markup"]
    assert kb is not None
    assert f"scan_{_pair()['baseToken']['address']}_ethereum" in str(kb)


@pytest.mark.asyncio
async def test_cmd_quick_lookup_failure_shows_not_found(monkeypatch, owner) -> None:
    async def _stub(url: str, timeout: float = 10) -> Any:
        return None

    monkeypatch.setattr(ex, "http_get_json", _stub)
    u = FakeUpdate(["zzz"])
    await ex.cmd_quick(u, FakeCtx(["zzz"]))
    assert len(u.message.replies) == 1
    assert "Could not find token: <b>ZZZ</b>" in u.message.replies[0]


@pytest.mark.asyncio
async def test_cmd_quick_body_without_pairs_also_not_found(monkeypatch, owner) -> None:
    # Truthy body but zero pairs falls through to the same not-found card.
    async def _stub(url: str, timeout: float = 10) -> Any:
        return {"pairs": []}

    monkeypatch.setattr(ex, "http_get_json", _stub)
    u = FakeUpdate(["zzz"])
    await ex.cmd_quick(u, FakeCtx(["zzz"]))
    assert "Could not find token" in u.message.replies[0]


# ── cmd_deployer ──


@pytest.mark.asyncio
async def test_cmd_deployer_no_user_silent_return() -> None:
    u = FakeUpdate(["0xabc"], uid=None)
    await ex.cmd_deployer(u, FakeCtx(["0xabc"]))
    assert u.message.replies == []


@pytest.mark.asyncio
async def test_cmd_deployer_no_args_shows_usage() -> None:
    u = FakeUpdate([])
    await ex.cmd_deployer(u, FakeCtx([]))
    assert len(u.message.replies) == 1
    assert "Deployer Fingerprint" in u.message.replies[0]
    assert "/deployer" in u.message.replies[0]


@pytest.mark.asyncio
async def test_cmd_deployer_rate_limited_non_owner_gets_paywall(monkeypatch) -> None:
    monkeypatch.setattr(ex, "is_owner", lambda uid: False)
    monkeypatch.setattr(ex.db, "check_rate_limit", lambda uid, kind: (False, 0, 0))
    monkeypatch.setattr(ex.db, "get_user_tier", lambda uid: "free")
    u = FakeUpdate(["0xabc"], uid=7)
    await ex.cmd_deployer(u, FakeCtx(["0xabc"]))
    assert len(u.message.replies) == 1
    assert u.message.reply_kwargs[0]["reply_markup"] is not None  # upgrade nudge kb


@pytest.mark.asyncio
async def test_cmd_deployer_happy_path_full_render(monkeypatch, owner) -> None:
    data = {
        "safety_score": 42,
        "risk_level": "medium",
        "evidence": {
            "deployer": {
                "risk_score": 75,
                "risk_label": "HIGH",
                "deployer_address": "0xdev",
                "rug_count": 2,
                "total_deployments": 9,
            },
            "holders": {"top_10_pct": 55, "holder_count": 1200},
            "liquidity": {"locked_pct": 88.4},
            "security": {
                "checks": {
                    "a": {"status": "fail"},
                    "b": {"status": "pass"},
                    "c": {"status": "fail"},
                }
            },
        },
    }

    async def _scan(target: str, chain: str | None) -> Any:
        return data

    async def _fp(addr: str, chain: str) -> Any:
        return {
            "risk_score": 80,
            "risk_bucket": "serial_launcher",
            "success_rate": 0.666,
            "launchpad": "pump.fun",
            "median_lifespan_hours": 3.7,
        }

    monkeypatch.setattr(ex, "scan_token_v2", _scan)
    monkeypatch.setattr(ex, "fetch_deployer_fingerprint", _fp)
    u = FakeUpdate(["0xtok"])
    await ex.cmd_deployer(u, FakeCtx(["0xtok"]))
    # Progress message first, then the final card lands via edit_text.
    assert "Checking deployer fingerprint..." in u.message.replies[0]
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "<code>0xdev</code>" in out
    assert "\U0001f534 Dev Risk Score: <b>80/100</b>" in out  # fp score wins over evidence 75
    assert "Risk Bucket: <b>serial_launcher</b>" in out
    assert "Total Deployments: <b>9</b>" in out
    assert "Success Rate: <b>67%</b>" in out
    assert "Known Launchpad: <b>pump.fun</b>" in out
    assert "Rug Count: <b>2</b>" in out
    assert "Median Lifespan: <b>4h</b>" in out  # 3.7 rounded
    assert "Top-10 Holders: <b>55%</b> | Holders: 1200" in out
    assert "LP Locked: <b>88%</b>" in out
    assert "Failed Security Checks: <b>2</b>" in out
    assert owner["n"] == 1


@pytest.mark.asyncio
async def test_cmd_deployer_scanner_down_failure_card(monkeypatch, owner) -> None:
    async def _scan(target: str, chain: str | None) -> Any:
        return None

    monkeypatch.setattr(ex, "scan_token_v2", _scan)
    u = FakeUpdate(["0xtok"])
    await ex.cmd_deployer(u, FakeCtx(["0xtok"]))
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "Deployer Check Failed" in out
    assert "scanner is unavailable" in out


@pytest.mark.asyncio
async def test_cmd_deployer_no_dev_data_card(monkeypatch, owner) -> None:
    async def _scan(target: str, chain: str | None) -> Any:
        return {"safety_score": 61, "risk_level": "low", "evidence": {}}

    monkeypatch.setattr(ex, "scan_token_v2", _scan)
    u = FakeUpdate(["0xtok"])
    await ex.cmd_deployer(u, FakeCtx(["0xtok"]))
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "No Deployer Data" in out
    assert "Safety: 61/100" in out
    assert "Risk level: low" in out


@pytest.mark.asyncio
async def test_cmd_deployer_http_error_failure_card(monkeypatch, owner) -> None:
    async def _scan(target: str, chain: str | None) -> Any:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(ex, "scan_token_v2", _scan)
    u = FakeUpdate(["0xtok"])
    await ex.cmd_deployer(u, FakeCtx(["0xtok"]))
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "Deployer Check Failed" in out
    assert "unexpected error" in out


# ── cmd_hype ──


class FakeResp:
    def __init__(self, status_code: int = 200, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeClient:
    """Duck-typed httpx.AsyncClient: routes URL suffixes to canned payloads."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    async def get(self, url: str) -> FakeResp:
        self.calls.append(url)
        for suffix, resp in self.routes.items():
            if url.endswith(suffix):
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return FakeResp(404, None)


@pytest.mark.asyncio
async def test_cmd_hype_no_user_silent_return() -> None:
    u = FakeUpdate(["PEPE"], uid=None)
    await ex.cmd_hype(u, FakeCtx(["PEPE"]))
    assert u.message.replies == []


@pytest.mark.asyncio
async def test_cmd_hype_no_args_shows_usage() -> None:
    u = FakeUpdate([])
    await ex.cmd_hype(u, FakeCtx([]))
    assert len(u.message.replies) == 1
    assert "Hype Score" in u.message.replies[0]
    assert "/hype" in u.message.replies[0]


@pytest.mark.asyncio
async def test_cmd_hype_c5_backend_path_full_render(monkeypatch, owner) -> None:
    client = FakeClient(
        {
            "/tokens/0x" + "cd" * 20: FakeResp(200, {"pairs": [_pair()]}),
            "/token-boosts/top/v1": FakeResp(200, []),
        }
    )
    monkeypatch.setattr(ex, "get_shared_client", lambda t: client)

    async def _hype(addr: str, chain: str) -> Any:
        return {
            "hype_score": 82,
            "signals": {
                "social_velocity": {"score": 90, "detail": "fast socials"},
                "volume_growth": {"score": 70},
                "holder_growth": {"score": 60},
                "price_momentum": {"score": 50},
            },
            "inactivity_hours": 5.2,
            "decay_factor": 0.8,
        }

    monkeypatch.setattr(ex, "fetch_hype_score", _hype)
    u = FakeUpdate(["0x" + "cd" * 20])
    await ex.cmd_hype(u, FakeCtx(["0x" + "cd" * 20]))
    assert "Measuring hype..." in u.message.replies[0]
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "\U0001f525 <b>Hype Score: 82/100</b>" in out  # >=70 fire emoji
    assert "Social Velocity: <b>90/100</b>" in out
    assert "Volume Growth: <b>70/100</b>" in out
    assert "Holder Growth: <b>60/100</b>" in out
    assert "Price Momentum: <b>50/100</b>" in out
    assert "Inactivity: 5h (decay x0.8)" in out
    assert "fast socials" in out  # detail strings joined
    assert owner["n"] == 1


@pytest.mark.asyncio
async def test_cmd_hype_local_fallback_math(monkeypatch, owner) -> None:
    created_ms = int((time.time() - 2 * 3600) * 1000)  # 2h old
    pair = _pair(
        priceChange={"h24": 40.0, "h1": 25.0},
        volume={"h24": 150000.0},
        liquidity={"usd": 200000.0},
        pairCreatedAt=created_ms,
    )
    client = FakeClient(
        {
            "/tokens/0x" + "cd" * 20: FakeResp(200, {"pairs": [pair]}),
            "/token-boosts/top/v1": FakeResp(200, []),
        }
    )
    monkeypatch.setattr(ex, "get_shared_client", lambda t: client)

    async def _hype(addr: str, chain: str) -> Any:
        return None  # backend unavailable -> local computation

    monkeypatch.setattr(ex, "fetch_hype_score", _hype)
    u = FakeUpdate(["0x" + "cd" * 20])
    await ex.cmd_hype(u, FakeCtx(["0x" + "cd" * 20]))
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    # boosts 0 -> 0; vol 150k -> 14; momentum h1>20&h24>30 -> 20; age<24h -> 10; liq>=100k -> 10 => 54
    assert "\U0001f7e1 <b>Hype Score: 54/100</b>" in out
    assert "Boosts: 0 (x8, max 40) -> 0" in out
    assert "-> 14/20" in out
    assert "-> 20/20" in out
    assert "-> 10/10" in out.splitlines()[8] or "-> 10/10" in out  # age + liquidity lines exist


@pytest.mark.asyncio
async def test_cmd_hype_symbol_resolved_via_dexscreener_search(monkeypatch, owner) -> None:
    addr = "0x" + "cd" * 20
    client = FakeClient(
        {
            "/search/?q=PEPE": FakeResp(200, {"pairs": [_pair()]}),
            f"/tokens/{addr}": FakeResp(200, {"pairs": [_pair()]}),
            "/token-boosts/top/v1": FakeResp(200, []),
        }
    )
    monkeypatch.setattr(ex, "get_shared_client", lambda t: client)

    async def _hype(a: str, c: str) -> Any:
        assert a == addr  # resolved from search, not the raw symbol
        return {"hype_score": 10, "signals": {}}

    monkeypatch.setattr(ex, "fetch_hype_score", _hype)
    u = FakeUpdate(["PEPE"])
    await ex.cmd_hype(u, FakeCtx(["PEPE"]))
    assert any("/search/?q=PEPE" in c for c in client.calls)


@pytest.mark.asyncio
async def test_cmd_hype_tokens_endpoint_404_not_found(monkeypatch, owner) -> None:
    client = FakeClient({"/token-boosts/top/v1": FakeResp(200, [])})
    monkeypatch.setattr(ex, "get_shared_client", lambda t: client)

    async def _hype(a: str, c: str) -> Any:
        return None

    monkeypatch.setattr(ex, "fetch_hype_score", _hype)
    u = FakeUpdate(["0x" + "cd" * 20])
    await ex.cmd_hype(u, FakeCtx(["0x" + "cd" * 20]))
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "Token not found" in out


@pytest.mark.asyncio
async def test_cmd_hype_no_pairs_message(monkeypatch, owner) -> None:
    client = FakeClient(
        {
            "/tokens/0x" + "cd" * 20: FakeResp(200, {"pairs": []}),
            "/token-boosts/top/v1": FakeResp(200, []),
        }
    )
    monkeypatch.setattr(ex, "get_shared_client", lambda t: client)

    async def _hype(a: str, c: str) -> Any:
        return None

    monkeypatch.setattr(ex, "fetch_hype_score", _hype)
    u = FakeUpdate(["0x" + "cd" * 20])
    await ex.cmd_hype(u, FakeCtx(["0x" + "cd" * 20]))
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "No trading pairs found" in out


@pytest.mark.asyncio
async def test_cmd_hype_unexpected_error_failure_card(monkeypatch, owner) -> None:
    client = FakeClient(
        {
            "/tokens/0x" + "cd" * 20: FakeResp(200, {"pairs": [_pair()]}),
            "/token-boosts/top/v1": FakeResp(200, []),
        }
    )
    monkeypatch.setattr(ex, "get_shared_client", lambda t: client)

    async def _hype(a: str, c: str) -> Any:
        raise ValueError("bad payload")

    monkeypatch.setattr(ex, "fetch_hype_score", _hype)
    u = FakeUpdate(["0x" + "cd" * 20])
    await ex.cmd_hype(u, FakeCtx(["0x" + "cd" * 20]))
    out = u.message.sent.edits[-1]  # type: ignore[union-attr]
    assert "Hype Check Failed" in out

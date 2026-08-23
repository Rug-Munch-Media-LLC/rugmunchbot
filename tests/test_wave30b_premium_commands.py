"""Test wave 30b — /mev /impersonation /deathclock /launches /cluster commands.

Behavior probed via .venv/bin/python before asserting (2026-08-23).
Backend seams (http_get_json/http_post_json) monkeypatched at the premium
module namespace; Telegram fakes duck-typed per wave29/portfolio pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.commands import premium as pm


class FakeChat:
    def __init__(self) -> None:
        self.actions: list[str] = []

    async def send_action(self, action: str) -> None:
        self.actions.append(action)


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.chat = FakeChat()

    async def reply_text(self, text: str, **kw: Any) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, args: list[str]) -> None:
        self.message = FakeMessage()


class FakeCtx:
    def __init__(self, args: list[str]) -> None:
        self.args = args


def _stub_backend(monkeypatch, payload: Any = None, post_payload: Any = None) -> dict[str, int]:
    calls = {"get": 0, "post": 0}

    async def _get(path: str, params=None, headers=None, timeout: float = 15.0):
        calls["get"] += 1
        return payload

    async def _post(path: str, json=None, headers=None, timeout: float = 20.0):
        calls["post"] += 1
        return post_payload

    monkeypatch.setattr(pm, "http_get_json", _get)
    monkeypatch.setattr(pm, "http_post_json", _post)
    return calls


# ── helpers ──


def test_fmt_pct_none_and_digits() -> None:
    assert pm._fmt_pct(None) == "N/A"
    assert pm._fmt_pct(12.34) == "12.3%"
    assert pm._fmt_pct(12.34, digits=2) == "12.34%"


def test_fmt_usd_bands() -> None:
    assert pm._fmt_usd(None) == "N/A"
    assert pm._fmt_usd(2_500_000) == "$2.5M"
    assert pm._fmt_usd(15_400) == "$15.4K"
    assert pm._fmt_usd(123.5) == "$123.50"


# ── /mev ──


@pytest.mark.asyncio
async def test_mev_no_args_shows_usage() -> None:
    u = FakeUpdate([])
    await pm.cmd_mev(u, FakeCtx([]))
    assert len(u.message.replies) == 1
    assert "/mev" in u.message.replies[0]


@pytest.mark.asyncio
async def test_mev_backend_down_unavailable_card(monkeypatch) -> None:
    _stub_backend(monkeypatch, payload=None)
    u = FakeUpdate(["0xabc"])
    await pm.cmd_mev(u, FakeCtx(["0xabc"]))
    assert any("MEV advisory unavailable" in r for r in u.message.replies)
    assert u.message.chat.actions == ["typing"]


@pytest.mark.asyncio
async def test_mev_happy_path_score_bands_and_swap_params(monkeypatch) -> None:
    _stub_backend(
        monkeypatch,
        payload={
            "risk_level": "high",
            "risk_score": 82,
            "sandwich_risk": 0.44,
            "frontrun_risk": 0.61,
            "safe_swap": {
                "slippage_bps": 150,
                "gas_price_gwei": 42,
                "use_mev_blocker": True,
                "deadline_min": 3,
            },
            "notes": "High traffic pool.",
        },
    )
    u = FakeUpdate(["0xabc", "base"])
    await pm.cmd_mev(u, FakeCtx(["0xabc", "base"]))
    out = u.message.replies[0]
    # QUIRK: backend sends sandwich/frontrun as 0-1 fractions but _fmt_pct
    # renders them as literal percentages (0.44 -> "0.4%"). Asserting probed
    # reality; flagged in SESSION_TODO as a suspected display bug.
    assert "\U0001f534 <b>Risk:</b> High (82/100)" in out
    assert "Sandwich Attack Risk:</b> 0.4%" in out
    assert "Slippage: 150 bps (1.5%)" in out
    assert "Gas: 42 gwei" in out
    assert "MEV-blocker routing: recommended" in out
    assert "Deadline: 3 min" in out
    assert "High traffic pool." in out
    assert "(Base)" in out


@pytest.mark.asyncio
async def test_mev_low_score_green_no_optional_sections(monkeypatch) -> None:
    _stub_backend(monkeypatch, payload={"risk_level": "low", "risk_score": 10})
    u = FakeUpdate(["0xabc"])
    await pm.cmd_mev(u, FakeCtx(["0xabc"]))
    out = u.message.replies[0]
    assert "\U0001f7e2 <b>Risk:</b> Low (10/100)" in out
    assert "Sandwich" not in out
    assert "Safe Swap" not in out


# ── /impersonation ──


@pytest.mark.asyncio
async def test_impersonation_full_render(monkeypatch) -> None:
    _stub_backend(
        monkeypatch,
        payload={
            "risk_level": "critical",
            "risk_score": 91,
            "delegates": [{"address": "0xd1", "reason": "honeypot delegate"}],
            "approvals": [{"spender": "0xs1", "token": "FAKE"}],
            "related_contracts": [{"address": "0xr1", "type": "previous rug"}],
            "verdict": "Likely impersonation of PEPE.",
        },
    )
    u = FakeUpdate(["0xabc"])
    await pm.cmd_impersonation(u, FakeCtx(["0xabc"]))
    out = u.message.replies[0]
    assert "\U0001f534 <b>Impersonation Risk:</b> Critical (91/100)" in out
    assert "Suspicious Delegates (1)" in out
    assert "<code>0xd1</code>" in out
    assert "Dangerous Approvals (1)" in out
    assert "FAKE \u2192 <code>0xs1</code>" in out
    assert "Related Contracts (1)" in out
    assert "<b>Verdict:</b> Likely impersonation of PEPE." in out


@pytest.mark.asyncio
async def test_impersonation_backend_down(monkeypatch) -> None:
    _stub_backend(monkeypatch, payload=None)
    u = FakeUpdate(["0xabc"])
    await pm.cmd_impersonation(u, FakeCtx(["0xabc"]))
    assert any("Impersonation audit unavailable" in r for r in u.message.replies)


@pytest.mark.asyncio
async def test_impersonation_no_args_usage() -> None:
    u = FakeUpdate([])
    await pm.cmd_impersonation(u, FakeCtx([]))
    assert "Impersonation Detector" in u.message.replies[0]


# ── /deathclock ──


@pytest.mark.asyncio
async def test_deathclock_days_bands(monkeypatch) -> None:
    cases = [
        (None, "\u2753", "Unknown"),
        (3, "\U0001f534", "3 days"),
        (20, "\U0001f7e1", "20 days"),
        (60, "\U0001f7e2", "60 days"),
        (120, "\U0001f7e2", "120 days (4.0 months)"),
    ]
    for days, emoji, expected in cases:
        payload = {"days_remaining": days, "risk_level": "medium"}
        _stub_backend(monkeypatch, payload=payload)
        u = FakeUpdate(["solana", "0xabc"])
        await pm.cmd_deathclock(u, FakeCtx(["solana", "0xabc"]))
        out = u.message.replies[0]
        assert f"{emoji} <b>Estimated Survival:</b> {expected}" in out, f"days={days}"


@pytest.mark.asyncio
async def test_deathclock_factors_and_probability(monkeypatch) -> None:
    _stub_backend(
        monkeypatch,
        payload={
            "days_remaining": 45,
            "risk_level": "high",
            "survival_probability": 0.234,
            "factors": {"liquidity_depth": 0.85, "holder_growth": 0.2},
            "explanation": "Thin LP.",
        },
    )
    u = FakeUpdate(["ethereum", "0xabc"])
    await pm.cmd_deathclock(u, FakeCtx(["ethereum", "0xabc"]))
    out = u.message.replies[0]
    # Same fraction-as-percent quirk: 0.234 renders "0.2%".
    assert "Survival Probability:</b> 0.2%" in out
    assert "\U0001f534 Liquidity Depth: 85%" in out  # >=0.7 red bar
    assert "\U0001f7e2 Holder Growth: 20%" in out
    assert "Thin LP." in out


@pytest.mark.asyncio
async def test_deathclock_single_arg_doubles_as_address(monkeypatch) -> None:
    seen: dict[str, str] = {}

    async def _get(path: str, params=None, headers=None, timeout: float = 15.0):
        seen["path"] = path
        return {"days_remaining": 10, "risk_level": "low"}

    monkeypatch.setattr(pm, "http_get_json", _get)
    u = FakeUpdate(["0xdeadbeef"])
    await pm.cmd_deathclock(u, FakeCtx(["0xdeadbeef"]))
    # args[0] becomes BOTH chain and address (probed behavior).
    assert "/death-clock/predict/0xdeadbeef/0xdeadbeef" in seen["path"]


# ── /launches ──


@pytest.mark.asyncio
async def test_launches_empty_feed(monkeypatch) -> None:
    _stub_backend(monkeypatch, payload={"launches": []})
    u = FakeUpdate([])
    await pm.cmd_launches(u, FakeCtx([]))
    out = u.message.replies[0]
    assert "No new launches detected" in out
    assert "last 24h" in out


@pytest.mark.asyncio
async def test_launches_feed_renders_entries_with_age_and_risk(monkeypatch) -> None:
    _stub_backend(
        monkeypatch,
        payload={
            "launches": [
                {
                    "name": "Pepe",
                    "symbol": "PEPE",
                    "address": "0xp",
                    "chain": "eth",
                    "risk_level": "high",
                    "age_hours": 2.5,
                    "liquidity_usd": 45_000,
                    "holders": 120,
                },
                {
                    "name": "Old",
                    "symbol": "OLD",
                    "address": "0xo",
                    "chain": "sol",
                    "risk_level": "low",
                    "age_hours": 50,
                },
            ]
        },
    )
    u = FakeUpdate(["base"])
    await pm.cmd_launches(u, FakeCtx(["base"]))
    out = u.message.replies[0]
    assert "New Token Launches \u2014 Base" in out
    assert "Showing 2 recent launches" in out
    assert "\U0001f534 <b>Pepe</b> (PEPE)" in out
    assert "Age: 2.5h ago" in out
    assert "Liquidity: $45.0K" in out
    assert "Holders: 120" in out
    assert "\U0001f7e2 <b>Old</b> (OLD)" in out
    assert "Age: 2.1d ago" in out  # 50h -> days


@pytest.mark.asyncio
async def test_launches_backend_down(monkeypatch) -> None:
    _stub_backend(monkeypatch, payload=None)
    u = FakeUpdate([])
    await pm.cmd_launches(u, FakeCtx([]))
    assert any("Launch feed unavailable" in r for r in u.message.replies)


# ── /cluster ──


@pytest.mark.asyncio
async def test_cluster_no_args_usage() -> None:
    u = FakeUpdate([])
    await pm.cmd_cluster(u, FakeCtx([]))
    assert "Wallet Clustering" in u.message.replies[0]


@pytest.mark.asyncio
async def test_cluster_clean_wallet(monkeypatch) -> None:
    calls = _stub_backend(monkeypatch, post_payload={"clusters": [], "funding_path": {}})
    u = FakeUpdate(["0xw"])
    await pm.cmd_cluster(u, FakeCtx(["0xw"]))
    out = u.message.replies[0]
    assert calls["post"] == 1
    assert "No suspicious clusters detected." in out
    assert "Funding Path" not in out


@pytest.mark.asyncio
async def test_cluster_clusters_and_funding_path(monkeypatch) -> None:
    _stub_backend(
        monkeypatch,
        post_payload={
            "clusters": [
                {
                    "cluster_id": "c1",
                    "size": 4,
                    "type": "bundle",
                    "confidence": 0.92,
                    "wallets": ["0xa", "0xb"],
                }
            ],
            "funding_path": {
                "source": "Binance hot wallet",
                "chain": "ethereum",
                "amount_usd": 75_000,
                "hops": [{"description": "CEX withdraw"}, {"description": "split"}],
            },
        },
    )
    u = FakeUpdate(["0xw"])
    await pm.cmd_cluster(u, FakeCtx(["0xw"]))
    out = u.message.replies[0]
    assert "Detected Clusters (1)" in out
    assert "Cluster c1: 4 wallets (bundle, 92% confidence)" in out
    assert "\u2514 <code>0xa</code>" in out
    assert "Source: Binance hot wallet" in out
    assert "Amount: $75.0K" in out
    assert "Hops: 2" in out
    assert "\u2192 CEX withdraw" in out


@pytest.mark.asyncio
async def test_cluster_backend_down(monkeypatch) -> None:
    _stub_backend(monkeypatch, post_payload=None)
    u = FakeUpdate(["0xw"])
    await pm.cmd_cluster(u, FakeCtx(["0xw"]))
    assert any("Clustering unavailable" in r for r in u.message.replies)
